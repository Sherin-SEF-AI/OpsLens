"""Celery tasks for AI agent pipeline execution."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import structlog
from celery import Task

from src.tasks.celery_app import celery_app

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Async bridge — run async code from sync Celery tasks
# ---------------------------------------------------------------------------

def _run_async(coro):
    """Run an async coroutine from a synchronous Celery task context.

    Creates a new event loop per invocation to avoid conflicts with
    any existing loop in the worker thread.
    """
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# Lazy initialization helpers
# ---------------------------------------------------------------------------

def _build_orchestrator():
    """Build a fresh AgentOrchestrator with all integrations wired.

    This is called per-task because Celery workers are long-lived
    processes and we want fresh config on each invocation.
    """
    from src.agents.orchestrator import AgentOrchestrator
    from src.config import get_config
    from src.incidents.manager import IncidentManager
    from src.notion_mcp.tools import NotionMCPTools

    config = get_config()
    notion_tools = NotionMCPTools(config.NOTION_MCP_URL, config.MCP_AUTH_TOKEN)
    incident_manager = IncidentManager(config, notion_tools)
    orchestrator = AgentOrchestrator(config, notion_tools, incident_manager)

    return orchestrator, incident_manager, config, notion_tools


def _get_incident_from_db(incident_id: str) -> dict[str, Any] | None:
    """Load incident data from the database (sync).

    Returns a dict with incident fields or None if not found.
    """
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker

    from src.config import get_config
    from src.database.models import Incident as IncidentModel

    config = get_config()
    db_url: str = getattr(
        config,
        "DATABASE_URL",
        "postgresql+asyncpg://opslens:opslens@localhost:5432/opslens",
    )
    sync_url = db_url.replace("+asyncpg", "+psycopg2")
    if "+asyncpg" in sync_url:
        sync_url = sync_url.replace("+asyncpg", "")

    engine = create_engine(sync_url, pool_size=3, pool_pre_ping=True)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    session = Session()
    try:
        stmt = select(IncidentModel).where(
            IncidentModel.incident_id == incident_id
        )
        result = session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            return None
        return {
            "id": str(row.id),
            "incident_id": row.incident_id,
            "title": row.title,
            "description": row.description,
            "status": row.status.value if row.status else "Triggered",
            "severity": row.severity,
            "service": row.service,
            "source": row.source,
            "notion_page_id": row.notion_page_id,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "resolved_at": row.resolved_at.isoformat() if row.resolved_at else None,
            "metadata": row.metadata_ or {},
        }
    finally:
        session.close()
        engine.dispose()


def _build_in_memory_incident(data: dict[str, Any]):
    """Convert DB incident dict to the in-memory Incident model the orchestrator expects."""
    from datetime import datetime, timezone

    from src.incidents.models import Incident, IncidentStatus

    status_map = {
        "Triggered": IncidentStatus.TRIGGERED,
        "Triaged": IncidentStatus.TRIAGED,
        "Investigating": IncidentStatus.INVESTIGATING,
        "Mitigated": IncidentStatus.MITIGATED,
        "Resolved": IncidentStatus.RESOLVED,
        "Postmortem": IncidentStatus.POSTMORTEM,
    }

    triggered_at = datetime.now(timezone.utc)
    if data.get("created_at"):
        try:
            triggered_at = datetime.fromisoformat(data["created_at"])
        except (ValueError, TypeError):
            pass

    return Incident(
        incident_id=data["incident_id"],
        title=data.get("title", ""),
        description=data.get("description", ""),
        severity=data.get("severity", "P2-Medium"),
        status=status_map.get(data.get("status", "Triggered"), IncidentStatus.TRIGGERED),
        service=data.get("service", "unknown"),
        source=data.get("source", "generic"),
        triggered_at=triggered_at,
        notion_page_id=data.get("notion_page_id", ""),
    )


def _build_synthetic_alert(incident_data: dict[str, Any]):
    """Build a UnifiedAlert from incident data for agent consumption."""
    from datetime import datetime, timezone

    from src.webhooks.schemas import AlertSource, AlertStatus, Severity, UnifiedAlert

    severity_map = {
        "P0-Critical": Severity.P0,
        "P1-High": Severity.P1,
        "P2-Medium": Severity.P2,
        "P3-Low": Severity.P3,
    }
    source_map = {
        "prometheus": AlertSource.PROMETHEUS,
        "grafana": AlertSource.GRAFANA,
        "pagerduty": AlertSource.PAGERDUTY,
        "manual": AlertSource.MANUAL,
        "generic": AlertSource.GENERIC,
    }

    triggered_at = datetime.now(timezone.utc)
    if incident_data.get("created_at"):
        try:
            triggered_at = datetime.fromisoformat(incident_data["created_at"])
        except (ValueError, TypeError):
            pass

    return UnifiedAlert(
        alert_id=f"task-{incident_data['incident_id']}",
        title=incident_data.get("title", ""),
        description=incident_data.get("description", ""),
        severity=severity_map.get(
            incident_data.get("severity", "P2-Medium"), Severity.P2
        ),
        status=AlertStatus.FIRING,
        service=incident_data.get("service", "unknown"),
        source=source_map.get(
            incident_data.get("source", "generic"), AlertSource.GENERIC
        ),
        triggered_at=triggered_at,
    )


# ---------------------------------------------------------------------------
# Celery tasks
# ---------------------------------------------------------------------------

@celery_app.task(
    bind=True,
    name="src.tasks.agent_tasks.run_agent_pipeline",
    max_retries=3,
    soft_time_limit=300,
    time_limit=600,
    acks_late=True,
)
def run_agent_pipeline(
    self: Task,
    incident_id: str,
) -> dict[str, Any]:
    """Run the full agent pipeline: triage -> correlation -> remediation -> comms.

    Args:
        incident_id: Human-readable incident ID (e.g., OPSLENS-0001).

    Returns:
        Dict with agents_run count and per-agent results.
    """
    log = logger.bind(task_id=self.request.id, incident_id=incident_id)
    log.info("agent_pipeline_task.starting")
    pipeline_start = time.monotonic()

    try:
        # Load incident from DB
        incident_data = _get_incident_from_db(incident_id)
        if not incident_data:
            log.error("agent_pipeline_task.incident_not_found")
            return {
                "agents_run": 0,
                "results": [],
                "error": f"Incident {incident_id} not found",
            }

        orchestrator, incident_manager, config, notion_tools = _build_orchestrator()

        # Build in-memory models
        incident = _build_in_memory_incident(incident_data)
        alert = _build_synthetic_alert(incident_data)

        # Register the incident in the manager
        incident_manager._incidents[incident_id] = incident

        # Run pipeline agents sequentially
        agent_results: list[dict[str, Any]] = []

        # 1. Triage
        triage_start = time.monotonic()
        triage_result = _run_async(
            orchestrator.triage_agent.run(incident, alert)
        )
        triage_ms = int((time.monotonic() - triage_start) * 1000)
        triage_confidence = (
            triage_result.get("confidence", 0.0)
            if isinstance(triage_result, dict) else 0.0
        )
        agent_results.append({
            "agent": "triage",
            "confidence": triage_confidence,
            "duration_ms": triage_ms,
        })
        log.info("agent_pipeline_task.triage_done", duration_ms=triage_ms)

        # 2. Correlation
        corr_start = time.monotonic()
        correlation_result = _run_async(
            orchestrator.correlation_agent.run(incident, alert, triage_result)
        )
        corr_ms = int((time.monotonic() - corr_start) * 1000)
        corr_confidence = (
            correlation_result.get("confidence", 0.0)
            if isinstance(correlation_result, dict) else 0.0
        )
        agent_results.append({
            "agent": "correlation",
            "confidence": corr_confidence,
            "duration_ms": corr_ms,
        })
        log.info("agent_pipeline_task.correlation_done", duration_ms=corr_ms)

        # 3. Remediation
        rem_start = time.monotonic()
        remediation_result = _run_async(
            orchestrator.remediation_agent.run(
                incident, alert, triage_result, correlation_result
            )
        )
        rem_ms = int((time.monotonic() - rem_start) * 1000)
        rem_confidence = (
            remediation_result.get("confidence", 0.0)
            if isinstance(remediation_result, dict) else 0.0
        )
        agent_results.append({
            "agent": "remediation",
            "confidence": rem_confidence,
            "duration_ms": rem_ms,
        })
        log.info("agent_pipeline_task.remediation_done", duration_ms=rem_ms)

        # 4. Comms (only for P0/P1)
        severity = incident_data.get("severity", "P3-Low")
        if severity in ("P0-Critical", "P1-High"):
            comms_start = time.monotonic()
            comms_result = _run_async(
                orchestrator.comms_agent.run(
                    incident, triage_result, correlation_result, remediation_result
                )
            )
            comms_ms = int((time.monotonic() - comms_start) * 1000)
            comms_confidence = (
                comms_result.get("confidence", 0.0)
                if isinstance(comms_result, dict) else 0.0
            )
            agent_results.append({
                "agent": "comms",
                "confidence": comms_confidence,
                "duration_ms": comms_ms,
            })
            log.info("agent_pipeline_task.comms_done", duration_ms=comms_ms)

        total_ms = int((time.monotonic() - pipeline_start) * 1000)
        log.info(
            "agent_pipeline_task.completed",
            agents_run=len(agent_results),
            total_duration_ms=total_ms,
        )

        return {
            "agents_run": len(agent_results),
            "results": agent_results,
            "total_duration_ms": total_ms,
        }

    except Exception as exc:
        log.exception("agent_pipeline_task.error", error=str(exc))
        raise self.retry(exc=exc, countdown=min(30 * (2 ** self.request.retries), 300))


@celery_app.task(
    bind=True,
    name="src.tasks.agent_tasks.run_single_agent",
    max_retries=3,
    soft_time_limit=300,
    time_limit=600,
    acks_late=True,
)
def run_single_agent(
    self: Task,
    incident_id: str,
    agent_type: str,
) -> dict[str, Any]:
    """Run a specific agent on an incident.

    Args:
        incident_id: Human-readable incident ID.
        agent_type: Agent to run (triage, correlation, remediation, comms, postmortem).

    Returns:
        Dict with agent name, confidence, duration_ms, and analysis_preview.
    """
    log = logger.bind(
        task_id=self.request.id,
        incident_id=incident_id,
        agent_type=agent_type,
    )
    log.info("single_agent_task.starting")
    start = time.monotonic()

    try:
        incident_data = _get_incident_from_db(incident_id)
        if not incident_data:
            return {
                "agent": agent_type,
                "confidence": 0.0,
                "duration_ms": 0,
                "analysis_preview": "",
                "error": f"Incident {incident_id} not found",
            }

        orchestrator, incident_manager, config, notion_tools = _build_orchestrator()
        incident = _build_in_memory_incident(incident_data)
        alert = _build_synthetic_alert(incident_data)
        incident_manager._incidents[incident_id] = incident

        result: dict[str, Any] = {}

        if agent_type == "triage":
            result = _run_async(orchestrator.triage_agent.run(incident, alert))
        elif agent_type == "correlation":
            # Correlation needs triage results; run triage first
            triage_result = _run_async(
                orchestrator.triage_agent.run(incident, alert)
            )
            result = _run_async(
                orchestrator.correlation_agent.run(incident, alert, triage_result)
            )
        elif agent_type == "remediation":
            triage_result = _run_async(
                orchestrator.triage_agent.run(incident, alert)
            )
            correlation_result = _run_async(
                orchestrator.correlation_agent.run(incident, alert, triage_result)
            )
            result = _run_async(
                orchestrator.remediation_agent.run(
                    incident, alert, triage_result, correlation_result
                )
            )
        elif agent_type == "comms":
            triage_result = _run_async(
                orchestrator.triage_agent.run(incident, alert)
            )
            correlation_result = _run_async(
                orchestrator.correlation_agent.run(incident, alert, triage_result)
            )
            remediation_result = _run_async(
                orchestrator.remediation_agent.run(
                    incident, alert, triage_result, correlation_result
                )
            )
            result = _run_async(
                orchestrator.comms_agent.run(
                    incident, triage_result, correlation_result, remediation_result
                )
            )
        elif agent_type == "postmortem":
            result = _run_async(orchestrator.postmortem_agent.run(incident))
        else:
            return {
                "agent": agent_type,
                "confidence": 0.0,
                "duration_ms": 0,
                "analysis_preview": "",
                "error": f"Unknown agent type: {agent_type}",
            }

        elapsed_ms = int((time.monotonic() - start) * 1000)
        confidence = result.get("confidence", 0.0) if isinstance(result, dict) else 0.0

        # Extract analysis text preview
        analysis_preview = ""
        if isinstance(result, dict):
            text = result.get("text", result.get("analysis", ""))
            analysis_preview = str(text)[:500] if text else ""

        log.info(
            "single_agent_task.completed",
            agent=agent_type,
            confidence=confidence,
            duration_ms=elapsed_ms,
        )
        return {
            "agent": agent_type,
            "confidence": confidence,
            "duration_ms": elapsed_ms,
            "analysis_preview": analysis_preview,
        }

    except Exception as exc:
        log.exception("single_agent_task.error", error=str(exc))
        raise self.retry(exc=exc, countdown=min(30 * (2 ** self.request.retries), 300))


@celery_app.task(
    bind=True,
    name="src.tasks.agent_tasks.run_postmortem_agent",
    max_retries=3,
    soft_time_limit=300,
    time_limit=600,
    acks_late=True,
)
def run_postmortem_agent(
    self: Task,
    incident_id: str,
) -> dict[str, Any]:
    """Generate a postmortem for a resolved incident.

    Args:
        incident_id: Human-readable incident ID.

    Returns:
        Dict with postmortem generation results.
    """
    log = logger.bind(task_id=self.request.id, incident_id=incident_id)
    log.info("postmortem_task.starting")
    start = time.monotonic()

    try:
        incident_data = _get_incident_from_db(incident_id)
        if not incident_data:
            return {
                "incident_id": incident_id,
                "postmortem_created": False,
                "error": f"Incident {incident_id} not found",
            }

        orchestrator, incident_manager, config, notion_tools = _build_orchestrator()
        incident = _build_in_memory_incident(incident_data)
        incident_manager._incidents[incident_id] = incident

        result = _run_async(orchestrator.postmortem_agent.run(incident))

        elapsed_ms = int((time.monotonic() - start) * 1000)
        postmortem_created = (
            result.get("postmortem_created", False)
            if isinstance(result, dict) else False
        )

        log.info(
            "postmortem_task.completed",
            postmortem_created=postmortem_created,
            duration_ms=elapsed_ms,
        )
        return {
            "incident_id": incident_id,
            "postmortem_created": postmortem_created,
            "duration_ms": elapsed_ms,
        }

    except Exception as exc:
        log.exception("postmortem_task.error", error=str(exc))
        raise self.retry(exc=exc, countdown=min(30 * (2 ** self.request.retries), 300))


@celery_app.task(
    bind=True,
    name="src.tasks.agent_tasks.rerun_on_change",
    max_retries=3,
    soft_time_limit=300,
    time_limit=600,
    acks_late=True,
)
def rerun_on_change(
    self: Task,
    incident_id: str,
    change_type: str,
    old_value: str,
    new_value: str,
) -> dict[str, Any]:
    """Re-run appropriate agents when a Notion edit is detected.

    Args:
        incident_id: Human-readable incident ID.
        change_type: Type of change (severity, status, root_cause, comment).
        old_value: Previous value.
        new_value: New value.

    Returns:
        Dict with change_type, agents_rerun list, and results.
    """
    log = logger.bind(
        task_id=self.request.id,
        incident_id=incident_id,
        change_type=change_type,
    )
    log.info(
        "rerun_on_change_task.starting",
        old_value=old_value,
        new_value=new_value,
    )
    start = time.monotonic()

    try:
        incident_data = _get_incident_from_db(incident_id)
        if not incident_data:
            return {
                "change_type": change_type,
                "agents_rerun": [],
                "error": f"Incident {incident_id} not found",
            }

        orchestrator, incident_manager, config, notion_tools = _build_orchestrator()
        incident = _build_in_memory_incident(incident_data)
        alert = _build_synthetic_alert(incident_data)
        incident_manager._incidents[incident_id] = incident

        agents_rerun: list[str] = []
        results: list[dict[str, Any]] = []

        if change_type == "severity":
            # Re-run triage with new severity context
            incident.severity = new_value
            agent_start = time.monotonic()
            triage_result = _run_async(
                orchestrator.triage_agent.run(incident, alert)
            )
            agent_ms = int((time.monotonic() - agent_start) * 1000)
            agents_rerun.append("triage")
            results.append({
                "agent": "triage",
                "duration_ms": agent_ms,
                "confidence": (
                    triage_result.get("confidence", 0.0)
                    if isinstance(triage_result, dict) else 0.0
                ),
            })

        elif change_type == "status":
            # If resolved, trigger postmortem
            if new_value in ("Resolved", "Postmortem"):
                agent_start = time.monotonic()
                pm_result = _run_async(
                    orchestrator.postmortem_agent.run(incident)
                )
                agent_ms = int((time.monotonic() - agent_start) * 1000)
                agents_rerun.append("postmortem")
                results.append({
                    "agent": "postmortem",
                    "duration_ms": agent_ms,
                    "postmortem_created": (
                        pm_result.get("postmortem_created", False)
                        if isinstance(pm_result, dict) else False
                    ),
                })

        elif change_type == "root_cause":
            # Root cause added -> trigger postmortem
            incident.root_cause = new_value
            agent_start = time.monotonic()
            pm_result = _run_async(
                orchestrator.postmortem_agent.run(incident)
            )
            agent_ms = int((time.monotonic() - agent_start) * 1000)
            agents_rerun.append("postmortem")
            results.append({
                "agent": "postmortem",
                "duration_ms": agent_ms,
                "postmortem_created": (
                    pm_result.get("postmortem_created", False)
                    if isinstance(pm_result, dict) else False
                ),
            })

        elif change_type == "comment":
            # Escalation comment -> re-run remediation
            if "ESCALATE" in new_value.upper():
                incident.severity = "P0-Critical"
                agent_start = time.monotonic()
                rem_result = _run_async(
                    orchestrator.remediation_agent.run(
                        incident, alert, {}, {"escalated": True}
                    )
                )
                agent_ms = int((time.monotonic() - agent_start) * 1000)
                agents_rerun.append("remediation")
                results.append({
                    "agent": "remediation",
                    "duration_ms": agent_ms,
                    "confidence": (
                        rem_result.get("confidence", 0.0)
                        if isinstance(rem_result, dict) else 0.0
                    ),
                })

        elapsed_ms = int((time.monotonic() - start) * 1000)
        log.info(
            "rerun_on_change_task.completed",
            agents_rerun=agents_rerun,
            total_duration_ms=elapsed_ms,
        )

        return {
            "change_type": change_type,
            "agents_rerun": agents_rerun,
            "results": results,
            "total_duration_ms": elapsed_ms,
        }

    except Exception as exc:
        log.exception("rerun_on_change_task.error", error=str(exc))
        raise self.retry(exc=exc, countdown=min(30 * (2 ** self.request.retries), 300))
