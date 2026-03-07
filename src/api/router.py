"""REST API routes for the dashboard."""

from datetime import datetime, timezone
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

logger = structlog.get_logger()

from src.api.schemas import (
    CommentRequest,
    IncidentDetailResponse,
    IncidentResponse,
    StatsResponse,
    TransitionRequest,
)
from src.incidents.manager import IncidentManager
from src.incidents.models import IncidentStatus, TimelineEventType
from src.incidents.state_machine import InvalidTransition

router = APIRouter(prefix="/api", tags=["api"])

# Set by main.py
_incident_manager: IncidentManager | None = None
_orchestrator = None
_notion_watcher = None
_alert_handler = None


def set_alert_handler(handler) -> None:
    """Set the alert handler closure from main.py lifespan."""
    global _alert_handler
    _alert_handler = handler


def set_dependencies(incident_manager: IncidentManager, orchestrator=None, notion_watcher=None) -> None:
    global _incident_manager, _orchestrator, _notion_watcher
    _incident_manager = incident_manager
    _orchestrator = orchestrator
    _notion_watcher = notion_watcher


def _get_manager() -> IncidentManager:
    if _incident_manager is None:
        raise HTTPException(500, "Incident manager not initialized")
    return _incident_manager


@router.get("/incidents", response_model=list[IncidentResponse])
async def list_incidents(
    status: str | None = None,
    severity: str | None = None,
    service: str | None = None,
):
    """List incidents with optional filters."""
    manager = _get_manager()
    incidents = manager.get_all_incidents()

    if status:
        incidents = [i for i in incidents if i.status.value == status]
    if severity:
        incidents = [i for i in incidents if i.severity == severity]
    if service:
        incidents = [i for i in incidents if i.service == service]

    # Sort: active first, then by triggered_at descending
    incidents.sort(
        key=lambda i: (
            i.status in {IncidentStatus.RESOLVED, IncidentStatus.POSTMORTEM},
            -(i.triggered_at.timestamp()),
        )
    )
    return [IncidentResponse(**i.model_dump()) for i in incidents]


@router.get("/incidents/active", response_model=list[IncidentResponse])
async def list_active_incidents():
    """Get only active (non-resolved) incidents."""
    manager = _get_manager()
    incidents = manager.get_active_incidents()
    incidents.sort(key=lambda i: i.triggered_at, reverse=True)
    return [IncidentResponse(**i.model_dump()) for i in incidents]


@router.get("/incidents/stats", response_model=StatsResponse)
async def get_stats():
    """Get incident metrics."""
    manager = _get_manager()
    return StatsResponse(**manager.get_stats())


@router.get("/incidents/{incident_id}", response_model=IncidentDetailResponse)
async def get_incident(incident_id: str):
    """Get detailed incident info."""
    manager = _get_manager()
    incident = manager.get_incident(incident_id)
    if not incident:
        raise HTTPException(404, f"Incident {incident_id} not found")

    data = incident.model_dump()
    data["timeline"] = [e.model_dump(mode="json") for e in incident.timeline]
    return IncidentDetailResponse(**data)


@router.get("/incidents/{incident_id}/timeline")
async def get_timeline(incident_id: str):
    """Get incident timeline events."""
    manager = _get_manager()
    incident = manager.get_incident(incident_id)
    if not incident:
        raise HTTPException(404, f"Incident {incident_id} not found")

    return [e.model_dump(mode="json") for e in incident.timeline]


@router.post("/incidents/{incident_id}/transition", response_model=IncidentResponse)
async def transition_incident(incident_id: str, req: TransitionRequest):
    """Manually transition an incident to a new status."""
    manager = _get_manager()

    try:
        new_status = IncidentStatus(req.new_status)
    except ValueError:
        raise HTTPException(
            400, f"Invalid status: {req.new_status}. Valid: {[s.value for s in IncidentStatus]}"
        )

    try:
        incident = await manager.transition(
            incident_id, new_status, reason=req.reason, actor=req.actor
        )
    except ValueError as e:
        raise HTTPException(404, str(e))
    except InvalidTransition as e:
        raise HTTPException(400, str(e))

    # Trigger postmortem agent on resolution
    if new_status == IncidentStatus.RESOLVED and _orchestrator:
        import asyncio

        asyncio.create_task(_orchestrator.handle_incident_resolved(incident))

    return IncidentResponse(**incident.model_dump())


