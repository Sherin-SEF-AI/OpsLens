"""Triage Agent: Assess severity, categorize, and assign incidents."""

from typing import Any

import structlog

from src.agents.prompts import TRIAGE_SYSTEM_PROMPT, TRIAGE_TOOLS
from src.incidents.models import Incident, TimelineEventType
from src.notion_mcp.tools import NotionMCPTools
from src.webhooks.schemas import UnifiedAlert

logger = structlog.get_logger()

MAX_TOOL_ROUNDS = 10


class TriageAgent:
    """Triage agent that assesses and categorizes incidents using Claude API."""

    def __init__(
        self,
        llm_client,
        model: str,
        notion_tools: NotionMCPTools,
    ):
        self.client = llm_client
        self.model = model
        self.notion = notion_tools

    async def _execute_tool(
        self, tool_name: str, tool_input: dict[str, Any], incident: Incident
    ) -> str:
        """Execute a tool call and return the result as a string."""
        log = logger.bind(incident_id=incident.incident_id, tool=tool_name)

        if tool_name == "search_notion":
            result = await self.notion.search(tool_input["query"])
            log.info("tool_executed", query=tool_input["query"])
            return result

        elif tool_name == "fetch_service_info":
            result = await self.notion.search(
                f"service {tool_input['service_name']}"
            )
            log.info("tool_executed", service=tool_input["service_name"])
            return result

        elif tool_name == "update_incident_severity":
            page_id = tool_input["incident_page_id"]
            severity = tool_input["severity"]
            await self.notion.update_page(
                page_id,
                properties={"Severity": {"select": {"name": severity}}},
            )
            incident.severity = severity
            log.info("severity_updated", severity=severity)
            return f"Severity updated to {severity}"

        elif tool_name == "add_incident_comment":
            page_id = tool_input["incident_page_id"]
            comment = tool_input["comment"]
            await self.notion.add_comment(page_id, comment)
            log.info("comment_added")
            return "Comment added successfully"

        else:
            return f"Unknown tool: {tool_name}"

    async def run(
        self, incident: Incident, alert: UnifiedAlert
    ) -> dict[str, Any]:
        """Run the triage agent on an incident."""
        log = logger.bind(incident_id=incident.incident_id, agent="triage")
        log.info("triage_agent_started")

        user_message = f"""Analyze this new incident:

**Incident ID:** {incident.incident_id}
**Title:** {alert.title}
**Description:** {alert.description}
**Auto-detected Severity:** {alert.severity.value}
**Service:** {alert.service}
**Source:** {alert.source.value}
**Labels:** {alert.labels}
**Triggered At:** {alert.triggered_at.isoformat()}
**Source URL:** {alert.source_url}
**Dashboard URL:** {alert.dashboard_url}

Notion Page ID for this incident: {incident.notion_page_id}

Please triage this incident: validate the severity, categorize it, and add your analysis as a comment."""

        messages: list[dict[str, Any]] = [{"role": "user", "content": user_message}]

        result = {"severity": incident.severity, "analysis": ""}

        for round_num in range(MAX_TOOL_ROUNDS):
            try:
                response = await self.client.messages.create(
                    model=self.model,
                    max_tokens=2048,
                    system=TRIAGE_SYSTEM_PROMPT,
                    tools=TRIAGE_TOOLS,
                    messages=messages,
                )
            except Exception:
                log.exception("claude_api_error", round=round_num)
                break

            # Process response
            if response.stop_reason == "end_turn":
                # Agent is done — extract final text
                for block in response.content:
                    if block.type == "text":
                        result["analysis"] = block.text
                log.info("triage_agent_completed", rounds=round_num + 1)
                break

            if response.stop_reason == "tool_use":
                # Append assistant message
                messages.append({"role": "assistant", "content": response.content})

                # Execute tool calls
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        try:
                            tool_result = await self._execute_tool(
                                block.name, block.input, incident
                            )
                        except Exception as e:
                            log.exception("tool_execution_error", tool=block.name)
                            tool_result = f"Error: {e}"

                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": tool_result,
                            }
                        )

                messages.append({"role": "user", "content": tool_results})
            else:
                # Unexpected stop reason
                log.warning("unexpected_stop_reason", reason=response.stop_reason)
                break

        result["severity"] = incident.severity
        return result
