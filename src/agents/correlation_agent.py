"""Correlation Agent: Find related incidents and patterns across connected tools."""

from typing import Any

import structlog

from src.agents.prompts import CORRELATION_SYSTEM_PROMPT, CORRELATION_TOOLS
from src.incidents.models import Incident
from src.notion_mcp.tools import NotionMCPTools
from src.webhooks.schemas import UnifiedAlert

logger = structlog.get_logger()

MAX_TOOL_ROUNDS = 12


class CorrelationAgent:
    """Correlation agent that searches across Notion and connected tools."""

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

        if tool_name == "search_notion":
            result = await self.notion.search(tool_input["query"])
            log.info("tool_executed", query=tool_input["query"])
            return result

        elif tool_name == "fetch_page":
            result = await self.notion.fetch_page(tool_input["page_url_or_id"])
            log.info("tool_executed")
            return result

        elif tool_name == "add_incident_comment":
            await self.notion.add_comment(
                tool_input["incident_page_id"], tool_input["comment"]
            )
            log.info("comment_added")
            return "Comment added successfully"

        elif tool_name == "link_related_incident":
            # Add related info as a comment
            comment = f"**Related Incident Found:** {tool_input['related_info']}"
            await self.notion.add_comment(
                tool_input["current_incident_page_id"], comment
            )
            log.info("related_incident_linked")
            return "Related incident linked"

        return f"Unknown tool: {tool_name}"

    async def run(
        self,
        incident: Incident,
        alert: UnifiedAlert,
        triage_result: dict[str, Any],
    ) -> dict[str, Any]:
        """Run the correlation agent."""
        log = logger.bind(incident_id=incident.incident_id, agent="correlation")
        log.info("correlation_agent_started")

        user_message = f"""Find connections and patterns for this incident:

**Incident ID:** {incident.incident_id}
**Title:** {alert.title}
**Description:** {alert.description}
**Severity:** {incident.severity}
**Service:** {alert.service}
**Source:** {alert.source.value}
**Labels:** {alert.labels}

**Triage Analysis:** {triage_result.get('analysis', 'No triage analysis available')}

Notion Page ID: {incident.notion_page_id}

Search for:
1. Past incidents with similar symptoms for service "{alert.service}"
2. Recent deployments or changes to "{alert.service}"
3. Related Slack discussions about "{alert.service}" issues
4. Relevant documentation or runbooks
5. Related Jira tickets

Add your correlation analysis as a comment on the incident page."""

        messages: list[dict[str, Any]] = [{"role": "user", "content": user_message}]
        result: dict[str, Any] = {"related_incidents": [], "analysis": ""}

        for round_num in range(MAX_TOOL_ROUNDS):
            try:
                response = await self.client.messages.create(
                    model=self.model,
                    max_tokens=2048,
                    system=CORRELATION_SYSTEM_PROMPT,
                    tools=CORRELATION_TOOLS,
                    messages=messages,
                )
            except Exception:
                log.exception("claude_api_error", round=round_num)
                break

            if response.stop_reason == "end_turn":
                for block in response.content:
                    if block.type == "text":
                        result["analysis"] = block.text
                log.info("correlation_agent_completed", rounds=round_num + 1)
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
