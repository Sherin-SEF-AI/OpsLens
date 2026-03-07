"""Confidence DB Tracker: logs agent confidence scores to a Notion database."""

from datetime import datetime, timezone
from typing import Any

import structlog

from src.notion_mcp.tools import NotionMCPTools

logger = structlog.get_logger()


class ConfidenceTracker:
    """Logs agent confidence scores to a Notion database for historical tracking.

    Each record stores: incident_id, agent_name, confidence_score, reason, timestamp.
    This enables feedback loops and confidence trend analysis.
    """

    def __init__(self, notion_tools: NotionMCPTools, database_id: str = ""):
        self.notion = notion_tools
        self.database_id = database_id

    async def log_confidence(
        self,
        incident_id: str,
        agent_name: str,
        score: int | None,
        reason: str,
        low_confidence: bool,
    ) -> None:
        """Log a single agent confidence record to Notion."""
        if not self.database_id:
            logger.debug("confidence_tracking_disabled", reason="No database ID")
            return

        try:
            properties = {
                "Name": {
                    "title": [
                        {"text": {"content": f"{incident_id} - {agent_name}"}}
                    ]
                },
                "Incident ID": {
                    "rich_text": [{"text": {"content": incident_id}}]
                },
                "Agent": {"select": {"name": agent_name}},
                "Confidence Score": {"number": score if score is not None else 0},
                "Reason": {
                    "rich_text": [{"text": {"content": reason[:2000]}}]
                },
                "Low Confidence": {"checkbox": low_confidence},
                "Timestamp": {
                    "date": {"start": datetime.now(timezone.utc).isoformat()}
                },
            }

            await self.notion.create_page(
                parent_id=self.database_id,
                title=f"{incident_id} - {agent_name}",
                properties=properties,
            )
            logger.info(
                "confidence_logged",
                incident_id=incident_id,
                agent=agent_name,
                score=score,
            )
        except Exception:
            logger.exception(
                "confidence_log_error",
                incident_id=incident_id,
                agent=agent_name,
            )

    async def log_all(
        self,
        incident_id: str,
        confidences: dict[str, dict[str, Any]],
    ) -> None:
        """Log confidence scores for all agents in a pipeline run."""
        for agent_name, conf in confidences.items():
            await self.log_confidence(
                incident_id=incident_id,
                agent_name=agent_name,
                score=conf.get("score"),
                reason=conf.get("reason", ""),
                low_confidence=conf.get("low_confidence", True),
            )
