"""Jira / Linear Integration for OpsLens.

Features:
- Auto-create follow-up tickets from postmortem action items
- Link remediation tasks to engineering sprints
- Track fix deployment status back into the incident timeline
- Support both Jira Cloud and Linear APIs
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone
from typing import Any

import httpx
import structlog

from src.incidents.models import Incident

logger = structlog.get_logger()


class JiraIntegration:
    """Jira Cloud integration for ticket management."""

    def __init__(
        self,
        base_url: str = "",
        email: str = "",
        api_token: str = "",
        project_key: str = "",
        default_issue_type: str = "Task",
    ):
        self.base_url = base_url.rstrip("/") if base_url else ""
        self.email = email
        self.api_token = api_token
        self.project_key = project_key
        self.default_issue_type = default_issue_type
        self._enabled = bool(base_url and email and api_token)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def _headers(self) -> dict[str, str]:
        creds = base64.b64encode(f"{self.email}:{self.api_token}".encode()).decode()
        return {
            "Authorization": f"Basic {creds}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def _request(
        self, method: str, path: str, json: dict | None = None
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.request(
                method,
                f"{self.base_url}/rest/api/3{path}",
                headers=self._headers(),
                json=json,
            )
            resp.raise_for_status()
            return resp.json() if resp.content else {}

    # --- Ticket Creation ---

    async def create_ticket(
        self,
        summary: str,
        description: str,
        issue_type: str = "",
        priority: str = "Medium",
        labels: list[str] | None = None,
        incident_id: str = "",
        assignee_email: str = "",
        sprint_id: int | None = None,
    ) -> dict[str, Any]:
        """Create a Jira ticket.

        Args:
            summary: Ticket title
            description: Ticket description (ADF or plain text)
            issue_type: Issue type (Task, Bug, Story, etc.)
            priority: Priority name (Highest, High, Medium, Low, Lowest)
            labels: List of labels
            incident_id: OpsLens incident ID for linking
            assignee_email: Assignee email (for Jira Cloud)
            sprint_id: Sprint ID to add the ticket to
        """
        if not self._enabled:
            return {"error": "Jira integration not configured"}

        try:
            issue_type = issue_type or self.default_issue_type
            all_labels = list(labels or [])
            all_labels.append("opslens")
            if incident_id:
                all_labels.append(f"incident-{incident_id.lower()}")

            # Build Atlassian Document Format (ADF) description
            adf_description = {
                "version": 1,
                "type": "doc",
                "content": [
                    {
                        "type": "paragraph",
                        "content": [
                            {"type": "text", "text": description}
                        ],
                    }
                ],
            }

            if incident_id:
                adf_description["content"].append(
                    {
                        "type": "paragraph",
                        "content": [
                            {"type": "text", "text": f"OpsLens Incident: {incident_id}", "marks": [{"type": "strong"}]},
                        ],
                    }
                )

            fields: dict[str, Any] = {
                "project": {"key": self.project_key},
                "summary": summary,
                "description": adf_description,
                "issuetype": {"name": issue_type},
                "priority": {"name": priority},
                "labels": all_labels,
            }

            if assignee_email:
                # Look up user by email
                try:
                    users = await self._request(
                        "GET", f"/user/search?query={assignee_email}"
                    )
                    if users:
                        fields["assignee"] = {"accountId": users[0]["accountId"]}
                except Exception:
                    pass

            result = await self._request("POST", "/issue", json={"fields": fields})

            ticket = {
                "key": result.get("key", ""),
                "id": result.get("id", ""),
                "url": f"{self.base_url}/browse/{result.get('key', '')}",
                "status": "created",
            }

            # Move to sprint if specified
            if sprint_id and result.get("id"):
                try:
                    await self._move_to_sprint(result["id"], sprint_id)
                    ticket["sprint_id"] = sprint_id
                except Exception as exc:
                    logger.warning("jira_sprint_move_error", error=str(exc))

            logger.info(
                "jira_ticket_created",
                key=ticket["key"],
                incident_id=incident_id,
            )
            return ticket

        except Exception as exc:
            logger.error("jira_create_error", error=str(exc))
            return {"error": str(exc)}

    async def _move_to_sprint(self, issue_id: str, sprint_id: int) -> None:
        """Move an issue to a sprint (requires Jira Software)."""
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{self.base_url}/rest/agile/1.0/sprint/{sprint_id}/issue",
                headers=self._headers(),
                json={"issues": [issue_id]},
            )
            resp.raise_for_status()

    # --- Bulk Ticket Creation from Postmortem ---

    async def create_action_items(
        self,
        incident: Incident,
        action_items: list[dict[str, str]],
        epic_key: str = "",
    ) -> list[dict[str, Any]]:
        """Create Jira tickets for each postmortem action item.

        Args:
            incident: The incident that generated the action items
            action_items: List of {title, description, priority, assignee} dicts
            epic_key: Optional epic to link tickets to
        """
        if not self._enabled:
            return []

        results = []
        for item in action_items:
            # Map incident severity to Jira priority
            priority_map = {
                "P0-Critical": "Highest",
                "P1-High": "High",
                "P2-Medium": "Medium",
                "P3-Low": "Low",
            }
            priority = item.get(
                "priority",
                priority_map.get(incident.severity, "Medium"),
            )

            description = (
                f"{item.get('description', '')}\n\n"
                f"---\n"
                f"Created from OpsLens incident {incident.incident_id}: "
                f"{incident.title}\n"
                f"Severity: {incident.severity}\n"
                f"Service: {incident.service}"
            )

            ticket = await self.create_ticket(
                summary=item.get("title", "Action item from incident"),
                description=description,
                issue_type="Task",
                priority=priority,
                incident_id=incident.incident_id,
                assignee_email=item.get("assignee", ""),
            )

            # Link to epic if specified
            if epic_key and ticket.get("key"):
                try:
                    await self._request(
                        "POST",
                        "/issueLink",
                        json={
                            "type": {"name": "Epic-Story Link"},
                            "inwardIssue": {"key": ticket["key"]},
                            "outwardIssue": {"key": epic_key},
                        },
                    )
                except Exception:
                    pass

            results.append(ticket)

        return results

    # --- Ticket Status Tracking ---

    async def get_ticket_status(self, ticket_key: str) -> dict[str, Any]:
        """Get the current status of a Jira ticket."""
        if not self._enabled:
            return {"error": "Jira not configured"}

        try:
            data = await self._request("GET", f"/issue/{ticket_key}")
            fields = data.get("fields", {})
            return {
                "key": ticket_key,
                "status": fields.get("status", {}).get("name", "Unknown"),
                "assignee": fields.get("assignee", {}).get("displayName", "Unassigned") if fields.get("assignee") else "Unassigned",
                "priority": fields.get("priority", {}).get("name", "Medium"),
                "summary": fields.get("summary", ""),
                "url": f"{self.base_url}/browse/{ticket_key}",
            }
        except Exception as exc:
            return {"error": str(exc)}

    async def get_incident_tickets(self, incident_id: str) -> list[dict[str, Any]]:
        """Get all Jira tickets linked to an incident via labels."""
        if not self._enabled:
            return []

        try:
            label = f"incident-{incident_id.lower()}"
            jql = f'project = {self.project_key} AND labels = "{label}"'
            data = await self._request(
                "GET", f"/search?jql={jql}&fields=key,summary,status,assignee,priority"
            )
            tickets = []
            for issue in data.get("issues", []):
                fields = issue.get("fields", {})
                tickets.append({
                    "key": issue["key"],
                    "summary": fields.get("summary", ""),
                    "status": fields.get("status", {}).get("name", "Unknown"),
                    "assignee": fields.get("assignee", {}).get("displayName", "Unassigned") if fields.get("assignee") else "Unassigned",
                    "priority": fields.get("priority", {}).get("name", ""),
                    "url": f"{self.base_url}/browse/{issue['key']}",
                })
            return tickets
        except Exception as exc:
            logger.error("jira_search_error", error=str(exc))
            return []

    # --- Sprint Information ---

    async def get_active_sprint(self, board_id: int) -> dict[str, Any] | None:
        """Get the active sprint for a board."""
        if not self._enabled:
            return None

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    f"{self.base_url}/rest/agile/1.0/board/{board_id}/sprint",
                    headers=self._headers(),
                    params={"state": "active"},
                )
                resp.raise_for_status()
                data = resp.json()

            sprints = data.get("values", [])
            if sprints:
                s = sprints[0]
                return {
                    "id": s["id"],
                    "name": s["name"],
                    "state": s["state"],
                    "start_date": s.get("startDate", ""),
                    "end_date": s.get("endDate", ""),
                }
        except Exception as exc:
            logger.error("jira_sprint_error", error=str(exc))

        return None

    # --- Add Comment to Ticket ---

    async def add_comment(self, ticket_key: str, comment: str) -> dict[str, Any]:
        """Add a comment to a Jira ticket."""
        if not self._enabled:
            return {"error": "Jira not configured"}

        try:
            body = {
                "body": {
                    "version": 1,
                    "type": "doc",
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [{"type": "text", "text": comment}],
                        }
                    ],
                }
            }
            result = await self._request(
                "POST", f"/issue/{ticket_key}/comment", json=body
            )
            return {"status": "ok", "comment_id": result.get("id", "")}
        except Exception as exc:
            return {"error": str(exc)}

    # --- Test Connection ---

    async def test_connection(self) -> dict[str, Any]:
        """Test Jira API connectivity."""
        if not self._enabled:
            return {"status": "disabled", "message": "Jira not configured"}

        try:
            data = await self._request("GET", "/myself")
            return {
                "status": "ok",
                "message": f"Connected as {data.get('displayName', 'unknown')} ({data.get('emailAddress', '')})",
                "user": data.get("displayName", ""),
            }
        except Exception as exc:
            return {"status": "error", "message": str(exc)}


class LinearIntegration:
    """Linear.app integration for ticket management."""

    def __init__(
        self,
        api_key: str = "",
        team_id: str = "",
        default_label: str = "incident",
    ):
        self.api_key = api_key
        self.team_id = team_id
        self.default_label = default_label
        self._enabled = bool(api_key)

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def _graphql(self, query: str, variables: dict | None = None) -> dict[str, Any]:
        """Execute a Linear GraphQL query."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "https://api.linear.app/graphql",
                headers={
                    "Authorization": self.api_key,
                    "Content-Type": "application/json",
                },
                json={"query": query, "variables": variables or {}},
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("errors"):
                logger.error("linear_graphql_error", errors=data["errors"])
            return data.get("data", {})

    async def create_ticket(
        self,
        title: str,
        description: str,
        priority: int = 2,
        incident_id: str = "",
        assignee_id: str = "",
    ) -> dict[str, Any]:
        """Create a Linear issue.

        Priority: 0=No priority, 1=Urgent, 2=High, 3=Medium, 4=Low
        """
        if not self._enabled:
            return {"error": "Linear integration not configured"}

        try:
            mutation = """
            mutation CreateIssue($input: IssueCreateInput!) {
                issueCreate(input: $input) {
                    success
                    issue {
                        id
                        identifier
                        url
                        title
                    }
                }
            }
            """

            desc = description
            if incident_id:
                desc += f"\n\n---\nOpsLens Incident: {incident_id}"

            input_data: dict[str, Any] = {
                "teamId": self.team_id,
                "title": title,
                "description": desc,
                "priority": priority,
            }

            if assignee_id:
                input_data["assigneeId"] = assignee_id

            result = await self._graphql(mutation, {"input": input_data})
            issue = result.get("issueCreate", {}).get("issue", {})

            if issue:
                logger.info(
                    "linear_ticket_created",
                    identifier=issue.get("identifier"),
                    incident_id=incident_id,
                )
                return {
                    "key": issue.get("identifier", ""),
                    "id": issue.get("id", ""),
                    "url": issue.get("url", ""),
                    "status": "created",
                }

            return {"error": "Failed to create Linear issue"}

        except Exception as exc:
            logger.error("linear_create_error", error=str(exc))
            return {"error": str(exc)}

    async def create_action_items(
        self,
        incident: Incident,
        action_items: list[dict[str, str]],
    ) -> list[dict[str, Any]]:
        """Create Linear issues for postmortem action items."""
        if not self._enabled:
            return []

        priority_map = {
            "P0-Critical": 1,
            "P1-High": 2,
            "P2-Medium": 3,
            "P3-Low": 4,
        }
        default_priority = priority_map.get(incident.severity, 3)

        results = []
        for item in action_items:
            description = (
                f"{item.get('description', '')}\n\n"
                f"From incident {incident.incident_id}: {incident.title}\n"
                f"Service: {incident.service}"
            )
            ticket = await self.create_ticket(
                title=item.get("title", "Action item"),
                description=description,
                priority=item.get("priority", default_priority),
                incident_id=incident.incident_id,
            )
            results.append(ticket)

        return results

    async def get_ticket_status(self, issue_id: str) -> dict[str, Any]:
        """Get Linear issue status."""
        if not self._enabled:
            return {"error": "Linear not configured"}

        try:
            query = """
            query Issue($id: String!) {
                issue(id: $id) {
                    id
                    identifier
                    title
                    url
                    state { name }
                    assignee { name }
                    priority
                }
            }
            """
            result = await self._graphql(query, {"id": issue_id})
            issue = result.get("issue", {})
            if issue:
                return {
                    "key": issue.get("identifier", ""),
                    "status": issue.get("state", {}).get("name", "Unknown"),
                    "assignee": issue.get("assignee", {}).get("name", "Unassigned") if issue.get("assignee") else "Unassigned",
                    "summary": issue.get("title", ""),
                    "url": issue.get("url", ""),
                }
            return {"error": "Issue not found"}
        except Exception as exc:
            return {"error": str(exc)}

    async def test_connection(self) -> dict[str, Any]:
        """Test Linear API connectivity."""
        if not self._enabled:
            return {"status": "disabled", "message": "Linear not configured"}

        try:
            query = """
            query Me {
                viewer {
                    id
                    name
                    email
                }
            }
            """
            result = await self._graphql(query)
            viewer = result.get("viewer", {})
            return {
                "status": "ok",
                "message": f"Connected as {viewer.get('name', 'unknown')} ({viewer.get('email', '')})",
            }
        except Exception as exc:
            return {"status": "error", "message": str(exc)}
