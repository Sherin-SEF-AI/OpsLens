"""API routes for enterprise integrations."""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Form, HTTPException
from pydantic import BaseModel

logger = structlog.get_logger()

router = APIRouter(prefix="/api/integrations", tags=["integrations"])

# Dependencies set by main.py
_github = None
_slack = None
_jira = None
_linear = None
_cloud = None
_knowledge_base = None
_outbound_webhooks = None
_incident_manager = None
_orchestrator = None


def set_integration_deps(
    github=None,
    slack=None,
    jira=None,
    linear=None,
    cloud=None,
    knowledge_base=None,
    outbound_webhooks=None,
    incident_manager=None,
    orchestrator=None,
):
    global _github, _slack, _jira, _linear, _cloud, _knowledge_base
    global _outbound_webhooks, _incident_manager, _orchestrator
    _github = github
    _slack = slack
    _jira = jira
    _linear = linear
    _cloud = cloud
    _knowledge_base = knowledge_base
    _outbound_webhooks = outbound_webhooks
    _incident_manager = incident_manager
    _orchestrator = orchestrator


# ---- GitHub ----


class GitHubCorrelateRequest(BaseModel):
    repo: str
    incident_id: str
    window_minutes: int = 30


class GitHubRollbackRequest(BaseModel):
    repo: str
    bad_commit_sha: str
    incident_id: str
    title: str = ""
    body: str = ""


class GitHubWorkflowRequest(BaseModel):
    repo: str
    workflow_id: str
    ref: str = ""
    inputs: dict[str, str] = {}


@router.post("/github/correlate")
async def github_correlate(req: GitHubCorrelateRequest):
    """Correlate GitHub deployments/commits with an incident."""
    if not _github or not _github.enabled:
        raise HTTPException(503, "GitHub integration not configured")

    incident = _incident_manager.get_incident(req.incident_id) if _incident_manager else None
    triggered_at = incident.triggered_at if incident else None

    if not triggered_at:
        from datetime import datetime, timezone
        triggered_at = datetime.now(timezone.utc)

    result = await _github.correlate_with_incident(
        repo=req.repo,
        incident_triggered_at=triggered_at,
        service_name=incident.service if incident else "",
        window_minutes=req.window_minutes,
    )
    return result


@router.post("/github/rollback-pr")
async def github_create_rollback_pr(req: GitHubRollbackRequest):
    """Create a rollback PR for a bad commit."""
    if not _github or not _github.enabled:
        raise HTTPException(503, "GitHub integration not configured")
    return await _github.create_rollback_pr(
        repo=req.repo,
        bad_commit_sha=req.bad_commit_sha,
        incident_id=req.incident_id,
        title=req.title,
        body=req.body,
    )


@router.post("/github/trigger-workflow")
async def github_trigger_workflow(req: GitHubWorkflowRequest):
    """Trigger a GitHub Actions workflow."""
    if not _github or not _github.enabled:
        raise HTTPException(503, "GitHub integration not configured")
    return await _github.trigger_workflow(
        repo=req.repo,
        workflow_id=req.workflow_id,
        ref=req.ref,
        inputs=req.inputs,
    )


@router.get("/github/commits/{repo:path}")
async def github_recent_commits(repo: str, branch: str = "", minutes: int = 60):
    """Get recent commits for a repo."""
    if not _github or not _github.enabled:
        raise HTTPException(503, "GitHub integration not configured")
    return await _github.get_recent_commits(repo, branch=branch, within_minutes=minutes)


@router.get("/github/deployments/{repo:path}")
async def github_recent_deployments(repo: str, minutes: int = 60):
    """Get recent deployments for a repo."""
    if not _github or not _github.enabled:
        raise HTTPException(503, "GitHub integration not configured")
    return await _github.get_recent_deployments(repo, within_minutes=minutes)


# ---- Slack ----


