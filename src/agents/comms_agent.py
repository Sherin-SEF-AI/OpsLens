"""Communications Agent: Generate incident communications for different audiences."""

from typing import Any

import structlog

from src.agents.prompts import COMMS_SYSTEM_PROMPT, COMMS_TOOLS
from src.incidents.models import Incident
from src.notion_mcp.tools import NotionMCPTools

logger = structlog.get_logger()

MAX_TOOL_ROUNDS = 8


class CommsAgent:
    """Communications agent that generates status updates for P0/P1 incidents."""

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
        log = logger.bind(incident_id=incident.incident_id, tool=tool_name)

        if tool_name == "fetch_page":
            result = await self.notion.fetch_page(tool_input["page_url_or_id"])
            log.info("tool_executed")
            return result

        elif tool_name == "add_incident_comment":
            await self.notion.add_comment(
                tool_input["incident_page_id"], tool_input["comment"]
            )
            log.info("comment_added")
            return "Comment added successfully"

        return f"Unknown tool: {tool_name}"

    async def run(
        self,
        incident: Incident,
        triage_result: dict[str, Any],
        correlation_result: dict[str, Any],
        remediation_result: dict[str, Any],
    ) -> dict[str, Any]:
        """Run the comms agent to generate incident communications."""
        log = logger.bind(incident_id=incident.incident_id, agent="comms")
        log.info("comms_agent_started")

        user_message = f"""Generate incident communications for this high-severity incident:

**Incident ID:** {incident.incident_id}
**Title:** {incident.title}
**Description:** {incident.description}
**Severity:** {incident.severity}
**Service:** {incident.service}
**Status:** {incident.status.value}
**Triggered At:** {incident.triggered_at.isoformat()}

**Triage Analysis:** {triage_result.get('analysis', 'N/A')}
**Correlation Analysis:** {correlation_result.get('analysis', 'N/A')}
**Remediation Analysis:** {remediation_result.get('analysis', 'N/A')}

Notion Page ID: {incident.notion_page_id}

Generate three communication templates (Status Page, Executive Summary, Internal Engineering) and add them as a comment on the incident page."""

        messages: list[dict[str, Any]] = [{"role": "user", "content": user_message}]
        result: dict[str, Any] = {"analysis": ""}

        for round_num in range(MAX_TOOL_ROUNDS):
            try:
                response = await self.client.messages.create(
                    model=self.model,
                    max_tokens=2048,
                    system=COMMS_SYSTEM_PROMPT,
                    tools=COMMS_TOOLS,
                    messages=messages,
                )
            except Exception:
                log.exception("llm_api_error", round=round_num)
                break

            if response.stop_reason == "end_turn":
                for block in response.content:
                    if block.type == "text":
                        result["analysis"] = block.text
                log.info("comms_agent_completed", rounds=round_num + 1)
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
