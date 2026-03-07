"""Postmortem Agent: Auto-generate structured postmortem drafts."""

from typing import Any

import structlog

from src.agents.prompts import POSTMORTEM_SYSTEM_PROMPT, POSTMORTEM_TOOLS
from src.config import OpsLensConfig
from src.incidents.models import Incident
from src.incidents.timeline import format_timeline_for_postmortem
from src.notion_mcp.tools import NotionMCPTools

logger = structlog.get_logger()

MAX_TOOL_ROUNDS = 8


class PostmortemAgent:
    """Postmortem agent that generates blameless postmortem drafts."""

    def __init__(
        self,
        llm_client,
        model: str,
        notion_tools: NotionMCPTools,
        config: OpsLensConfig,
    ):
        self.client = llm_client
        self.model = model
        self.notion = notion_tools
        self.config = config

    async def _execute_tool(
        self, tool_name: str, tool_input: dict[str, Any], incident: Incident
    ) -> str:
        log = logger.bind(incident_id=incident.incident_id, tool=tool_name)

        if tool_name == "fetch_page":
            result = await self.notion.fetch_page(tool_input["page_url_or_id"])
            log.info("tool_executed")
            return result

        elif tool_name == "list_comments":
            result = await self.notion.list_comments(tool_input["page_id"])
            log.info("tool_executed")
            return str(result)

        elif tool_name == "create_postmortem":
            title = tool_input["title"]
            content = tool_input["content"]
            result = await self.notion.create_page(
                parent_id=self.config.NOTION_POSTMORTEMS_DB_ID,
                title=title,
                properties={
                    "Name": {"title": [{"text": {"content": title}}]},
                    "Status": {"select": {"name": "Draft"}},
                    "Blameless": {"checkbox": True},
                },
            )
            log.info("postmortem_page_created")
            return f"Postmortem page created: {title}"

        elif tool_name == "add_incident_comment":
            await self.notion.add_comment(
                tool_input["incident_page_id"], tool_input["comment"]
            )
            log.info("comment_added")
            return "Comment added successfully"

        return f"Unknown tool: {tool_name}"

    async def run(self, incident: Incident) -> dict[str, Any]:
        """Generate a postmortem for a resolved incident."""
        log = logger.bind(incident_id=incident.incident_id, agent="postmortem")
        log.info("postmortem_agent_started")

        duration = "Unknown"
        if incident.duration_seconds:
            mins = incident.duration_seconds // 60
            secs = incident.duration_seconds % 60
            duration = f"{mins}m {secs}s"

        timeline_text = format_timeline_for_postmortem(incident.timeline)

        user_message = f"""Generate a blameless postmortem for this resolved incident:

**Incident ID:** {incident.incident_id}
**Title:** {incident.title}
**Severity:** {incident.severity}
**Service:** {incident.service}
**Duration:** {duration}
**Triggered At:** {incident.triggered_at.isoformat()}
**Resolved At:** {incident.resolved_at.isoformat() if incident.resolved_at else 'N/A'}
**Root Cause:** {incident.root_cause or 'Not determined'}

**Timeline:**
{timeline_text}

Notion Incident Page ID: {incident.notion_page_id}

Steps:
1. Fetch the incident page to get full context and agent analyses
2. List comments to get the complete timeline
3. Create a postmortem page in the Postmortems database
4. Add a comment on the incident page linking to the postmortem"""

        messages: list[dict[str, Any]] = [{"role": "user", "content": user_message}]
        result: dict[str, Any] = {"postmortem_created": False, "analysis": ""}

        for round_num in range(MAX_TOOL_ROUNDS):
            try:
                response = await self.client.messages.create(
                    model=self.model,
                    max_tokens=4096,
                    system=POSTMORTEM_SYSTEM_PROMPT,
                    tools=POSTMORTEM_TOOLS,
                    messages=messages,
                )
            except Exception:
                log.exception("claude_api_error", round=round_num)
                break

            if response.stop_reason == "end_turn":
                for block in response.content:
                    if block.type == "text":
                        result["analysis"] = block.text
                result["postmortem_created"] = True
                log.info("postmortem_agent_completed", rounds=round_num + 1)
                break

            if response.stop_reason == "tool_use":
                messages.append({"role": "assistant", "content": response.content})
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
                break

        return result