@router.post("/slack/war-room/{incident_id}")
async def slack_create_war_room(incident_id: str):
    """Create a Slack war room channel for an incident."""
    if not _slack or not _slack.enabled:
        raise HTTPException(503, "Slack integration not configured")
    if not _incident_manager:
        raise HTTPException(503, "Incident manager not initialized")

    incident = _incident_manager.get_incident(incident_id)
    if not incident:
        raise HTTPException(404, f"Incident {incident_id} not found")

    return await _slack.create_war_room(incident)


@router.post("/slack/thread-update/{incident_id}")
async def slack_thread_update(incident_id: str, message: str):
    """Post an update to an incident's Slack thread."""
    if not _slack or not _slack.enabled:
        raise HTTPException(503, "Slack integration not configured")
    return await _slack.post_thread_update(incident_id, message)


@router.post("/slack/interaction")
async def slack_interaction(payload: dict[str, Any]):
    """Handle Slack interactive component callbacks."""
    if not _slack:
        raise HTTPException(503, "Slack integration not configured")
    action_info = await _slack.handle_interaction(payload)

    # Process the action
    action = action_info.get("action", "")
    incident_id = action_info.get("incident_id", "")

    if not _incident_manager or not incident_id:
        return {"text": "Unable to process action"}

    incident = _incident_manager.get_incident(incident_id)
    if not incident:
        return {"text": f"Incident {incident_id} not found"}

    from src.incidents.models import IncidentStatus

    if action == "incident_acknowledge":
        try:
            await _incident_manager.transition(
                incident_id, IncidentStatus.INVESTIGATING,
                reason="Acknowledged via Slack", actor=f"slack:{action_info.get('user_id', '')}"
            )
            return {"text": f"Incident {incident_id} acknowledged"}
        except Exception as e:
            return {"text": f"Error: {e}"}

    elif action == "incident_escalate":
        if _orchestrator:
            from src.sync.notion_watcher import NotionChangeEvent
            event = NotionChangeEvent(
                incident_id=incident_id,
                field="comment_escalate",
                old_value="",
                new_value="ESCALATE: Triggered from Slack",
            )
            import asyncio
            asyncio.create_task(_orchestrator.handle_escalation(event, incident))
        return {"text": f"Incident {incident_id} escalated"}

    elif action == "incident_resolve":
        try:
            await _incident_manager.transition(
                incident_id, IncidentStatus.RESOLVED,
                reason="Resolved via Slack", actor=f"slack:{action_info.get('user_id', '')}"
            )
            return {"text": f"Incident {incident_id} resolved"}
        except Exception as e:
            return {"text": f"Error: {e}"}

    return {"text": "Action processed"}