@router.post("/incidents/{incident_id}/comment")
async def add_comment(incident_id: str, req: CommentRequest):
    """Add a manual comment to an incident."""
    manager = _get_manager()
    try:
        await manager.add_timeline_event(
            incident_id,
            req.comment,
            event_type=TimelineEventType.MANUAL_ACTION,
            actor=req.actor,
        )
    except ValueError as e:
        raise HTTPException(404, str(e))
    return {"status": "ok"}


@router.get("/services")
async def list_services():
    """List known services (from incidents)."""
    manager = _get_manager()
    services = set()
    for inc in manager.get_all_incidents():
        services.add(inc.service)
    return sorted(services)


@router.get("/runbooks")
async def list_runbooks():
    """Placeholder — runbooks are managed in Notion."""
    return {"message": "Runbooks are managed in the Notion Runbooks database"}


@router.post("/sync/poll")
async def trigger_sync_poll():
    """Manually trigger a Notion sync poll (checks for human edits now)."""
    if not _notion_watcher:
        raise HTTPException(503, "Notion watcher not initialized")
    await _notion_watcher._poll_once()
    return {"status": "ok", "message": "Sync poll completed"}


@router.post("/incidents/{incident_id}/sync")
async def sync_incident_from_notion(incident_id: str):
    """Force sync a single incident from Notion (detect human edits now)."""
    manager = _get_manager()
    incident = manager.get_incident(incident_id)
    if not incident:
        raise HTTPException(404, f"Incident {incident_id} not found")
    if not incident.notion_page_id:
        raise HTTPException(400, "Incident has no linked Notion page")
    if not _notion_watcher:
        raise HTTPException(503, "Notion watcher not initialized")

    await _notion_watcher._check_incident(incident)
    return {
        "status": "ok",
        "incident_id": incident_id,
        "notion_page_id": incident.notion_page_id,
    }


# ── Webhook Playground ──────────────────────────────────────────────


class PlaygroundRequest(BaseModel):
    source: str  # alertmanager, grafana, pagerduty, generic, manual
    payload: dict[str, Any]


@router.post("/playground/test")
async def playground_test(req: PlaygroundRequest):
    """Dry-run a webhook payload: normalize it without creating an incident."""
    from src.webhooks.normalizer import (
        normalize_alertmanager,
        normalize_generic,
        normalize_grafana,
        normalize_manual,
        normalize_pagerduty,
    )
    from src.webhooks.schemas import (
        AlertManagerWebhook,
        GenericAlert,
        GrafanaWebhook,
        ManualIncident,
        PagerDutyWebhook,
    )

    try:
        if req.source == "alertmanager":
            webhook = AlertManagerWebhook.model_validate(req.payload)
            alerts = normalize_alertmanager(webhook)
        elif req.source == "grafana":
            webhook = GrafanaWebhook.model_validate(req.payload)
            alerts = normalize_grafana(webhook)
        elif req.source == "pagerduty":
            webhook = PagerDutyWebhook.model_validate(req.payload)
            alerts = normalize_pagerduty(webhook)
        elif req.source == "generic":
            alert = GenericAlert.model_validate(req.payload)
            alerts = normalize_generic(alert)
        elif req.source == "manual":
            incident = ManualIncident.model_validate(req.payload)
            alerts = normalize_manual(incident)
        else:
            raise HTTPException(400, f"Unknown source: {req.source}")

        # Check dedup
        manager = _get_manager()
        results = []
        for a in alerts:
            dedup_key = f"{a.service}:{a.title}"
            is_duplicate = any(
                inc.title == a.title and inc.service == a.service
                and inc.status.value not in ("Resolved", "Postmortem")
                for inc in manager.get_all_incidents()
            )
            results.append({
                "alert_id": a.alert_id,
                "title": a.title,
                "severity": a.severity.value if hasattr(a.severity, "value") else str(a.severity),
                "service": a.service,
                "source": a.source.value if hasattr(a.source, "value") else str(a.source),
                "status": a.status.value if hasattr(a.status, "value") else str(a.status),
                "description": a.description[:300],
                "labels": a.labels,
                "is_duplicate": is_duplicate,
                "dedup_key": dedup_key,
            })

        return {
            "status": "ok",
            "source": req.source,
            "alerts_parsed": len(results),
            "alerts": results,
            "validation": "passed",
        }
    except HTTPException:
        raise
    except Exception as e:
        return {
            "status": "error",
            "source": req.source,
            "alerts_parsed": 0,
            "alerts": [],
            "validation": "failed",
            "error": str(e),
            "error_type": type(e).__name__,
        }


