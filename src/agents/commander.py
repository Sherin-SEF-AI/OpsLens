"""Incident Commander: contextual AI assistant scoped to a specific incident."""

from __future__ import annotations

import json
from typing import Any

import structlog

from src.agents.llm_client import LLMClient
from src.incidents.models import Incident
from src.notion_mcp.tools import NotionMCPTools

logger = structlog.get_logger()

MAX_TOOL_ROUNDS = 5

COMMANDER_SYSTEM_PROMPT = """You are the OpsLens Incident Commander for incident {incident_id}.

You are NOT a general chatbot. You are a purpose-built incident co-pilot scoped to THIS incident. You already know everything the AI agents found. You give direct, operational answers.

RESPONSE FORMAT RULES (MANDATORY):
1. Lead with the answer. Never start with "Let me search..." or "I'll look into..."
2. Use SEARCH tools proactively before answering — don't say you can't find something without trying
3. Every response MUST end with a "Recommended Actions" section using this exact format:

### Recommended Actions
[ACTION:escalate] Escalate to P1 — blast radius affects checkout flow
[ACTION:search:payment-service rollback runbook] Find rollback runbook
[ACTION:transition:Investigating] Move to Investigating status
[ACTION:notify:@oncall-payments] Page the payments on-call team
[ACTION:search:similar memory leak incidents] Check for similar past incidents
[ACTION:runbook:Scale down replicas] kubectl scale deployment payment-svc --replicas=2

ACTION FORMAT:
- [ACTION:search:<query>] — triggers a Notion search in the UI
- [ACTION:transition:<status>] — suggests a status change (Triaged, Investigating, Mitigated, Resolved)
- [ACTION:escalate] — flag for escalation
- [ACTION:notify:<who>] — suggest notifying someone
- [ACTION:runbook:<step>] — a concrete remediation step to execute
- [ACTION:ask:<follow-up question>] — suggest a follow-up question to ask the commander

4. Be SPECIFIC. Reference actual data: service names, error codes, timestamps, page IDs from your searches.
5. When comparing incidents, cite the specific incident IDs and what was different.
6. When suggesting remediation, give the actual commands or steps, not "consider restarting the service."
7. If agent analyses already identified something relevant, reference it directly — don't repeat the full analysis.

CAPABILITIES:
- Search Notion workspace + connected tools (Slack, Drive, Jira, Confluence)
- Fetch any Notion page for deep content
- Search past incidents for patterns
- Cross-reference runbooks, service docs, deployment history

You ADVISE, the human DECIDES. You cannot modify anything.

--- INCIDENT CONTEXT ---
{incident_context}

--- AGENT ANALYSES ---
{agent_analyses}
"""

COMMANDER_TOOLS = [
    {
        "name": "search_notion",
        "description": "Search the Notion workspace and all connected tools (Slack, Google Drive, Jira, Confluence) for relevant context. Use this to find past incidents, runbooks, service documentation, Slack discussions, deployment logs, and more.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Semantic search query — be specific. E.g. 'payment-service runbook', 'memory leak incidents 2026', 'redis connection pool config'",
                }
            },
            "required": ["query"],
        },
    },
    {
        "name": "fetch_page",
        "description": "Fetch the full content of a specific Notion page by ID or URL. Use when you found a relevant page via search and need to read its full content.",
        "input_schema": {
            "type": "object",
            "properties": {
                "page_id": {
                    "type": "string",
                    "description": "Notion page ID (UUID) or URL",
                }
            },
            "required": ["page_id"],
        },
    },
    {
        "name": "search_incidents",
        "description": "Search past incidents by keyword. Use this to find similar incidents, compare patterns, or check if this has happened before.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query for past incidents — service name, error pattern, or description",
                }
            },
            "required": ["query"],
        },
    },
]