@router.post("/slack/slash-command")
async def slack_slash_command(
    command: str = Form(""),
    text: str = Form(""),
    user_id: str = Form(""),
    channel_id: str = Form(""),
    response_url: str = Form(""),
):
    """Handle /opslens slash commands from Slack."""
    if not _slack:
        raise HTTPException(503, "Slack integration not configured")

    result = await _slack.handle_slash_command(
        command, text, user_id, channel_id, response_url
    )

    subcommand = result.get("subcommand", "")

    if subcommand == "list" and _incident_manager:
        active = _incident_manager.get_active_incidents()
        if not active:
            return {"response_type": "ephemeral", "text": "No active incidents"}
        lines = [f"*Active Incidents ({len(active)}):*"]
        for inc in active[:10]:
            lines.append(f"- `{inc.incident_id}` {inc.severity} | {inc.title} | {inc.status.value}")
        return {"response_type": "ephemeral", "text": "\n".join(lines)}

    elif subcommand == "create":
        args_text = result.get("args", "").strip()
        parts = args_text.split(maxsplit=2)
        if len(parts) < 3:
            return {
                "response_type": "ephemeral",
                "text": "Usage: `/opslens create <severity> <service> <title>`\nExample: `/opslens create P1 payment-service Checkout failing for users`",
            }
        severity_input, service, title = parts
        # Normalize severity
        severity_map = {
            "p0": "P0", "p1": "P1", "p2": "P2", "p3": "P3",
            "critical": "P0", "high": "P1", "warning": "P2", "low": "P3",
        }
        severity = severity_map.get(severity_input.lower(), severity_input.upper())
        if severity not in ("P0", "P1", "P2", "P3"):
            return {
                "response_type": "ephemeral",
                "text": f"Invalid severity `{severity_input}`. Use: P0, P1, P2, P3 (or critical, high, warning, low)",
            }

        from src.webhooks.normalizer import normalize_manual
        from src.webhooks.schemas import ManualIncident
        from src.api.router import _alert_handler

        if not _alert_handler:
            return {"response_type": "ephemeral", "text": "Alert handler not initialized"}

        manual = ManualIncident(title=title, description=f"Created via Slack by <@{result.get('user_id', 'unknown')}>", severity=severity, service=service)
        alerts = normalize_manual(manual)
        for a in alerts:
            await _alert_handler(a)

        return {
            "response_type": "in_channel",
            "text": f":rotating_light: *Incident Created* | {severity} | {service}\n*{title}*\nCreated by <@{result.get('user_id', 'unknown')}>",
        }

    elif subcommand == "status" and _incident_manager:
        inc_id = result.get("args", "").strip()
        if inc_id:
            incident = _incident_manager.get_incident(inc_id.upper())
            if incident:
                return {
                    "response_type": "ephemeral",
                    "text": (
                        f"*{incident.incident_id}: {incident.title}*\n"
                        f"Severity: {incident.severity}\n"
                        f"Status: {incident.status.value}\n"
                        f"Service: {incident.service}\n"
                        f"Owner: {incident.owner or 'Unassigned'}"
                    ),
                }
            return {"response_type": "ephemeral", "text": f"Incident {inc_id} not found"}
        # Show stats
        stats = _incident_manager.get_stats()
        return {
            "response_type": "ephemeral",
            "text": f"*OpsLens Status:* {stats['active']} active, {stats['total']} total incidents",
        }

    return result


# ---- Jira / Linear ----


class CreateTicketRequest(BaseModel):
    summary: str
    description: str
    incident_id: str = ""
    priority: str = "Medium"
    issue_type: str = ""
    assignee: str = ""
    labels: list[str] = []


class CreateActionItemsRequest(BaseModel):
    incident_id: str
    action_items: list[dict[str, str]]
    epic_key: str = ""


@router.post("/tickets/create")
async def create_ticket(req: CreateTicketRequest):
    """Create a ticket in the configured ticket provider (Jira or Linear)."""
    if _jira and _jira.enabled:
        return await _jira.create_ticket(
            summary=req.summary,
            description=req.description,
            issue_type=req.issue_type or "Task",
            priority=req.priority,
            labels=req.labels,
            incident_id=req.incident_id,
            assignee_email=req.assignee,
        )
    elif _linear and _linear.enabled:
        priority_map = {"Highest": 1, "High": 2, "Medium": 3, "Low": 4, "Lowest": 4}
        return await _linear.create_ticket(
            title=req.summary,
            description=req.description,
            priority=priority_map.get(req.priority, 3),
            incident_id=req.incident_id,
        )
    raise HTTPException(503, "No ticket provider configured (Jira or Linear)")


@router.post("/tickets/action-items")
async def create_action_items(req: CreateActionItemsRequest):
    """Create tickets for all postmortem action items."""
    if not _incident_manager:
        raise HTTPException(503, "Incident manager not initialized")

    incident = _incident_manager.get_incident(req.incident_id)
    if not incident:
        raise HTTPException(404, f"Incident {req.incident_id} not found")

    if _jira and _jira.enabled:
        results = await _jira.create_action_items(
            incident, req.action_items, epic_key=req.epic_key
        )
    elif _linear and _linear.enabled:
        results = await _linear.create_action_items(incident, req.action_items)
    else:
        raise HTTPException(503, "No ticket provider configured")

    return {"tickets": results, "count": len(results)}


@router.get("/tickets/incident/{incident_id}")
async def get_incident_tickets(incident_id: str):
    """Get all tickets linked to an incident."""
    if _jira and _jira.enabled:
        return await _jira.get_incident_tickets(incident_id)
    return []