@router.post("/playground/send")
async def playground_send(req: PlaygroundRequest):
    """Send a webhook payload live — normalize and create a real incident."""
    from src.webhooks.normalizer import (
        normalize_alertmanager,
        normalize_generic,
        normalize_grafana,
        normalize_manual,
        normalize_pagerduty,
    )
    from src.webhooks.schemas import (
        AlertManagerWebhook,
        GenericAlert,
        GrafanaWebhook,
        ManualIncident,
        PagerDutyWebhook,
    )

    try:
        if req.source == "alertmanager":
            webhook = AlertManagerWebhook.model_validate(req.payload)
            alerts = normalize_alertmanager(webhook)
        elif req.source == "grafana":
            webhook = GrafanaWebhook.model_validate(req.payload)
            alerts = normalize_grafana(webhook)
        elif req.source == "pagerduty":
            webhook = PagerDutyWebhook.model_validate(req.payload)
            alerts = normalize_pagerduty(webhook)
        elif req.source == "generic":
            alert = GenericAlert.model_validate(req.payload)
            alerts = normalize_generic(alert)
        elif req.source == "manual":
            incident = ManualIncident.model_validate(req.payload)
            alerts = normalize_manual(incident)
        else:
            raise HTTPException(400, f"Unknown source: {req.source}")

        if _alert_handler is None:
            raise HTTPException(503, "Alert handler not initialized")
        for a in alerts:
            await _alert_handler(a)

        return {"status": "accepted", "alerts": len(alerts)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Failed to process: {e}")


# ── Agent Audit Trail ────────────────────────────────────────────────


@router.get("/audit-trail")
async def get_audit_trail(incident_id: str | None = None):
    """Get agent audit trail — all timeline events across incidents or for one."""
    manager = _get_manager()

    if incident_id:
        incident = manager.get_incident(incident_id)
        if not incident:
            raise HTTPException(404, f"Incident {incident_id} not found")
        incidents = [incident]
    else:
        incidents = manager.get_all_incidents()

    trail = []
    for inc in incidents:
        for event in inc.timeline:
            trail.append({
                "incident_id": inc.incident_id,
                "incident_title": inc.title,
                "severity": inc.severity,
                "timestamp": event.timestamp.isoformat(),
                "event_type": event.event_type.value,
                "actor": event.actor,
                "message": event.message,
                "is_agent": event.event_type.value.startswith("agent_")
                    or event.actor in (
                        "triage-agent", "correlation-agent",
                        "remediation-agent", "postmortem-agent",
                        "comms-agent", "orchestrator",
                        "github-integration", "knowledge-base",
                    ),
                "is_mcp_call": any(
                    kw in event.message.lower()
                    for kw in ("notion", "mcp", "search", "runbook", "database")
                ),
            })

    trail.sort(key=lambda x: x["timestamp"], reverse=True)
    return trail


@router.get("/audit-trail/{incident_id}/replay")
async def get_incident_replay(incident_id: str):
    """Get a structured replay of agent actions for a single incident."""
    manager = _get_manager()
    incident = manager.get_incident(incident_id)
    if not incident:
        raise HTTPException(404, f"Incident {incident_id} not found")

    events = sorted(incident.timeline, key=lambda e: e.timestamp)
    if not events:
        return {"incident_id": incident_id, "steps": [], "total_duration_ms": 0}

    start_time = events[0].timestamp
    steps = []
    for i, event in enumerate(events):
        elapsed_ms = int((event.timestamp - start_time).total_seconds() * 1000)
        step_duration_ms = 0
        if i + 1 < len(events):
            step_duration_ms = int(
                (events[i + 1].timestamp - event.timestamp).total_seconds() * 1000
            )

        phase = "system"
        if "triage" in event.event_type.value or event.actor == "triage-agent":
            phase = "triage"
        elif "correlation" in event.event_type.value or event.actor == "correlation-agent":
            phase = "correlation"
        elif "remediation" in event.event_type.value or event.actor == "remediation-agent":
            phase = "remediation"
        elif "postmortem" in event.event_type.value or event.actor == "postmortem-agent":
            phase = "postmortem"
        elif event.actor == "comms-agent":
            phase = "communications"
        elif event.actor in ("github-integration", "knowledge-base"):
            phase = "enrichment"
        elif event.event_type.value == "status_change":
            phase = "transition"

        steps.append({
            "step": i + 1,
            "timestamp": event.timestamp.isoformat(),
            "elapsed_ms": elapsed_ms,
            "duration_ms": step_duration_ms,
            "phase": phase,
            "actor": event.actor,
            "event_type": event.event_type.value,
            "message": event.message,
        })

    total_ms = int((events[-1].timestamp - start_time).total_seconds() * 1000)
    return {
        "incident_id": incident_id,
        "incident_title": incident.title,
        "severity": incident.severity,
        "status": incident.status.value,
        "steps": steps,
        "total_duration_ms": total_ms,
        "total_steps": len(steps),
    }


# ── Semantic Search ──────────────────────────────────────────────────

_notion_tools = None
_knowledge_base = None


def set_search_dependencies(notion_tools=None, knowledge_base=None) -> None:
    global _notion_tools, _knowledge_base
    _notion_tools = notion_tools
    _knowledge_base = knowledge_base


class SearchRequest(BaseModel):
    query: str
    scope: str = "all"  # all, incidents, notion, knowledge_base


@router.post("/search")
async def semantic_search(req: SearchRequest):
    """Unified semantic search across incidents, Notion, and knowledge base."""
    import json as _json

    results = {
        "query": req.query,
        "incidents": [],
        "notion": [],
        "knowledge_base": [],
    }

    # 1. Search in-memory incidents (title + description match)
    if req.scope in ("all", "incidents"):
        manager = _get_manager()
        query_lower = req.query.lower()
        for inc in manager.get_all_incidents():
            score = 0
            title_lower = inc.title.lower()
            desc_lower = inc.description.lower()
            # Simple relevance scoring
            if query_lower in title_lower:
                score += 0.9
            if query_lower in desc_lower:
                score += 0.5
            # Check individual words
            for word in query_lower.split():
                if len(word) > 2:
                    if word in title_lower:
                        score += 0.3
                    if word in desc_lower:
                        score += 0.15
                    if word in inc.service.lower():
                        score += 0.2
                    # Check timeline
                    for event in inc.timeline:
                        if word in event.message.lower():
                            score += 0.05
                            break
            if score > 0.1:
                results["incidents"].append({
                    "incident_id": inc.incident_id,
                    "title": inc.title,
                    "severity": inc.severity,
                    "status": inc.status.value,
                    "service": inc.service,
                    "description": inc.description[:200],
                    "score": round(min(score, 1.0), 2),
                    "source": "incidents",
                })
        results["incidents"].sort(key=lambda x: x["score"], reverse=True)
        results["incidents"] = results["incidents"][:10]

    # 2. Search Notion workspace via MCP
    if req.scope in ("all", "notion") and _notion_tools:
        try:
            raw = await _notion_tools.search(req.query)
            try:
                data = _json.loads(raw)
                pages = data.get("results", [])
                for page in pages[:10]:
                    title_parts = []
                    props = page.get("properties", {})
                    # Extract title from any title property
                    for prop in props.values():
                        if prop.get("type") == "title":
                            for t in prop.get("title", []):
                                title_parts.append(t.get("plain_text", ""))
                    title = " ".join(title_parts) or "Untitled"

                    page_type = page.get("object", "page")
                    parent = page.get("parent", {})
                    parent_type = parent.get("type", "")

                    results["notion"].append({
                        "page_id": page.get("id", ""),
                        "title": title,
                        "type": page_type,
                        "parent_type": parent_type,
                        "url": page.get("url", ""),
                        "last_edited": page.get("last_edited_time", ""),
                        "source": "notion",
                    })
            except (ValueError, TypeError):
                # Non-JSON response, extract what we can
                if raw and len(raw) > 10:
                    results["notion"].append({
                        "page_id": "",
                        "title": f"Search results for: {req.query}",
                        "type": "raw",
                        "parent_type": "",
                        "url": "",
                        "last_edited": "",
                        "source": "notion",
                        "raw_preview": raw[:500],
                    })
        except Exception as e:
            results["notion"].append({
                "error": str(e),
                "source": "notion",
            })

    # 3. Search knowledge base (semantic embeddings)
    if req.scope in ("all", "knowledge_base") and _knowledge_base:
        try:
            kb_results = await _knowledge_base.search(req.query, top_k=5)
            for r in kb_results:
                results["knowledge_base"].append({
                    "title": r.get("title", ""),
                    "content": r.get("content", "")[:300],
                    "score": round(r.get("score", 0), 2),
                    "doc_type": r.get("doc_type", ""),
                    "source": "knowledge_base",
                })
        except Exception as e:
            results["knowledge_base"].append({
                "error": str(e),
                "source": "knowledge_base",
            })

    total = (
        len(results["incidents"])
        + len(results["notion"])
        + len(results["knowledge_base"])
    )
    results["total_results"] = total
    return results


# ── Incident Commander ───────────────────────────────────────────────

_commander = None


def set_commander(commander) -> None:
    global _commander
    _commander = commander


class CommanderRequest(BaseModel):
    message: str
    conversation_id: str = ""


# In-memory conversation store (per incident)
_commander_conversations: dict[str, list[dict]] = {}


@router.post("/incidents/{incident_id}/commander")
async def commander_query(incident_id: str, req: CommanderRequest):
    """Send a query to the Incident Commander for a specific incident."""
    if not _commander:
        raise HTTPException(503, "Incident Commander not initialized")

    manager = _get_manager()
    incident = manager.get_incident(incident_id)
    if not incident:
        raise HTTPException(404, f"Incident {incident_id} not found")

    # Get or create conversation
    conv_key = f"{incident_id}:{req.conversation_id}" if req.conversation_id else incident_id
    history = _commander_conversations.get(conv_key, [])

    try:
        response = await _commander.query(
            incident=incident,
            user_message=req.message,
            conversation_history=history,
        )

        # Update conversation history
        history.append({"role": "user", "content": req.message})
        history.append({"role": "assistant", "content": response})
        # Keep last 20 messages to avoid context overflow
        _commander_conversations[conv_key] = history[-20:]

        return {
            "response": response,
            "incident_id": incident_id,
            "conversation_id": conv_key,
        }
    except Exception as e:
        logger.exception("commander_error", incident_id=incident_id)
        raise HTTPException(500, f"Commander error: {e}")


@router.delete("/incidents/{incident_id}/commander/history")
async def clear_commander_history(incident_id: str):
    """Clear conversation history for an incident's commander."""
    keys_to_remove = [k for k in _commander_conversations if k.startswith(incident_id)]
    for k in keys_to_remove:
        del _commander_conversations[k]
    return {"cleared": len(keys_to_remove)}
