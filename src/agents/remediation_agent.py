"""Remediation Agent: Search runbooks and propose fixes."""

from typing import Any

import structlog

from src.agents.prompts import REMEDIATION_SYSTEM_PROMPT, REMEDIATION_TOOLS
from src.incidents.models import Incident
from src.notion_mcp.tools import NotionMCPTools
from src.webhooks.schemas import UnifiedAlert

logger = structlog.get_logger()

MAX_TOOL_ROUNDS = 10


class RemediationAgent:
    """Remediation agent that searches runbooks and proposes fixes."""

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

        return f"Unknown tool: {tool_name}"

    async def run(
        self,
        incident: Incident,
        alert: UnifiedAlert,
        triage_result: dict[str, Any],
        correlation_result: dict[str, Any],
    ) -> dict[str, Any]:
        """Run the remediation agent."""
        log = logger.bind(incident_id=incident.incident_id, agent="remediation")
        log.info("remediation_agent_started")

        title = alert.title if alert else incident.title
        description = alert.description if alert else incident.description
        service = alert.service if alert else incident.service
        labels = alert.labels if alert else incident.labels

        escalation_note = ""
        if correlation_result.get("escalated"):
            escalation_note = "\n\n**⚠️ THIS INCIDENT HAS BEEN ESCALATED. Prioritize immediate mitigation steps.**"

        user_message = f"""Propose remediation for this incident:

**Incident ID:** {incident.incident_id}
**Title:** {title}
**Description:** {description}
**Severity:** {incident.severity}
**Service:** {service}
**Labels:** {labels}

**Triage Analysis:** {triage_result.get('analysis', 'N/A')}
**Correlation Analysis:** {correlation_result.get('analysis', 'N/A')}{escalation_note}

Notion Page ID: {incident.notion_page_id}

Search for applicable runbooks for service "{service}" and propose specific remediation steps. Add your recommendations as a comment on the incident page."""

        messages: list[dict[str, Any]] = [{"role": "user", "content": user_message}]
        result: dict[str, Any] = {"runbooks_found": [], "analysis": ""}

        for round_num in range(MAX_TOOL_ROUNDS):
            try:
                response = await self.client.messages.create(
                    model=self.model,
                    max_tokens=2048,
                    system=REMEDIATION_SYSTEM_PROMPT,
                    tools=REMEDIATION_TOOLS,
                    messages=messages,
                )
            except Exception:
                log.exception("claude_api_error", round=round_num)
                break

            if response.stop_reason == "end_turn":
                for block in response.content:
                    if block.type == "text":
                        result["analysis"] = block.text
                log.info("remediation_agent_completed", rounds=round_num + 1)
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
