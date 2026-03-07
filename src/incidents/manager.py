"""Core incident lifecycle manager."""

import asyncio
import json as _json
import re
import time
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine

import structlog

from src.agents.alert_grouping import AlertGrouper
from src.config import OpsLensConfig
from src.incidents.models import (
    Incident,
    IncidentStatus,
    TimelineEvent,
    TimelineEventType,
)
from src.incidents.state_machine import InvalidTransition, execute_transition
from src.incidents.timeline import create_event, format_timeline_comment
from src.notion_mcp.templates import incident_page_content
from src.notion_mcp.tools import NotionMCPTools
from src.webhooks.schemas import UnifiedAlert

logger = structlog.get_logger()


class IncidentManager:
    """
    Core incident lifecycle manager.
    Maintains in-memory state synchronized with Notion.
    """

    def __init__(
        self,
        config: OpsLensConfig,
        notion_tools: NotionMCPTools,
    ):
        self.config = config
        self.notion = notion_tools
        self._incidents: dict[str, Incident] = {}
        self._counter = 0
        self._dedup_cache: dict[str, tuple[str, float]] = {}  # fingerprint -> (incident_id, timestamp)
        self._ws_broadcast: Callable | None = None
        self._alert_grouper = AlertGrouper(
            group_window_seconds=config.DEDUP_WINDOW_SECONDS * 2
        )

    def set_ws_broadcast(self, broadcast_fn: Callable) -> None:
        """Set the WebSocket broadcast function."""
        self._ws_broadcast = broadcast_fn

    async def rehydrate_from_notion(self) -> int:
        """Load existing incidents from Notion database on startup."""
        ds_id = self.config.NOTION_INCIDENTS_DS_ID
        if not ds_id:
            logger.warning("rehydrate_skipped", reason="No incidents DS ID configured")
            return 0

        try:
            raw = await self.notion.query_database(ds_id, page_size=100)
            text = self.notion._extract_text(raw)
            data = _json.loads(text)

            results = data.get("results", [])
            if not results:
                logger.info("rehydrate_empty", msg="No incidents in Notion DB")
                return 0

            loaded = 0
            for page in results:
                try:
                    props = page.get("properties", {})
                    page_id = page.get("id", "")

                    # Extract incident ID
                    inc_id_prop = props.get("Incident ID", {})
                    rich_text = inc_id_prop.get("rich_text", [])
                    incident_id = rich_text[0]["text"]["content"] if rich_text else ""
                    if not incident_id:
                        continue

                    # Extract title
                    name_prop = props.get("Name", {})
                    title_parts = name_prop.get("title", [])
                    full_title = title_parts[0]["text"]["content"] if title_parts else ""
                    # Strip "[OPSLENS-XXXX] " prefix from title
                    title = re.sub(r"^\[OPSLENS-\d+\]\s*", "", full_title)

                    # Extract status
                    status_prop = props.get("Status", {})
                    status_name = (status_prop.get("select") or {}).get("name", "Triggered")
                    try:
                        status = IncidentStatus(status_name)
                    except ValueError:
                        status = IncidentStatus.TRIGGERED

                    # Extract severity
                    sev_prop = props.get("Severity", {})
                    severity = (sev_prop.get("select") or {}).get("name", "P3-Low")

                    # Extract source
                    source_prop = props.get("Alert Source", {})
                    source = (source_prop.get("select") or {}).get("name", "generic")

                    # Extract triggered_at
                    date_prop = props.get("Triggered At", {})
                    date_val = (date_prop.get("date") or {}).get("start", "")
                    triggered_at = (
                        datetime.fromisoformat(date_val) if date_val
                        else datetime.fromisoformat(page.get("created_time", datetime.now(timezone.utc).isoformat()))
                    )

                    # Extract description/impact
                    impact_prop = props.get("Impact", {})
                    impact_rt = impact_prop.get("rich_text", [])
                    description = impact_rt[0]["text"]["content"] if impact_rt else ""

                    # Service — stored as "Service Name" rich_text property
                    service = "unknown"
                    svc_prop = props.get("Service Name", {})
                    svc_rt = svc_prop.get("rich_text", [])
                    if svc_rt:
                        service = svc_rt[0].get("text", {}).get("content", "unknown")

                    incident = Incident(
                        incident_id=incident_id,
                        title=title,
                        description=description,
                        severity=severity,
                        status=status,
                        service=service,
                        source=source.lower(),
                        triggered_at=triggered_at,
                        notion_page_id=page_id,
                    )

                    self._incidents[incident_id] = incident

                    # Update counter to avoid ID collisions
                    id_match = re.search(r"OPSLENS-(\d+)", incident_id)
                    if id_match:
                        num = int(id_match.group(1))
                        if num >= self._counter:
                            self._counter = num

                    loaded += 1
                except Exception:
                    logger.exception("rehydrate_page_error", page_id=page.get("id"))

            # Load comments (timeline/agent actions) for each unique incident
            for incident in self._incidents.values():
                if not incident.notion_page_id:
                    continue
                try:
                    raw_comments = await self.notion.list_comments(incident.notion_page_id)
                    comments_text = self.notion._extract_text(raw_comments)
                    comments_data = _json.loads(comments_text)
                    for comment in comments_data.get("results", []):
                        rich_text = comment.get("rich_text", [])
                        if not rich_text:
                            continue
                        text = rich_text[0].get("plain_text", "")
                        if not text:
                            continue
                        event = self._parse_comment_to_event(text, comment.get("created_time", ""))
                        if event:
                            incident.timeline.append(event)
                    # Update agent_actions_count
                    incident.agent_actions_count = sum(
                        1 for e in incident.timeline
                        if e.event_type.value.startswith("agent_")
                    )
                except Exception:
                    logger.debug("rehydrate_comments_error", incident_id=incident.incident_id)

            logger.info("rehydrate_complete", loaded=loaded, total_in_notion=len(results))
            return loaded
        except Exception:
            logger.exception("rehydrate_failed")
            return 0

    @staticmethod
    def _parse_comment_to_event(text: str, created_time: str) -> TimelineEvent | None:
        """Parse a Notion comment back into a TimelineEvent."""
        # Format: [2026-03-07 07:22:18 UTC] 🔍 [agent_triage] Message (by actor)
        m = re.match(
            r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC)\]\s*\S+\s*\[(\w+)\]\s*(.*?)(?:\s*\(by\s+(.*?)\))?\s*$",
            text,
            re.DOTALL,
        )
        if m:
            ts_str, event_type_str, message, actor = m.groups()
            try:
                ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S %Z").replace(tzinfo=timezone.utc)
            except ValueError:
                ts = datetime.fromisoformat(created_time) if created_time else datetime.now(timezone.utc)
            try:
                evt_type = TimelineEventType(event_type_str)
            except ValueError:
                evt_type = TimelineEventType.COMMENT
            return TimelineEvent(
                timestamp=ts,
                event_type=evt_type,
                message=message.strip(),
                actor=actor.strip() if actor else "system",
            )
        # Fallback: treat as a plain comment
        if text.strip():
            ts = datetime.fromisoformat(created_time) if created_time else datetime.now(timezone.utc)
            return TimelineEvent(
                timestamp=ts,
                event_type=TimelineEventType.COMMENT,
                message=text.strip()[:500],
                actor="system",
            )
        return None

    async def _broadcast(self, event_type: str, data: dict[str, Any]) -> None:
        """Broadcast event to WebSocket clients."""
        if self._ws_broadcast:
            try:
                await self._ws_broadcast({"type": event_type, "data": data})
            except Exception:
                logger.exception("ws_broadcast_error")

    def _next_id(self) -> str:
        self._counter += 1
        return f"OPSLENS-{self._counter:04d}"

    def _is_duplicate(self, fingerprint: str) -> str | None:
        """Check if this alert is a duplicate within the dedup window."""
        if fingerprint in self._dedup_cache:
            incident_id, ts = self._dedup_cache[fingerprint]
            if time.monotonic() - ts < self.config.DEDUP_WINDOW_SECONDS:
                return incident_id
            else:
                del self._dedup_cache[fingerprint]
        return None

    async def create_incident(self, alert: UnifiedAlert) -> Incident:
        """
        Create a new incident from a normalized alert.
        Handles deduplication and Notion page creation.
        """
        # Check dedup (exact fingerprint match)
        existing_id = self._is_duplicate(alert.fingerprint)
        if existing_id:
            logger.info(
                "duplicate_alert",
                alert_id=alert.alert_id,
                existing_incident=existing_id,
            )
            existing = self._incidents.get(existing_id)
            if existing:
                event = create_event(
                    TimelineEventType.COMMENT,
                    f"Duplicate alert received: {alert.title}",
                )
                existing.timeline.append(event)
                try:
                    await self.notion.add_comment(
                        existing.notion_page_id,
                        format_timeline_comment(event),
                    )
                except Exception:
                    logger.exception("notion_comment_error", incident_id=existing_id)
                return existing

        # Smart alert grouping: check if this alert should join an existing incident
        grouped_incident = self._alert_grouper.find_group(
            alert, self.get_active_incidents()
        )
        if grouped_incident:
            comment = self._alert_grouper.format_grouped_alert_comment(alert)
            event = create_event(
                TimelineEventType.COMMENT,
                f"Related alert grouped: {alert.title}",
            )
            grouped_incident.timeline.append(event)
            try:
                await self.notion.add_comment(grouped_incident.notion_page_id, comment)
            except Exception:
                logger.exception(
                    "notion_group_comment_error",
                    incident_id=grouped_incident.incident_id,
                )
            await self._broadcast(
                "alert_grouped",
                {
                    "incident_id": grouped_incident.incident_id,
                    "alert_title": alert.title,
                },
            )
            return grouped_incident

        # Generate ID
        incident_id = self._next_id()
        log = logger.bind(incident_id=incident_id)

        # Create Notion page
        content = incident_page_content(
            incident_id=incident_id,
            title=alert.title,
            severity=alert.severity.value,
            service=alert.service,
            source=alert.source.value,
            description=alert.description,
            triggered_at=alert.triggered_at.isoformat(),
            source_url=alert.source_url,
            dashboard_url=alert.dashboard_url,
            runbook_url=alert.runbook_url,
            labels=alert.labels,
        )

        # Build Notion-API-formatted properties
        source_name = alert.source.value.capitalize()
        if source_name == "Prometheus":
            pass  # already correct
        elif source_name == "Generic":
            pass
        # Map to valid option names in our database
        source_map = {
            "prometheus": "Prometheus",
            "grafana": "Grafana",
            "pagerduty": "PagerDuty",
            "manual": "Manual",
            "generic": "Generic",
        }
        notion_source = source_map.get(alert.source.value, "Generic")

        properties = {
            "Name": {"title": [{"text": {"content": f"[{incident_id}] {alert.title}"}}]},
            "Incident ID": {"rich_text": [{"text": {"content": incident_id}}]},
            "Status": {"select": {"name": "Triggered"}},
            "Severity": {"select": {"name": alert.severity.value}},
            "Alert Source": {"select": {"name": notion_source}},
            "Triggered At": {"date": {"start": alert.triggered_at.isoformat()}},
            "Impact": {"rich_text": [{"text": {"content": alert.description[:2000]}}]},
        }

        notion_page_id = ""
        try:
            notion_result = await self.notion.create_page(
                parent_id=self.config.NOTION_INCIDENTS_DB_ID,
                title=f"[{incident_id}] {alert.title}",
                properties=properties,
            )
            # Extract page ID from MCP result
            result_text = self.notion._extract_text(notion_result)
            log.debug("notion_page_raw_result", result_text=result_text[:500])
            # The API returns JSON with an "id" field
            import json as _json
            try:
                page_data = _json.loads(result_text)
                notion_page_id = page_data.get("id", "")
            except (ValueError, TypeError):
                # Try to find UUID pattern in text
                import re
                uuid_match = re.search(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', result_text)
                if uuid_match:
                    notion_page_id = uuid_match.group(0)
                elif "id" in result_text:
                    for part in result_text.split('"'):
                        if len(part) == 36 and "-" in part:
                            notion_page_id = part
                            break
            log.info("notion_page_created", page_id=notion_page_id)
        except Exception:
            log.exception("notion_page_creation_error")

        # Create incident object
        incident = Incident(
            incident_id=incident_id,
            title=alert.title,
            description=alert.description,
            severity=alert.severity.value,
            status=IncidentStatus.TRIGGERED,
            service=alert.service,
            source=alert.source.value,
            triggered_at=alert.triggered_at,
            notion_page_id=notion_page_id,
            fingerprint=alert.fingerprint,
            labels=alert.labels,
            annotations=alert.annotations,
            source_url=alert.source_url,
            dashboard_url=alert.dashboard_url,
            runbook_url=alert.runbook_url,
            raw_alert=alert.raw_payload,
        )

        # Add creation timeline event
        event = create_event(
            TimelineEventType.CREATED,
            f"Incident created from {alert.source.value} alert: {alert.title}",
        )
        incident.timeline.append(event)

        # Store
        self._incidents[incident_id] = incident
        self._dedup_cache[alert.fingerprint] = (incident_id, time.monotonic())

        # Add timeline comment to Notion
        if notion_page_id:
            try:
                await self.notion.add_comment(
                    notion_page_id, format_timeline_comment(event)
                )
            except Exception:
                log.exception("notion_timeline_comment_error")

        # Broadcast WebSocket event
        await self._broadcast("incident_created", incident.model_dump(mode="json"))

        log.info(
            "incident_created",
            severity=incident.severity,
            service=incident.service,
            source=incident.source,
        )

        return incident

    async def transition(
        self,
        incident_id: str,
        new_status: IncidentStatus,
        reason: str = "",
        actor: str = "system",
    ) -> Incident:
        """Execute a state transition with all side effects."""
        incident = self._incidents.get(incident_id)
        if not incident:
            raise ValueError(f"Incident {incident_id} not found")

        log = logger.bind(incident_id=incident_id)

        # Execute FSM transition (validates)
        new_state = execute_transition(
            incident.status, new_status, incident_id
        )
        old_status = incident.status
        incident.status = new_state

        # Handle resolution
        if new_status == IncidentStatus.RESOLVED:
            incident.resolved_at = datetime.now(timezone.utc)
            duration = (incident.resolved_at - incident.triggered_at).total_seconds()
            incident.duration_seconds = int(duration)

        # Timeline event
        msg = f"Status changed: {old_status.value} → {new_state.value}"
        if reason:
            msg += f" — {reason}"
        event = create_event(TimelineEventType.STATUS_CHANGE, msg, actor=actor)
        incident.timeline.append(event)

        # Update Notion
        if incident.notion_page_id:
            try:
                props: dict[str, Any] = {
                    "Status": {"select": {"name": new_state.value}},
                }
                if incident.resolved_at:
                    props["Resolved At"] = {"date": {"start": incident.resolved_at.isoformat()}}
                await self.notion.update_page(incident.notion_page_id, properties=props)
                await self.notion.add_comment(
                    incident.notion_page_id, format_timeline_comment(event)
                )
            except Exception:
                log.exception("notion_transition_update_error")

        # Broadcast
        await self._broadcast(
            "incident_updated",
            {
                "incident_id": incident_id,
                "old_status": old_status.value,
                "new_status": new_state.value,
                "reason": reason,
            },
        )

        log.info(
            "incident_transitioned",
            old_status=old_status.value,
            new_status=new_state.value,
        )

        return incident

    async def add_timeline_event(
        self,
        incident_id: str,
        message: str,
        event_type: TimelineEventType = TimelineEventType.COMMENT,
        actor: str = "system",
    ) -> None:
        """Add a timeline event and post it as a Notion comment."""
        incident = self._incidents.get(incident_id)
        if not incident:
            raise ValueError(f"Incident {incident_id} not found")

        event = create_event(event_type, message, actor=actor)
        incident.timeline.append(event)
        incident.agent_actions_count += 1

        if incident.notion_page_id:
            try:
                await self.notion.add_comment(
                    incident.notion_page_id, format_timeline_comment(event)
                )
            except Exception:
                logger.exception(
                    "notion_timeline_error", incident_id=incident_id
                )

        await self._broadcast(
            "timeline_event",
            {
                "incident_id": incident_id,
                "event": event.model_dump(mode="json"),
            },
        )

    async def resolve_incident(
        self, incident_id: str, root_cause: str = ""
    ) -> Incident:
        """Resolve an incident and update Notion."""
        incident = self._incidents.get(incident_id)
        if not incident:
            raise ValueError(f"Incident {incident_id} not found")

        if root_cause:
            incident.root_cause = root_cause
            if incident.notion_page_id:
                try:
                    await self.notion.update_page(
                        incident.notion_page_id,
                        properties={
                            "Root Cause": {"rich_text": [{"text": {"content": root_cause[:2000]}}]},
                        },
                    )
                except Exception:
                    logger.exception(
                        "notion_root_cause_update_error",
                        incident_id=incident_id,
                    )

        return await self.transition(
            incident_id, IncidentStatus.RESOLVED, reason=root_cause or "Resolved"
        )

    def get_incident(self, incident_id: str) -> Incident | None:
        """Get a single incident by ID."""
        return self._incidents.get(incident_id)

    def get_active_incidents(self) -> list[Incident]:
        """Get all non-resolved incidents."""
        active_statuses = {
            IncidentStatus.TRIGGERED,
            IncidentStatus.TRIAGED,
            IncidentStatus.INVESTIGATING,
            IncidentStatus.MITIGATED,
        }
        return [
            inc
            for inc in self._incidents.values()
            if inc.status in active_statuses
        ]

    def get_all_incidents(self) -> list[Incident]:
        """Get all incidents."""
        return list(self._incidents.values())

    def get_stats(self) -> dict[str, Any]:
        """Calculate incident metrics."""
        all_inc = list(self._incidents.values())
        resolved = [i for i in all_inc if i.duration_seconds is not None]
        active = self.get_active_incidents()

        # MTTR by severity
        mttr_by_severity: dict[str, float] = {}
        for sev in ["P0-Critical", "P1-High", "P2-Medium", "P3-Low"]:
            sev_resolved = [
                i for i in resolved if i.severity == sev and i.duration_seconds
            ]
            if sev_resolved:
                mttr_by_severity[sev] = sum(
                    i.duration_seconds for i in sev_resolved  # type: ignore
                ) / len(sev_resolved)

        # Count by severity
        by_severity: dict[str, int] = {}
        for inc in all_inc:
            by_severity[inc.severity] = by_severity.get(inc.severity, 0) + 1

        # Count by service
        by_service: dict[str, int] = {}
        for inc in all_inc:
            by_service[inc.service] = by_service.get(inc.service, 0) + 1

        return {
            "total": len(all_inc),
            "active": len(active),
            "resolved": len(resolved),
            "mttr_by_severity": mttr_by_severity,
            "by_severity": by_severity,
            "by_service": by_service,
        }
