"""Notion Command Center: periodically updates a living stats page in Notion."""

import asyncio
from datetime import datetime, timezone
from typing import Any

import structlog

from src.incidents.manager import IncidentManager
from src.notion_mcp.tools import NotionMCPTools

logger = structlog.get_logger()


class CommandCenter:
    """Maintains a living Notion page with real-time incident stats.

    Updates periodically with:
    - Active incident count and list
    - MTTR by severity
    - Agent action stats
    - Recent incident summary
    """

    def __init__(
        self,
        notion_tools: NotionMCPTools,
        incident_manager: IncidentManager,
        page_id: str = "",
        update_interval: int = 120,
    ):
        self.notion = notion_tools
        self.incidents = incident_manager
        self.page_id = page_id
        self.update_interval = update_interval
        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        """Start the periodic update loop."""
        if not self.page_id:
            logger.info("command_center_disabled", reason="No page ID configured")
            return
        self._running = True
        self._task = asyncio.create_task(self._update_loop())
        logger.info("command_center_started", page_id=self.page_id)

    async def stop(self) -> None:
        """Stop the update loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("command_center_stopped")

    async def _update_loop(self) -> None:
        """Periodically update the command center page."""
        while self._running:
            try:
                await self._update_page()
            except Exception:
                logger.exception("command_center_update_error")
            await asyncio.sleep(self.update_interval)

    def _build_stats_content(self) -> str:
        """Build the markdown content for the command center page."""
        stats = self.incidents.get_stats()
        active = self.incidents.get_active_incidents()
        all_incidents = self.incidents.get_all_incidents()
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        lines = [
            f"Last updated: {now}\n",
            "## Dashboard Overview\n",
            f"| Metric | Value |",
            f"|--------|-------|",
            f"| Total Incidents | {stats['total']} |",
            f"| Active Incidents | {stats['active']} |",
            f"| Resolved | {stats['resolved']} |",
        ]

        # MTTR by severity
        if stats.get("mttr_by_severity"):
            lines.append("\n## Mean Time to Resolve (MTTR)\n")
            lines.append("| Severity | MTTR |")
            lines.append("|----------|------|")
            for sev, mttr in stats["mttr_by_severity"].items():
                minutes = mttr / 60
                if minutes >= 60:
                    display = f"{minutes / 60:.1f} hours"
                else:
                    display = f"{minutes:.0f} min"
                lines.append(f"| {sev} | {display} |")

        # By severity breakdown
        if stats.get("by_severity"):
            lines.append("\n## Incidents by Severity\n")
            lines.append("| Severity | Count |")
            lines.append("|----------|-------|")
            for sev, count in sorted(stats["by_severity"].items()):
                lines.append(f"| {sev} | {count} |")

        # By service breakdown
        if stats.get("by_service"):
            lines.append("\n## Incidents by Service\n")
            lines.append("| Service | Count |")
            lines.append("|---------|-------|")
            for svc, count in sorted(stats["by_service"].items(), key=lambda x: -x[1]):
                lines.append(f"| {svc} | {count} |")

        # Active incidents list
        if active:
            lines.append("\n## Active Incidents\n")
            lines.append("| ID | Title | Severity | Status | Service | Age |")
            lines.append("|----|-------|----------|--------|---------|-----|")
            for inc in sorted(active, key=lambda i: i.triggered_at, reverse=True):
                age_sec = (datetime.now(timezone.utc) - inc.triggered_at).total_seconds()
                if age_sec >= 3600:
                    age = f"{age_sec / 3600:.1f}h"
                else:
                    age = f"{age_sec / 60:.0f}m"
                lines.append(
                    f"| {inc.incident_id} | {inc.title[:40]} | {inc.severity} "
                    f"| {inc.status.value} | {inc.service} | {age} |"
                )
        else:
            lines.append("\n## Active Incidents\n\nNo active incidents.\n")

        # Agent stats
        total_actions = sum(i.agent_actions_count for i in all_incidents)
        lines.append(f"\n## Agent Statistics\n")
        lines.append(f"| Metric | Value |")
        lines.append(f"|--------|-------|")
        lines.append(f"| Total Agent Actions | {total_actions} |")
        lines.append(f"| Avg Actions/Incident | {total_actions / max(len(all_incidents), 1):.1f} |")

        return "\n".join(lines)

    async def _update_page(self) -> None:
        """Update the command center Notion page."""
        content = self._build_stats_content()

        try:
            await self.notion.add_comment(self.page_id, content)
            logger.debug("command_center_updated")
        except Exception:
            logger.exception("command_center_page_update_error")

    async def force_update(self) -> str:
        """Force an immediate update and return the content."""
        content = self._build_stats_content()
        if self.page_id:
            try:
                await self.notion.add_comment(self.page_id, content)
            except Exception:
                logger.exception("command_center_force_update_error")
        return content
