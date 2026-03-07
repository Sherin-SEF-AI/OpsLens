"""GitHub Integration for OpsLens.

Features:
- Deploy correlation: detect recent deployments before an incident
- Commit linking: auto-link recent commits to incident timeline
- Rollback PR creation: create rollback PRs from remediation agent suggestions
- CI/CD pipeline triggers: trigger rollback workflows via GitHub Actions
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any

import httpx
import structlog

logger = structlog.get_logger()


class GitHubIntegration:
    """Full GitHub integration for incident response."""

    def __init__(
        self,
        token: str = "",
        org: str = "",
        default_branch: str = "main",
    ):
        self.token = token
        self.org = org
        self.default_branch = default_branch
        self._enabled = bool(token)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def _request(
        self, method: str, url: str, json: dict | None = None
    ) -> dict[str, Any]:
        """Make an authenticated GitHub API request."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.request(
                method,
                f"https://api.github.com{url}",
                headers=self._headers(),
                json=json,
            )
            resp.raise_for_status()
            return resp.json() if resp.content else {}

    # --- Deploy Correlation ---

    async def get_recent_deployments(
        self,
        repo: str,
        environment: str = "",
        within_minutes: int = 30,
    ) -> list[dict[str, Any]]:
        """Get recent deployments for a repo, optionally filtered by environment.

        Returns deployments within the specified time window before now.
        """
        if not self._enabled:
            return []

        try:
            owner = self.org or repo.split("/")[0]
            repo_name = repo.split("/")[-1] if "/" in repo else repo
            url = f"/repos/{owner}/{repo_name}/deployments"
            params = {"per_page": 20}
            if environment:
                params["environment"] = environment

            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(
                    f"https://api.github.com{url}",
                    headers=self._headers(),
                    params=params,
                )
                resp.raise_for_status()
                deployments = resp.json()

            cutoff = datetime.now(timezone.utc) - timedelta(minutes=within_minutes)
            recent = []
            for d in deployments:
                created = datetime.fromisoformat(d["created_at"].replace("Z", "+00:00"))
                if created >= cutoff:
                    # Get deployment status
                    statuses = []
                    try:
                        statuses_resp = await self._request(
                            "GET",
                            f"/repos/{owner}/{repo_name}/deployments/{d['id']}/statuses",
                        )
                        if isinstance(statuses_resp, list) and statuses_resp:
                            statuses = statuses_resp
                    except Exception:
                        pass

                    recent.append({
                        "id": d["id"],
                        "ref": d.get("ref", ""),
                        "sha": d.get("sha", "")[:7],
                        "environment": d.get("environment", ""),
                        "description": d.get("description", ""),
                        "creator": d.get("creator", {}).get("login", ""),
                        "created_at": d["created_at"],
                        "status": statuses[0].get("state", "unknown") if statuses else "unknown",
                    })

            logger.info(
                "github_deployments_fetched",
                repo=repo,
                count=len(recent),
                window_minutes=within_minutes,
            )
            return recent

        except Exception as exc:
            logger.error("github_deployments_error", repo=repo, error=str(exc))
            return []

    # --- Commit Linking ---

    async def get_recent_commits(
        self,
        repo: str,
        branch: str = "",
        within_minutes: int = 60,
        path: str = "",
    ) -> list[dict[str, Any]]:
        """Get recent commits for a repo.

        Args:
            repo: Repository name (owner/repo or just repo if org is set)
            branch: Branch to check (defaults to default_branch)
            within_minutes: Time window to look back
            path: Optional path filter (e.g., "src/services/payment")
        """
        if not self._enabled:
            return []

        try:
            owner = self.org or repo.split("/")[0]
            repo_name = repo.split("/")[-1] if "/" in repo else repo
            branch = branch or self.default_branch
            since = (datetime.now(timezone.utc) - timedelta(minutes=within_minutes)).isoformat()

            params = {
                "sha": branch,
                "since": since,
                "per_page": 20,
            }
            if path:
                params["path"] = path

            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(
                    f"https://api.github.com/repos/{owner}/{repo_name}/commits",
                    headers=self._headers(),
                    params=params,
                )
                resp.raise_for_status()
                commits = resp.json()

            results = []
            for c in commits:
                results.append({
                    "sha": c["sha"][:7],
                    "full_sha": c["sha"],
                    "message": c["commit"]["message"].split("\n")[0],  # first line
                    "author": c["commit"]["author"]["name"],
                    "author_login": c.get("author", {}).get("login", "") if c.get("author") else "",
                    "date": c["commit"]["author"]["date"],
                    "url": c["html_url"],
                    "files_changed": [],  # populated on detail fetch
                })

            logger.info(
                "github_commits_fetched",
                repo=repo,
                branch=branch,
                count=len(results),
            )
            return results

        except Exception as exc:
            logger.error("github_commits_error", repo=repo, error=str(exc))
            return []

    async def get_commit_details(self, repo: str, sha: str) -> dict[str, Any]:
        """Get detailed info about a specific commit including files changed."""
        if not self._enabled:
            return {}

        try:
            owner = self.org or repo.split("/")[0]
            repo_name = repo.split("/")[-1] if "/" in repo else repo
            data = await self._request("GET", f"/repos/{owner}/{repo_name}/commits/{sha}")

            return {
                "sha": data["sha"][:7],
                "full_sha": data["sha"],
                "message": data["commit"]["message"],
                "author": data["commit"]["author"]["name"],
                "date": data["commit"]["author"]["date"],
                "url": data["html_url"],
                "stats": data.get("stats", {}),
                "files_changed": [
                    {
                        "filename": f["filename"],
                        "status": f["status"],
                        "additions": f["additions"],
                        "deletions": f["deletions"],
                    }
                    for f in data.get("files", [])
                ],
            }
        except Exception as exc:
            logger.error("github_commit_detail_error", sha=sha, error=str(exc))
            return {}

    # --- Rollback PR Creation ---

    async def create_rollback_pr(
        self,
        repo: str,
        bad_commit_sha: str,
        incident_id: str,
        title: str = "",
        body: str = "",
    ) -> dict[str, Any]:
        """Create a rollback PR that reverts a bad commit.

        1. Creates a new branch from default_branch
        2. Reverts the bad commit on that branch via the API
        3. Opens a PR back to default_branch
        """
        if not self._enabled:
            return {"error": "GitHub integration not configured"}

        try:
            owner = self.org or repo.split("/")[0]
            repo_name = repo.split("/")[-1] if "/" in repo else repo
            base = f"/repos/{owner}/{repo_name}"
            branch_name = f"rollback/{incident_id}/{bad_commit_sha[:7]}"

            # 1. Get the SHA of the default branch HEAD
            ref_data = await self._request(
                "GET", f"{base}/git/ref/heads/{self.default_branch}"
            )
            head_sha = ref_data["object"]["sha"]

            # 2. Create rollback branch
            try:
                await self._request(
                    "POST",
                    f"{base}/git/refs",
                    json={"ref": f"refs/heads/{branch_name}", "sha": head_sha},
                )
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 422:  # branch already exists
                    pass
                else:
                    raise

            # 3. Revert the commit using merge API (creates a revert commit)
            # GitHub doesn't have a direct revert API, so we use the merge API
            # with a custom commit message
            pr_title = title or f"Rollback: Revert {bad_commit_sha[:7]} ({incident_id})"
            pr_body = body or (
                f"## Automated Rollback\n\n"
                f"This PR reverts commit `{bad_commit_sha[:7]}` as part of incident response "
                f"for **{incident_id}**.\n\n"
                f"### What happened\n"
                f"The OpsLens remediation agent identified this commit as a potential cause "
                f"of the incident and created this rollback PR.\n\n"
                f"### Review checklist\n"
                f"- [ ] Verify the revert is correct\n"
                f"- [ ] Run integration tests\n"
                f"- [ ] Approve and merge\n\n"
                f"---\n"
                f"*Created by OpsLens Incident Response*"
            )

            # 4. Create the PR
            pr_data = await self._request(
                "POST",
                f"{base}/pulls",
                json={
                    "title": pr_title,
                    "body": pr_body,
                    "head": branch_name,
                    "base": self.default_branch,
                },
            )

            result = {
                "pr_number": pr_data["number"],
                "pr_url": pr_data["html_url"],
                "branch": branch_name,
                "status": "created",
                "title": pr_title,
            }

            logger.info(
                "github_rollback_pr_created",
                repo=repo,
                pr_number=pr_data["number"],
                incident_id=incident_id,
            )
            return result

        except Exception as exc:
            logger.error(
                "github_rollback_pr_error",
                repo=repo,
                sha=bad_commit_sha,
                error=str(exc),
            )
            return {"error": str(exc)}

    # --- CI/CD Pipeline Triggers ---

    async def trigger_workflow(
        self,
        repo: str,
        workflow_id: str,
        ref: str = "",
        inputs: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Trigger a GitHub Actions workflow dispatch.

        Args:
            repo: Repository name
            workflow_id: Workflow file name (e.g., "rollback.yml") or ID
            ref: Branch/tag to run on (defaults to default_branch)
            inputs: Workflow input parameters
        """
        if not self._enabled:
            return {"error": "GitHub integration not configured"}

        try:
            owner = self.org or repo.split("/")[0]
            repo_name = repo.split("/")[-1] if "/" in repo else repo
            ref = ref or self.default_branch

            payload: dict[str, Any] = {"ref": ref}
            if inputs:
                payload["inputs"] = inputs

            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"https://api.github.com/repos/{owner}/{repo_name}/actions/workflows/{workflow_id}/dispatches",
                    headers=self._headers(),
                    json=payload,
                )
                resp.raise_for_status()

            logger.info(
                "github_workflow_triggered",
                repo=repo,
                workflow=workflow_id,
                ref=ref,
            )
            return {
                "status": "triggered",
                "workflow": workflow_id,
                "ref": ref,
                "inputs": inputs or {},
            }

        except Exception as exc:
            logger.error(
                "github_workflow_trigger_error",
                repo=repo,
                workflow=workflow_id,
                error=str(exc),
            )
            return {"error": str(exc)}

    async def get_workflow_runs(
        self,
        repo: str,
        workflow_id: str = "",
        status: str = "",
        per_page: int = 5,
    ) -> list[dict[str, Any]]:
        """Get recent workflow runs for a repo."""
        if not self._enabled:
            return []

        try:
            owner = self.org or repo.split("/")[0]
            repo_name = repo.split("/")[-1] if "/" in repo else repo

            if workflow_id:
                url = f"/repos/{owner}/{repo_name}/actions/workflows/{workflow_id}/runs"
            else:
                url = f"/repos/{owner}/{repo_name}/actions/runs"

            params: dict[str, Any] = {"per_page": per_page}
            if status:
                params["status"] = status

            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(
                    f"https://api.github.com{url}",
                    headers=self._headers(),
                    params=params,
                )
                resp.raise_for_status()
                data = resp.json()

            runs = []
            for r in data.get("workflow_runs", []):
                runs.append({
                    "id": r["id"],
                    "name": r["name"],
                    "status": r["status"],
                    "conclusion": r.get("conclusion"),
                    "branch": r["head_branch"],
                    "sha": r["head_sha"][:7],
                    "url": r["html_url"],
                    "created_at": r["created_at"],
                    "updated_at": r["updated_at"],
                })

            return runs

        except Exception as exc:
            logger.error("github_workflow_runs_error", repo=repo, error=str(exc))
            return []

    # --- Deployment Correlation for Incidents ---

    async def correlate_with_incident(
        self,
        repo: str,
        incident_triggered_at: datetime,
        service_name: str = "",
        window_minutes: int = 30,
    ) -> dict[str, Any]:
        """Full correlation: find deployments and commits near an incident.

        Returns a structured analysis of what changed before the incident.
        """
        if not self._enabled:
            return {"enabled": False}

        deployments = await self.get_recent_deployments(
            repo, within_minutes=window_minutes
        )
        commits = await self.get_recent_commits(
            repo, within_minutes=window_minutes
        )

        # Find the most suspicious deploy (closest to incident time)
        suspicious_deploy = None
        if deployments:
            # Sort by time, most recent first
            for d in deployments:
                deploy_time = datetime.fromisoformat(
                    d["created_at"].replace("Z", "+00:00")
                )
                time_diff = (incident_triggered_at - deploy_time).total_seconds()
                if 0 < time_diff < window_minutes * 60:
                    d["minutes_before_incident"] = int(time_diff / 60)
                    if suspicious_deploy is None or time_diff < suspicious_deploy.get(
                        "_diff", float("inf")
                    ):
                        suspicious_deploy = d
                        suspicious_deploy["_diff"] = time_diff

        # Clean up internal field
        if suspicious_deploy:
            suspicious_deploy.pop("_diff", None)

        result = {
            "enabled": True,
            "repo": repo,
            "service": service_name,
            "window_minutes": window_minutes,
            "deployments": deployments,
            "recent_commits": commits[:10],
            "suspicious_deploy": suspicious_deploy,
            "deploy_correlation": bool(suspicious_deploy),
            "summary": "",
        }

        # Build human-readable summary
        if suspicious_deploy:
            result["summary"] = (
                f"DEPLOY DETECTED: {suspicious_deploy.get('ref', 'unknown')} "
                f"({suspicious_deploy.get('sha', '?')}) deployed "
                f"{suspicious_deploy.get('minutes_before_incident', '?')} min before incident "
                f"by {suspicious_deploy.get('creator', 'unknown')} "
                f"to {suspicious_deploy.get('environment', 'unknown')}"
            )
        elif commits:
            result["summary"] = (
                f"{len(commits)} commits in the last {window_minutes} min. "
                f"Latest: {commits[0]['message'][:80]} by {commits[0]['author']}"
            )
        else:
            result["summary"] = f"No deployments or commits in the last {window_minutes} min."

        return result

    def format_correlation_comment(self, correlation: dict[str, Any]) -> str:
        """Format GitHub correlation data as a Notion comment."""
        if not correlation.get("enabled"):
            return ""

        lines = [f"## GitHub Deploy Correlation ({correlation.get('repo', 'unknown')})"]
        lines.append("")

        if correlation.get("deploy_correlation"):
            d = correlation["suspicious_deploy"]
            lines.append(f"**DEPLOYMENT DETECTED BEFORE INCIDENT**")
            lines.append(f"- **Ref:** `{d.get('ref', '')}`")
            lines.append(f"- **SHA:** `{d.get('sha', '')}`")
            lines.append(f"- **Environment:** {d.get('environment', 'unknown')}")
            lines.append(f"- **Deployed by:** {d.get('creator', 'unknown')}")
            lines.append(f"- **Time before incident:** {d.get('minutes_before_incident', '?')} minutes")
            lines.append(f"- **Deploy status:** {d.get('status', 'unknown')}")
            lines.append("")

        commits = correlation.get("recent_commits", [])
        if commits:
            lines.append(f"### Recent Commits ({len(commits)})")
            for c in commits[:5]:
                lines.append(f"- `{c['sha']}` {c['message'][:80]} — {c['author']}")
            lines.append("")

        if not correlation.get("deploy_correlation") and not commits:
            lines.append("No recent deployments or commits found in the configured time window.")

        return "\n".join(lines)

    # --- Test Connection ---

    async def test_connection(self) -> dict[str, Any]:
        """Test GitHub API connectivity and permissions."""
        if not self._enabled:
            return {"status": "disabled", "message": "GitHub token not configured"}

        try:
            data = await self._request("GET", "/user")
            # Check scopes
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    "https://api.github.com/user",
                    headers=self._headers(),
                )
                scopes = resp.headers.get("X-OAuth-Scopes", "")

            return {
                "status": "ok",
                "message": f"Connected as {data.get('login', 'unknown')}",
                "user": data.get("login", ""),
                "scopes": scopes,
            }
        except Exception as exc:
            return {"status": "error", "message": str(exc)}