@router.get("/tickets/{ticket_key}/status")
async def get_ticket_status(ticket_key: str):
    """Get the current status of a ticket."""
    if _jira and _jira.enabled:
        return await _jira.get_ticket_status(ticket_key)
    elif _linear and _linear.enabled:
        return await _linear.get_ticket_status(ticket_key)
    raise HTTPException(503, "No ticket provider configured")


# ---- Cloud Providers ----


@router.get("/cloud/alerts")
async def cloud_get_alerts():
    """Get active alerts from all configured cloud providers."""
    if not _cloud or not _cloud.any_enabled:
        raise HTTPException(503, "No cloud providers configured")
    return await _cloud.get_all_active_alerts()


@router.get("/cloud/health/{provider}/{service_name}")
async def cloud_service_health(
    provider: str,
    service_name: str,
    cluster: str = "default",
    namespace: str = "default",
    resource_group: str = "",
):
    """Get service health from a cloud provider."""
    if not _cloud:
        raise HTTPException(503, "Cloud providers not configured")
    return await _cloud.get_service_health(
        service_name,
        provider=provider,
        cluster=cluster,
        namespace=namespace,
        resource_group=resource_group,
    )


class ECSActionRequest(BaseModel):
    cluster: str
    service_name: str
    desired_count: int = 0


@router.post("/cloud/aws/ecs/restart")
async def aws_ecs_restart(req: ECSActionRequest):
    """Restart an ECS service (force new deployment)."""
    if not _cloud or not _cloud.aws.enabled:
        raise HTTPException(503, "AWS not configured")
    return await _cloud.aws.restart_ecs_service(req.cluster, req.service_name)


@router.post("/cloud/aws/ecs/scale")
async def aws_ecs_scale(req: ECSActionRequest):
    """Scale an ECS service."""
    if not _cloud or not _cloud.aws.enabled:
        raise HTTPException(503, "AWS not configured")
    if req.desired_count <= 0:
        raise HTTPException(400, "desired_count must be positive")
    return await _cloud.aws.scale_ecs_service(
        req.cluster, req.service_name, req.desired_count
    )


@router.get("/cloud/test")
async def cloud_test_connections():
    """Test all cloud provider connections."""
    if not _cloud:
        return {"aws": {"status": "disabled"}, "gcp": {"status": "disabled"}, "azure": {"status": "disabled"}}
    return await _cloud.test_all_connections()


# ---- Knowledge Base ----


class KBSearchRequest(BaseModel):
    query: str
    top_k: int = 5
    doc_type: str = ""


class KBIndexRequest(BaseModel):
    doc_id: str
    title: str
    content: str
    doc_type: str = "document"
    metadata: dict[str, Any] = {}


@router.post("/knowledge-base/search")
async def kb_search(req: KBSearchRequest):
    """Search the knowledge base using semantic similarity."""
    if not _knowledge_base:
        raise HTTPException(503, "Knowledge base not initialized")
    results = await _knowledge_base.search(
        req.query, top_k=req.top_k, doc_type=req.doc_type
    )
    return {"results": results, "count": len(results)}


@router.post("/knowledge-base/index")
async def kb_index_document(req: KBIndexRequest):
    """Index a document in the knowledge base."""
    if not _knowledge_base:
        raise HTTPException(503, "Knowledge base not initialized")
    doc_id = await _knowledge_base.index_text(
        doc_id=req.doc_id,
        title=req.title,
        content=req.content,
        doc_type=req.doc_type,
        metadata=req.metadata,
    )
    return {"doc_id": doc_id, "status": "indexed"}


@router.post("/knowledge-base/index-incident/{incident_id}")
async def kb_index_incident(incident_id: str):
    """Index a resolved incident for future learning."""
    if not _knowledge_base or not _incident_manager:
        raise HTTPException(503, "Knowledge base or incident manager not initialized")

    incident = _incident_manager.get_incident(incident_id)
    if not incident:
        raise HTTPException(404, f"Incident {incident_id} not found")

    result = await _knowledge_base.learn_from_resolution(incident)
    return result


