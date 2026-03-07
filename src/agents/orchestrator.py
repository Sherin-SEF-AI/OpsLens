"""Agent orchestrator: coordinates AI agents for incident response."""

from __future__ import annotations

import asyncio
from typing import Any, TYPE_CHECKING

import structlog

from src.agents.comms_agent import CommsAgent
from src.agents.confidence import extract_all_confidences, format_confidence_summary
from src.agents.confidence_tracker import ConfidenceTracker
from src.agents.correlation_agent import CorrelationAgent
from src.agents.postmortem_agent import PostmortemAgent
from src.agents.remediation_agent import RemediationAgent
from src.agents.triage_agent import TriageAgent
from src.config import OpsLensConfig
from src.incidents.manager import IncidentManager
from src.incidents.models import Incident, IncidentStatus, TimelineEventType
from src.integrations.slack_notifier import send_slack_notification
from src.notion_mcp.tools import NotionMCPTools
from src.webhooks.schemas import UnifiedAlert

if TYPE_CHECKING:
    from src.sync.notion_watcher import NotionChangeEvent

logger = structlog.get_logger()


class AgentOrchestrator:
    """Coordinates AI agents for incident response."""

    def __init__(
        self,
        config: OpsLensConfig,
        notion_tools: NotionMCPTools,
        incident_manager: IncidentManager,
    ):
        self.config = config
        self.notion = notion_tools
        self.incidents = incident_manager
        self._semaphore = asyncio.Semaphore(config.MAX_CONCURRENT_AGENTS)

        # Initialize LLM client (supports Anthropic + Gemini with fallback)
        from src.agents.llm_client import LLMClient
        self.llm_client = LLMClient.from_config(config)

        # Pick model based on provider
        if config.LLM_PROVIDER == "gemini":
            model = config.GEMINI_MODEL
        else:
            model = config.ANTHROPIC_MODEL

        # Initialize agents
        self.triage_agent = TriageAgent(self.llm_client, model, notion_tools)
        self.correlation_agent = CorrelationAgent(
            self.llm_client, model, notion_tools
        )
        self.remediation_agent = RemediationAgent(
            self.llm_client, model, notion_tools
        )
        self.postmortem_agent = PostmortemAgent(
            self.llm_client, model, notion_tools, config
        )
        self.comms_agent = CommsAgent(self.llm_client, model, notion_tools)
        self.confidence_tracker = ConfidenceTracker(
            notion_tools, config.NOTION_CONFIDENCE_DB_ID
        )

        # Enterprise integrations (set later via set_integrations)
        self.github = None
        self.slack_integration = None
        self.jira = None
        self.linear = None
        self.cloud = None
        self.knowledge_base = None
        self.outbound_webhooks = None

    def set_integrations(self, **kwargs) -> None:
        """Wire enterprise integrations into the orchestrator."""
        self.github = kwargs.get("github")
        self.slack_integration = kwargs.get("slack_integration")
        self.jira = kwargs.get("jira")
        self.linear = kwargs.get("linear")
        self.cloud = kwargs.get("cloud")
        self.knowledge_base = kwargs.get("knowledge_base")
        self.outbound_webhooks = kwargs.get("outbound_webhooks")

    async def handle_new_incident(
        self, incident: Incident, alert: UnifiedAlert
    ) -> None:
        """
        Full agent pipeline for new incidents:
        1. Triage Agent -> Assess severity, categorize
        2. Correlation Agent -> Find related incidents & context
        3. Remediation Agent -> Search runbooks, propose fixes
        """
        async with self._semaphore:
            log = logger.bind(incident_id=incident.incident_id)
            log.info("agent_pipeline_started")

            try:
                # 0. Pre-pipeline: GitHub deploy correlation & KB search
                github_correlation = {}
                if self.github and self.github.enabled:
                    try:
                        github_correlation = await self.github.correlate_with_incident(
                            repo=incident.labels.get("repo", incident.service),
                            incident_triggered_at=incident.triggered_at,
                            service_name=incident.service,
                        )
                        if github_correlation.get("deploy_correlation"):
                            comment = self.github.format_correlation_comment(github_correlation)
                            if incident.notion_page_id and comment:
                                await self.notion.add_comment(incident.notion_page_id, comment)
                            await self.incidents.add_timeline_event(
                                incident.incident_id,
                                f"GitHub deploy detected: {github_correlation.get('summary', '')}",
                                TimelineEventType.COMMENT,
                                actor="github-integration",
                            )
                    except Exception:
                        log.exception("github_correlation_error")

                kb_similar = []
                if self.knowledge_base:
                    try:
                        kb_similar = await self.knowledge_base.find_similar_incidents(incident, top_k=3)
                        if kb_similar:
                            kb_summary = "\n".join(
                                f"- {r['title']} (score: {r['score']:.2f})"
                                for r in kb_similar
                            )
                            await self.incidents.add_timeline_event(
                                incident.incident_id,
                                f"Knowledge base found {len(kb_similar)} similar past incidents:\n{kb_summary}",
                                TimelineEventType.COMMENT,
                                actor="knowledge-base",
                            )
                    except Exception:
                        log.exception("kb_search_error")

                # 1. Triage
                await self.incidents.add_timeline_event(
                    incident.incident_id,
                    "Triage agent started analysis",
                    TimelineEventType.AGENT_TRIAGE,
                    actor="triage-agent",
                )
                triage_result = await self.triage_agent.run(incident, alert)

                # Transition to TRIAGED
                try:
                    await self.incidents.transition(
                        incident.incident_id,
                        IncidentStatus.TRIAGED,
                        reason="Triage agent completed analysis",
                        actor="triage-agent",
                    )
                except Exception:
                    log.warning("triage_transition_failed")

                # 2. Correlation
                await self.incidents.add_timeline_event(
                    incident.incident_id,
                    "Correlation agent searching for patterns",
                    TimelineEventType.AGENT_CORRELATION,
                    actor="correlation-agent",
                )
                correlation_result = await self.correlation_agent.run(
                    incident, alert, triage_result
                )

                # 3. Remediation
                await self.incidents.add_timeline_event(
                    incident.incident_id,
                    "Remediation agent searching for runbooks",
                    TimelineEventType.AGENT_REMEDIATION,
                    actor="remediation-agent",
                )
                remediation_result = await self.remediation_agent.run(
                    incident, alert, triage_result, correlation_result
                )

                # 4. Communications Agent for P0/P1 incidents
                comms_result: dict[str, Any] = {}
                if incident.severity in ("P0-Critical", "P1-High"):
                    await self.incidents.add_timeline_event(
                        incident.incident_id,
                        "Communications agent generating incident updates",
                        TimelineEventType.COMMENT,
                        actor="comms-agent",
                    )
                    comms_result = await self.comms_agent.run(
                        incident, triage_result, correlation_result, remediation_result
                    )

                # 5. Extract and log confidence scores
                agent_results = {
                    "triage": triage_result,
                    "correlation": correlation_result,
                    "remediation": remediation_result,
                }
                if comms_result:
                    agent_results["communications"] = comms_result

                confidences = extract_all_confidences(agent_results)
                confidence_summary = format_confidence_summary(confidences)

                # Post confidence summary to Notion
                if incident.notion_page_id:
                    try:
                        await self.notion.add_comment(
                            incident.notion_page_id, confidence_summary
                        )
                    except Exception:
                        log.exception("confidence_summary_comment_error")

                # Log to confidence tracking DB
                try:
                    await self.confidence_tracker.log_all(
                        incident.incident_id, confidences
                    )
                except Exception:
                    log.exception("confidence_tracking_error")

                # Transition to INVESTIGATING (skip if already resolved by human)
                if incident.status not in (
                    IncidentStatus.RESOLVED,
                    IncidentStatus.POSTMORTEM,
                ):
                    try:
                        await self.incidents.transition(
                            incident.incident_id,
                            IncidentStatus.INVESTIGATING,
                            reason="Agent analysis complete, awaiting human action",
                            actor="orchestrator",
                        )
                    except Exception:
                        log.warning("investigating_transition_failed")

                # Update agent actions count
                if incident.notion_page_id:
                    try:
                        await self.notion.update_page(
                            incident.notion_page_id,
                            properties={
                                "Agent Actions Count": {"number": incident.agent_actions_count}
                            },
                        )
                    except Exception:
                        log.exception("agent_count_update_error")

                # 6. Post-pipeline: Slack thread updates
                if self.slack_integration and self.slack_integration.enabled:
                    try:
                        triage_summary = triage_result.get("text", "")[:300] if isinstance(triage_result, dict) else str(triage_result)[:300]
                        await self.slack_integration.post_agent_update(
                            incident.incident_id, "triage", triage_summary
                        )
                    except Exception:
                        log.exception("slack_thread_update_error")

                # 7. Post-pipeline: Outbound webhook dispatch
                if self.outbound_webhooks:
                    try:
                        await self.outbound_webhooks.dispatch(
                            "agent.triage_completed",
                            {"triage_result": str(triage_result)[:500]},
                            incident,
                        )
                    except Exception:
                        log.exception("outbound_webhook_dispatch_error")

                log.info(
                    "agent_pipeline_completed",
                    agent_actions=incident.agent_actions_count,
                    confidences={k: v.get("score") for k, v in confidences.items()},
                )

            except Exception:
                log.exception("agent_pipeline_error")
                await self.incidents.add_timeline_event(
                    incident.incident_id,
                    "Agent pipeline encountered an error — manual investigation required",
                    TimelineEventType.COMMENT,
                    actor="orchestrator",
                )

    async def handle_incident_resolved(self, incident: Incident) -> None:
        """Trigger postmortem generation when an incident is resolved."""
        async with self._semaphore:
            log = logger.bind(incident_id=incident.incident_id)
            log.info("postmortem_pipeline_started")

            try:
                await self.incidents.add_timeline_event(
                    incident.incident_id,
                    "Postmortem agent generating draft",
                    TimelineEventType.AGENT_POSTMORTEM,
                    actor="postmortem-agent",
                )

                result = await self.postmortem_agent.run(incident)

                if result.get("postmortem_created"):
                    try:
                        await self.incidents.transition(
                            incident.incident_id,
                            IncidentStatus.POSTMORTEM,
                            reason="Postmortem draft generated",
                            actor="postmortem-agent",
                        )
                    except Exception:
                        log.warning("postmortem_transition_failed")

                # Index resolved incident in knowledge base
                if self.knowledge_base:
                    try:
                        await self.knowledge_base.learn_from_resolution(incident)
                        log.info("kb_incident_indexed", incident_id=incident.incident_id)
                    except Exception:
                        log.exception("kb_indexing_error")

                # Dispatch outbound webhooks
                if self.outbound_webhooks:
                    try:
                        await self.outbound_webhooks.dispatch(
                            "incident.resolved",
                            {"postmortem_created": result.get("postmortem_created", False)},
                            incident,
                        )
                    except Exception:
                        log.exception("outbound_webhook_resolve_error")

                # Slack thread update
                if self.slack_integration and self.slack_integration.enabled:
                    try:
                        await self.slack_integration.update_incident_status_in_thread(
                            incident, "Investigating", "Resolved"
                        )
                    except Exception:
                        log.exception("slack_resolved_thread_error")

                log.info("postmortem_pipeline_completed")

            except Exception:
                log.exception("postmortem_pipeline_error")

    # --- Human-in-the-loop reactions (triggered by NotionWatcher) ---

    async def handle_severity_change(
        self, event: NotionChangeEvent, incident: Incident
    ) -> None:
        """Human upgraded severity in Notion → re-triage with new context."""
        log = logger.bind(incident_id=incident.incident_id)
        log.info(
            "human_severity_change",
            old=event.old_value,
            new=event.new_value,
        )

        # Sync severity to in-memory model
        incident.severity = event.new_value

        await self.incidents.add_timeline_event(
            incident.incident_id,
            f"Human changed severity: {event.old_value} → {event.new_value}. "
            f"Re-running triage agent with updated context.",
            TimelineEventType.MANUAL_ACTION,
            actor="notion-watcher",
        )

        # Build a synthetic alert for re-triage
        from src.webhooks.schemas import AlertStatus

        retriage_alert = UnifiedAlert(
            alert_id=f"retriage-{incident.incident_id}",
            title=incident.title,
            description=incident.description,
            severity=self._map_severity(event.new_value),
            status=AlertStatus.FIRING,
            service=incident.service,
            source=self._map_source(incident.source),
            triggered_at=incident.triggered_at,
            labels=incident.labels,
            annotations=incident.annotations,
        )

        async with self._semaphore:
            try:
                await self.incidents.add_timeline_event(
                    incident.incident_id,
                    "Re-triage started after human severity change",
                    TimelineEventType.AGENT_TRIAGE,
                    actor="triage-agent",
                )
                await self.triage_agent.run(incident, retriage_alert)
                log.info("retriage_completed")
            except Exception:
                log.exception("retriage_error")

        # Send Slack notification about severity change
        if self.config.SLACK_WEBHOOK_URL:
            asyncio.create_task(
                send_slack_notification(
                    self.config.SLACK_WEBHOOK_URL,
                    self.config.SLACK_CHANNEL,
                    incident,
                    notify_type=f"severity changed to {event.new_value}",
                )
            )

    async def handle_status_change(
        self, event: NotionChangeEvent, incident: Incident
    ) -> None:
        """Human changed status in Notion → sync and trigger appropriate flow."""
        log = logger.bind(incident_id=incident.incident_id)
        log.info(
            "human_status_change",
            old=event.old_value,
            new=event.new_value,
        )

        new_status_str = event.new_value
        try:
            new_status = IncidentStatus(new_status_str)
        except ValueError:
            log.warning("unknown_status_from_notion", status=new_status_str)
            return

        # Skip if already in sync
        if incident.status == new_status:
            return

        await self.incidents.add_timeline_event(
            incident.incident_id,
            f"Human changed status in Notion: {event.old_value} → {new_status_str}",
            TimelineEventType.MANUAL_ACTION,
            actor="notion-watcher",
        )

        # Force-sync the status (bypass FSM since human override)
        old_status = incident.status
        incident.status = new_status

        if new_status == IncidentStatus.RESOLVED:
            # Human resolved in Notion → trigger postmortem
            from datetime import datetime, timezone

            incident.resolved_at = datetime.now(timezone.utc)
            if incident.triggered_at:
                incident.duration_seconds = int(
                    (incident.resolved_at - incident.triggered_at).total_seconds()
                )

            log.info("human_resolved_in_notion")
            asyncio.create_task(self.handle_incident_resolved(incident))

            # Send resolved Slack notification
            if self.config.SLACK_WEBHOOK_URL:
                asyncio.create_task(
                    send_slack_notification(
                        self.config.SLACK_WEBHOOK_URL,
                        self.config.SLACK_CHANNEL,
                        incident,
                        notify_type="resolved",
                    )
                )

        # Broadcast the change to WebSocket clients
        await self.incidents._broadcast(
            "incident_updated",
            {
                "incident_id": incident.incident_id,
                "old_status": old_status.value,
                "new_status": new_status.value,
                "reason": "Human updated in Notion",
            },
        )

    async def handle_root_cause_added(
        self, event: NotionChangeEvent, incident: Incident
    ) -> None:
        """Human added root cause in Notion → trigger postmortem agent."""
        log = logger.bind(incident_id=incident.incident_id)
        log.info("human_root_cause_added", root_cause=event.new_value[:100])

        # Sync to in-memory model
        incident.root_cause = event.new_value

        await self.incidents.add_timeline_event(
            incident.incident_id,
            f"Human added root cause via Notion: {event.new_value[:200]}. "
            f"Triggering postmortem agent.",
            TimelineEventType.MANUAL_ACTION,
            actor="notion-watcher",
        )

        # If incident is still active, this is a strong signal — trigger postmortem
        asyncio.create_task(self.handle_incident_resolved(incident))

    async def handle_escalation(
        self, event: NotionChangeEvent, incident: Incident
    ) -> None:
        """Human wrote ESCALATE comment in Notion → trigger escalation flow."""
        log = logger.bind(incident_id=incident.incident_id)
        log.info("escalation_triggered", comment=event.new_value[:100])

        await self.incidents.add_timeline_event(
            incident.incident_id,
            f"ESCALATION requested via Notion comment: {event.new_value[:200]}",
            TimelineEventType.ESCALATION,
            actor="notion-watcher",
        )

        # Upgrade severity if not already P0
        if incident.severity != "P0-Critical":
            old_sev = incident.severity
            incident.severity = "P0-Critical"
            await self.incidents.add_timeline_event(
                incident.incident_id,
                f"Auto-escalated severity: {old_sev} → P0-Critical",
                TimelineEventType.ESCALATION,
                actor="escalation-handler",
            )
            # Update Notion
            if incident.notion_page_id:
                try:
                    await self.notion.update_page(
                        incident.notion_page_id,
                        properties={
                            "Severity": {"select": {"name": "P0-Critical"}},
                        },
                    )
                except Exception:
                    log.exception("escalation_severity_update_error")

        # Send urgent Slack notification
        if self.config.SLACK_WEBHOOK_URL:
            asyncio.create_task(
                send_slack_notification(
                    self.config.SLACK_WEBHOOK_URL,
                    self.config.SLACK_CHANNEL,
                    incident,
                    notify_type="ESCALATED",
                )
            )

        # Re-run remediation with escalation context
        async with self._semaphore:
            try:
                await self.incidents.add_timeline_event(
                    incident.incident_id,
                    "Re-running remediation agent with escalation context",
                    TimelineEventType.AGENT_REMEDIATION,
                    actor="remediation-agent",
                )
                await self.remediation_agent.run(
                    incident, None, {}, {"escalated": True}
                )
                log.info("escalation_remediation_completed")
            except Exception:
                log.exception("escalation_remediation_error")

    # --- Helpers ---

    @staticmethod
    def _map_severity(severity_str: str) -> Any:
        """Map a Notion severity string to a UnifiedAlert Severity enum."""
        from src.webhooks.schemas import Severity

        mapping = {
            "P0-Critical": Severity.P0,
            "P1-High": Severity.P1,
            "P2-Medium": Severity.P2,
            "P3-Low": Severity.P3,
        }
        return mapping.get(severity_str, Severity.P2)

    @staticmethod
    def _map_source(source_str: str) -> Any:
        """Map a source string to an AlertSource enum."""
        from src.webhooks.schemas import AlertSource

        mapping = {
            "prometheus": AlertSource.PROMETHEUS,
            "grafana": AlertSource.GRAFANA,
            "pagerduty": AlertSource.PAGERDUTY,
            "manual": AlertSource.MANUAL,
            "generic": AlertSource.GENERIC,
        }
        return mapping.get(source_str, AlertSource.GENERIC)