class IncidentCommander:
    """Contextual AI assistant scoped to a specific incident."""

    def __init__(
        self,
        llm_client: LLMClient,
        model: str,
        notion_tools: NotionMCPTools,
    ):
        self.client = llm_client
        self.model = model
        self.notion = notion_tools

    def _build_incident_context(self, incident: Incident) -> str:
        lines = [
            f"Incident ID: {incident.incident_id}",
            f"Title: {incident.title}",
            f"Status: {incident.status.value}",
            f"Severity: {incident.severity}",
            f"Service: {incident.service}",
            f"Source: {incident.source}",
            f"Triggered: {incident.triggered_at.isoformat()}",
            f"Description: {incident.description}",
            f"Owner: {incident.owner or 'Unassigned'}",
            f"Notion Page: {incident.notion_page_id}",
        ]
        if incident.root_cause:
            lines.append(f"Root Cause: {incident.root_cause}")
        if incident.labels:
            lines.append(f"Labels: {json.dumps(incident.labels)}")
        return "\n".join(lines)

    def _build_agent_analyses(self, incident: Incident) -> str:
        if not incident.timeline:
            return "No agent analyses available yet."
        analyses = []
        for event in incident.timeline:
            if event.event_type.value.startswith("agent_") or event.event_type.value == "comment":
                analyses.append(
                    f"[{event.timestamp.strftime('%H:%M:%S')}] "
                    f"({event.event_type.value}) {event.message}"
                )
        return "\n\n".join(analyses) if analyses else "No agent analyses available yet."

    async def _execute_tool(
        self, tool_name: str, tool_input: dict[str, Any], incident: Incident
    ) -> str:
        log = logger.bind(incident_id=incident.incident_id, tool=tool_name)

        if tool_name == "search_notion":
            result = await self.notion.search(tool_input["query"])
            log.info("commander_tool_executed", query=tool_input["query"])
            return result if isinstance(result, str) else str(result)

        elif tool_name == "fetch_page":
            result = await self.notion.fetch_page(tool_input["page_id"])
            log.info("commander_tool_executed", page_id=tool_input["page_id"])
            return result if isinstance(result, str) else str(result)

        elif tool_name == "search_incidents":
            result = await self.notion.search(
                f"OPSLENS incident {tool_input['query']}"
            )
            log.info("commander_tool_executed", query=tool_input["query"])
            return result if isinstance(result, str) else str(result)

        return f"Unknown tool: {tool_name}"

    async def query(
        self,
        incident: Incident,
        user_message: str,
        conversation_history: list[dict[str, Any]] | None = None,
    ) -> str:
        """Process a commander query in the context of a specific incident."""
        log = logger.bind(incident_id=incident.incident_id)
        log.info("commander_query", message=user_message[:100])

        # Build system prompt with full incident context
        system = COMMANDER_SYSTEM_PROMPT.format(
            incident_id=incident.incident_id,
            incident_context=self._build_incident_context(incident),
            agent_analyses=self._build_agent_analyses(incident),
        )

        # Build messages: history + new user message
        messages = list(conversation_history or [])
        messages.append({"role": "user", "content": user_message})

        # Agentic loop — allow tool use
        for round_num in range(MAX_TOOL_ROUNDS):
            response = await self.client.messages.create(
                model=self.model,
                max_tokens=2048,
                system=system,
                tools=COMMANDER_TOOLS,
                messages=messages,
            )

            if response.stop_reason == "end_turn":
                # Extract text response
                text_parts = [
                    block.text for block in response.content if block.type == "text"
                ]
                result = "\n".join(text_parts)
                log.info("commander_response", rounds=round_num + 1, length=len(result))
                return result

            # Handle tool calls
            messages.append({"role": "assistant", "content": response.content})

            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    try:
                        result = await self._execute_tool(
                            block.name, block.input, incident
                        )
                        # Truncate long results
                        if len(result) > 4000:
                            result = result[:4000] + "\n... (truncated)"
                    except Exception as e:
                        result = f"Tool error: {e}"
                        log.warning("commander_tool_error", tool=block.name, error=str(e))

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })

            messages.append({"role": "user", "content": tool_results})

        # Hit max tool rounds — do one final call without tools to force a summary
        messages.append({"role": "assistant", "content": response.content})
        # Add any pending tool results
        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": "(search limit reached — summarize what you have)",
                })
        if tool_results:
            messages.append({"role": "user", "content": tool_results})

        final = await self.client.messages.create(
            model=self.model,
            max_tokens=2048,
            system=system,
            messages=messages,
        )
        text_parts = [block.text for block in final.content if block.type == "text"]
        result = "\n".join(text_parts)
        log.info("commander_response", rounds=MAX_TOOL_ROUNDS, length=len(result), forced_summary=True)
        return result