@router.get("/knowledge-base/similar/{incident_id}")
async def kb_find_similar(incident_id: str, top_k: int = 5):
    """Find past incidents similar to a given one."""
    if not _knowledge_base or not _incident_manager:
        raise HTTPException(503, "Knowledge base or incident manager not initialized")

    incident = _incident_manager.get_incident(incident_id)
    if not incident:
        raise HTTPException(404, f"Incident {incident_id} not found")

    results = await _knowledge_base.find_similar_incidents(incident, top_k=top_k)
    return {"results": results, "count": len(results)}


@router.get("/knowledge-base/stats")
async def kb_stats():
    """Get knowledge base statistics."""
    if not _knowledge_base:
        return {"total_documents": 0, "by_type": {}}
    return _knowledge_base.get_stats()


# ---- Outbound Webhooks ----


class WebhookSubscriptionRequest(BaseModel):
    name: str
    url: str
    secret: str = ""
    events: list[str] = ["*"]
    filters: dict[str, Any] = {}
    headers: dict[str, str] = {}
    payload_template: dict[str, Any] | None = None
    enabled: bool = True
    retry_count: int = 3
    timeout_seconds: int = 10


@router.get("/outbound-webhooks")
async def list_outbound_webhooks():
    """List all outbound webhook subscriptions."""
    if not _outbound_webhooks:
        return []
    return _outbound_webhooks.list_subscriptions()


@router.post("/outbound-webhooks")
async def create_outbound_webhook(req: WebhookSubscriptionRequest):
    """Create a new outbound webhook subscription."""
    if not _outbound_webhooks:
        raise HTTPException(503, "Outbound webhooks not initialized")
    sub = _outbound_webhooks.add_subscription(
        name=req.name,
        url=req.url,
        secret=req.secret,
        events=req.events,
        filters=req.filters,
        headers=req.headers,
        payload_template=req.payload_template,
        enabled=req.enabled,
        retry_count=req.retry_count,
        timeout_seconds=req.timeout_seconds,
    )
    return sub.to_dict()


@router.put("/outbound-webhooks/{sub_id}")
async def update_outbound_webhook(sub_id: str, updates: dict[str, Any]):
    """Update an outbound webhook subscription."""
    if not _outbound_webhooks:
        raise HTTPException(503, "Outbound webhooks not initialized")
    sub = _outbound_webhooks.update_subscription(sub_id, updates)
    if not sub:
        raise HTTPException(404, f"Subscription {sub_id} not found")
    return sub.to_dict()


@router.delete("/outbound-webhooks/{sub_id}")
async def delete_outbound_webhook(sub_id: str):
    """Delete an outbound webhook subscription."""
    if not _outbound_webhooks:
        raise HTTPException(503, "Outbound webhooks not initialized")
    if _outbound_webhooks.remove_subscription(sub_id):
        return {"status": "deleted"}
    raise HTTPException(404, f"Subscription {sub_id} not found")


@router.post("/outbound-webhooks/{sub_id}/test")
async def test_outbound_webhook(sub_id: str):
    """Send a test webhook to a subscription."""
    if not _outbound_webhooks:
        raise HTTPException(503, "Outbound webhooks not initialized")
    return await _outbound_webhooks.test_subscription(sub_id)


@router.get("/outbound-webhooks/history")
async def outbound_webhook_history(sub_id: str = "", limit: int = 50):
    """Get outbound webhook delivery history."""
    if not _outbound_webhooks:
        return []
    return _outbound_webhooks.get_delivery_history(sub_id=sub_id, limit=limit)


@router.get("/outbound-webhooks/events")
async def outbound_webhook_events():
    """Get list of supported event types for outbound webhooks."""
    if not _outbound_webhooks:
        from src.integrations.outbound_webhooks import EVENT_TYPES
        return EVENT_TYPES
    return _outbound_webhooks.get_supported_events()
