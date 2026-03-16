"""Celery tasks for enterprise features: SLA, on-call, reports, cleanup."""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from celery import Task
from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session, sessionmaker

from src.tasks.celery_app import celery_app

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_async(coro):
    """Run an async coroutine from a synchronous Celery task."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _get_sync_engine():
    """Create a synchronous SQLAlchemy engine."""
    from src.config import get_config

    config = get_config()
    db_url: str = getattr(
        config,
        "DATABASE_URL",
        "postgresql+asyncpg://opslens:opslens@localhost:5432/opslens",
    )
    sync_url = db_url.replace("+asyncpg", "+psycopg2")
    if "+asyncpg" in sync_url:
        sync_url = sync_url.replace("+asyncpg", "")
    return create_engine(sync_url, pool_size=5, pool_pre_ping=True)


def _get_sync_session() -> Session:
    """Create a synchronous session."""
    engine = _get_sync_engine()
    SyncSession = sessionmaker(bind=engine, expire_on_commit=False)
    return SyncSession()


def _get_async_session():
    """Get an async session for modules that require it."""
    from src.database.engine import AsyncSessionLocal
    return AsyncSessionLocal()


# ---------------------------------------------------------------------------
# SLA breach checking
# ---------------------------------------------------------------------------

@celery_app.task(
    bind=True,
    name="src.tasks.enterprise_tasks.check_sla_breaches_task",
    max_retries=2,
    soft_time_limit=60,
    time_limit=120,
)
def check_sla_breaches_task(self: Task) -> dict[str, Any]:
    """Check all active incidents against SLA policies, record breaches.

    Runs periodically (every 60 seconds) via beat_schedule.

    Returns:
        Dict with checked count, breaches_found, and notifications_sent.
    """
    log = logger.bind(task_id=self.request.id)
    log.info("sla_check_task.starting")
    start = time.monotonic()

    from src.database.models import (
        Incident,
        IncidentStatusEnum,
        Organization,
        SLABreach,
        SLABreachTypeEnum,
        SLAPolicy,
    )

    session = _get_sync_session()
    checked = 0
    breaches_found = 0
    notifications_sent = 0

    try:
        # Get all organizations
        orgs = session.execute(select(Organization)).scalars().all()

        for org in orgs:
            # Get active incidents
            active_stmt = select(Incident).where(
                Incident.org_id == org.id,
                Incident.status.notin_([
                    IncidentStatusEnum.RESOLVED,
                    IncidentStatusEnum.POSTMORTEM,
                ]),
            )
            active_incidents = session.execute(active_stmt).scalars().all()

            # Get active SLA policies for this org
            policies_stmt = select(SLAPolicy).where(
                SLAPolicy.org_id == org.id,
                SLAPolicy.is_active.is_(True),
            )
            policies = session.execute(policies_stmt).scalars().all()
            policy_map: dict[str, SLAPolicy] = {p.severity: p for p in policies}

            for incident in active_incidents:
                checked += 1
                policy = policy_map.get(incident.severity)
                if not policy:
                    continue

                now = datetime.now(timezone.utc)
                created = incident.created_at
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                elapsed_minutes = (now - created).total_seconds() / 60.0

                # Check response SLA
                if elapsed_minutes > policy.response_time_minutes:
                    breach = _record_breach_sync(
                        session, incident.id, policy.id,
                        SLABreachTypeEnum.RESPONSE,
                    )
                    if breach:
                        breaches_found += 1

                # Check acknowledge SLA
                if elapsed_minutes > policy.acknowledge_time_minutes:
                    # Only breach if still in TRIGGERED state
                    if incident.status == IncidentStatusEnum.TRIGGERED:
                        breach = _record_breach_sync(
                            session, incident.id, policy.id,
                            SLABreachTypeEnum.ACKNOWLEDGE,
                        )
                        if breach:
                            breaches_found += 1

                # Check resolution SLA
                if elapsed_minutes > policy.resolution_time_minutes:
                    breach = _record_breach_sync(
                        session, incident.id, policy.id,
                        SLABreachTypeEnum.RESOLUTION,
                    )
                    if breach:
                        breaches_found += 1

            # Send notifications for un-notified breaches
            unnotified_stmt = (
                select(SLABreach)
                .join(Incident, SLABreach.incident_id == Incident.id)
                .where(
                    Incident.org_id == org.id,
                    SLABreach.notified.is_(False),
                )
            )
            unnotified = session.execute(unnotified_stmt).scalars().all()
            for breach in unnotified:
                breach.notified = True
                notifications_sent += 1

        session.commit()

        elapsed_ms = int((time.monotonic() - start) * 1000)
        log.info(
            "sla_check_task.completed",
            checked=checked,
            breaches_found=breaches_found,
            notifications_sent=notifications_sent,
            duration_ms=elapsed_ms,
        )
        return {
            "checked": checked,
            "breaches_found": breaches_found,
            "notifications_sent": notifications_sent,
        }

    except Exception as exc:
        session.rollback()
        log.exception("sla_check_task.error", error=str(exc))
        raise self.retry(exc=exc, countdown=30)
    finally:
        session.close()


def _record_breach_sync(
    session: Session,
    incident_id: uuid.UUID,
    policy_id: uuid.UUID,
    breach_type: Any,
) -> bool:
    """Record an SLA breach if not already recorded. Returns True if new."""
    from src.database.models import SLABreach

    existing_stmt = select(SLABreach).where(
        SLABreach.incident_id == incident_id,
        SLABreach.sla_policy_id == policy_id,
        SLABreach.breach_type == breach_type,
    )
    existing = session.execute(existing_stmt).scalar_one_or_none()
    if existing:
        return False

    breach = SLABreach(
        incident_id=incident_id,
        sla_policy_id=policy_id,
        breach_type=breach_type,
    )
    session.add(breach)
    session.flush()
    return True


# ---------------------------------------------------------------------------
# Auto-escalation
# ---------------------------------------------------------------------------

@celery_app.task(
    bind=True,
    name="src.tasks.enterprise_tasks.auto_escalate_task",
    max_retries=2,
    soft_time_limit=60,
    time_limit=120,
)
def auto_escalate_task(self: Task) -> dict[str, Any]:
    """Check incidents past escalation timeout and escalate to next level.

    Returns:
        Dict with checked, escalated counts.
    """
    log = logger.bind(task_id=self.request.id)
    log.info("auto_escalate_task.starting")
    start = time.monotonic()

    from src.config import get_config
    from src.database.models import (
        Incident,
        IncidentStatusEnum,
        Organization,
        TimelineEvent,
        TimelineEventTypeEnum,
    )

    config = get_config()
    escalation_timeout = config.AUTO_ESCALATION_MINUTES

    session = _get_sync_session()
    checked = 0
    escalated = 0

    try:
        orgs = session.execute(select(Organization)).scalars().all()

        for org in orgs:
            # Find incidents past escalation timeout that are still in TRIGGERED state
            cutoff = datetime.now(timezone.utc) - timedelta(minutes=escalation_timeout)
            stmt = select(Incident).where(
                Incident.org_id == org.id,
                Incident.status == IncidentStatusEnum.TRIGGERED,
                Incident.created_at <= cutoff,
            )
            stale_incidents = session.execute(stmt).scalars().all()

            for incident in stale_incidents:
                checked += 1

                # Check if already escalated
                esc_stmt = select(TimelineEvent).where(
                    TimelineEvent.incident_id == incident.id,
                    TimelineEvent.event_type == TimelineEventTypeEnum.ESCALATION,
                )
                existing_escalation = session.execute(esc_stmt).scalar_one_or_none()

                # Determine escalation level
                level = 0
                if existing_escalation:
                    meta = existing_escalation.metadata_ or {}
                    level = meta.get("escalation_level", 0) + 1
                    if level > 3:
                        continue  # Max escalation reached

                # Record escalation timeline event
                event = TimelineEvent(
                    incident_id=incident.id,
                    event_type=TimelineEventTypeEnum.ESCALATION,
                    message=(
                        f"Auto-escalated to level {level} after "
                        f"{escalation_timeout} minutes without acknowledgment."
                    ),
                    actor="auto-escalation",
                    metadata_={
                        "escalation_level": level,
                        "timeout_minutes": escalation_timeout,
                    },
                )
                session.add(event)
                escalated += 1
                log.info(
                    "auto_escalate_task.escalated",
                    incident_id=incident.incident_id,
                    level=level,
                )

        session.commit()

        elapsed_ms = int((time.monotonic() - start) * 1000)
        log.info(
            "auto_escalate_task.completed",
            checked=checked,
            escalated=escalated,
            duration_ms=elapsed_ms,
        )
        return {"checked": checked, "escalated": escalated}

    except Exception as exc:
        session.rollback()
        log.exception("auto_escalate_task.error", error=str(exc))
        raise self.retry(exc=exc, countdown=30)
    finally:
        session.close()


# ---------------------------------------------------------------------------
# On-call rotation
# ---------------------------------------------------------------------------

@celery_app.task(
    bind=True,
    name="src.tasks.enterprise_tasks.rotate_oncall_schedules_task",
    max_retries=2,
    soft_time_limit=60,
    time_limit=120,
)
def rotate_oncall_schedules_task(self: Task) -> dict[str, Any]:
    """Check all on-call schedules and rotate if the period has elapsed.

    Runs every hour via beat_schedule.

    Returns:
        Dict with checked, rotated counts.
    """
    log = logger.bind(task_id=self.request.id)
    log.info("oncall_rotate_task.starting")
    start = time.monotonic()

    from src.database.models import OnCallSchedule, RotationTypeEnum

    session = _get_sync_session()
    checked = 0
    rotated = 0

    try:
        schedules = session.execute(select(OnCallSchedule)).scalars().all()
        now = datetime.now(timezone.utc)

        for schedule in schedules:
            checked += 1
            members = schedule.members if isinstance(schedule.members, list) else []
            if not members:
                continue

            last_rotation = schedule.updated_at
            if last_rotation.tzinfo is None:
                last_rotation = last_rotation.replace(tzinfo=timezone.utc)

            # Determine rotation period
            if schedule.rotation_type == RotationTypeEnum.DAILY:
                period = timedelta(days=1)
            elif schedule.rotation_type == RotationTypeEnum.WEEKLY:
                period = timedelta(weeks=1)
            else:
                policy = schedule.escalation_policy or {}
                hours = policy.get("rotation_period_hours", 168)
                period = timedelta(hours=hours)

            if (now - last_rotation) >= period:
                old_index = schedule.current_index
                schedule.current_index = (schedule.current_index + 1) % len(members)
                schedule.updated_at = now
                session.flush()
                rotated += 1

                new_member = members[schedule.current_index]
                log.info(
                    "oncall_rotate_task.rotated",
                    team=schedule.team_name,
                    old_index=old_index,
                    new_index=schedule.current_index,
                    new_member=new_member.get("name", "unknown"),
                )

        session.commit()

        elapsed_ms = int((time.monotonic() - start) * 1000)
        log.info(
            "oncall_rotate_task.completed",
            checked=checked,
            rotated=rotated,
            duration_ms=elapsed_ms,
        )
        return {"checked": checked, "rotated": rotated}

    except Exception as exc:
        session.rollback()
        log.exception("oncall_rotate_task.error", error=str(exc))
        raise self.retry(exc=exc, countdown=30)
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Notion sync (periodic polling)
# ---------------------------------------------------------------------------

@celery_app.task(
    bind=True,
    name="src.tasks.enterprise_tasks.notion_sync_task",
    max_retries=1,
    soft_time_limit=25,
    time_limit=30,
)
def notion_sync_task(self: Task) -> dict[str, Any]:
    """Poll Notion for changes and sync back to OpsLens.

    Runs every 30 seconds via beat_schedule. Delegates to the
    NotionWatcher's poll method.

    Returns:
        Dict with changes_detected count.
    """
    log = logger.bind(task_id=self.request.id)
    log.debug("notion_sync_task.starting")
    start = time.monotonic()

    try:
        from src.config import get_config
        from src.notion_mcp.tools import NotionMCPTools
        from src.sync.notion_watcher import NotionWatcher

        config = get_config()
        if not config.NOTION_INCIDENTS_DB_ID:
            return {"changes_detected": 0, "skipped": True}

        notion_tools = NotionMCPTools(config.NOTION_MCP_URL, config.MCP_AUTH_TOKEN)

        from src.incidents.manager import IncidentManager
        incident_manager = IncidentManager(config, notion_tools)

        watcher = NotionWatcher(config, notion_tools)
        # _poll_once is the internal single-poll method
        _run_async(watcher._poll_once())
        changes_count = 0  # _poll_once handles changes via callbacks

        elapsed_ms = int((time.monotonic() - start) * 1000)
        log.debug(
            "notion_sync_task.completed",
            changes_detected=changes_count,
            duration_ms=elapsed_ms,
        )
        return {"changes_detected": changes_count}

    except Exception as exc:
        log.exception("notion_sync_task.error", error=str(exc))
        # Don't retry aggressively for sync failures
        return {"changes_detected": 0, "error": str(exc)}


# ---------------------------------------------------------------------------
# Command center update
# ---------------------------------------------------------------------------

@celery_app.task(
    bind=True,
    name="src.tasks.enterprise_tasks.command_center_update_task",
    max_retries=1,
    soft_time_limit=60,
    time_limit=120,
)
def command_center_update_task(self: Task) -> dict[str, Any]:
    """Refresh the Notion command center page with latest metrics.

    Runs every 120 seconds via beat_schedule.

    Returns:
        Dict with update status.
    """
    log = logger.bind(task_id=self.request.id)
    log.debug("command_center_task.starting")
    start = time.monotonic()

    try:
        from src.config import get_config
        from src.notion_mcp.tools import NotionMCPTools

        config = get_config()
        page_id = config.NOTION_COMMAND_CENTER_PAGE_ID
        if not page_id:
            return {"updated": False, "reason": "No command center page configured"}

        notion_tools = NotionMCPTools(config.NOTION_MCP_URL, config.MCP_AUTH_TOKEN)

        # Gather metrics from DB
        session = _get_sync_session()
        try:
            from src.database.models import Incident, IncidentStatusEnum

            total_stmt = select(Incident)
            total = len(session.execute(total_stmt).scalars().all())

            active_stmt = select(Incident).where(
                Incident.status.notin_([
                    IncidentStatusEnum.RESOLVED,
                    IncidentStatusEnum.POSTMORTEM,
                ])
            )
            active = len(session.execute(active_stmt).scalars().all())

            resolved_stmt = select(Incident).where(
                Incident.status.in_([
                    IncidentStatusEnum.RESOLVED,
                    IncidentStatusEnum.POSTMORTEM,
                ])
            )
            resolved = len(session.execute(resolved_stmt).scalars().all())

            # Update the command center page in Notion
            from src.incidents.manager import IncidentManager as _IM
            from src.sync.command_center import CommandCenter

            _im = _IM(config, notion_tools)
            updater = CommandCenter(
                notion_tools, _im, page_id=page_id
            )
            _run_async(updater.force_update())

            # Also update the page title with timestamp
            _run_async(notion_tools.update_page(
                page_id,
                properties={
                    "Name": {
                        "title": [{
                            "text": {"content": f"OpsLens Command Center (Updated: {datetime.now(timezone.utc).strftime('%H:%M UTC')})"}
                        }]
                    }
                },
            ))

        finally:
            session.close()

        elapsed_ms = int((time.monotonic() - start) * 1000)
        log.debug(
            "command_center_task.completed",
            duration_ms=elapsed_ms,
        )
        return {"updated": True}

    except Exception as exc:
        log.exception("command_center_task.error", error=str(exc))
        return {"updated": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Scheduled reports
# ---------------------------------------------------------------------------

@celery_app.task(
    bind=True,
    name="src.tasks.enterprise_tasks.generate_scheduled_report",
    max_retries=2,
    soft_time_limit=120,
    time_limit=300,
)
def generate_scheduled_report(
    self: Task,
    report_type: str,
    org_id: str,
) -> dict[str, Any]:
    """Generate a daily/weekly/monthly incident report.

    Args:
        report_type: One of daily, weekly, monthly.
        org_id: Organization UUID string.

    Returns:
        Dict with report_id, title, and report_type.
    """
    log = logger.bind(
        task_id=self.request.id,
        report_type=report_type,
        org_id=org_id,
    )
    log.info("report_task.starting")
    start = time.monotonic()

    try:
        org_uuid = uuid.UUID(org_id)

        async def _generate():
            async with _get_async_session() as session:
                from src.enterprise.reporting import ReportGenerator

                generator = ReportGenerator(session)
                now = datetime.now(timezone.utc)

                if report_type == "daily":
                    report = await generator.generate_daily_report(
                        org_uuid, now - timedelta(days=1)
                    )
                elif report_type == "weekly":
                    report = await generator.generate_weekly_report(
                        org_uuid, now - timedelta(weeks=1)
                    )
                elif report_type == "monthly":
                    report = await generator.generate_monthly_report(
                        org_uuid, now.month, now.year
                    )
                else:
                    # Default to daily
                    report = await generator.generate_daily_report(
                        org_uuid, now - timedelta(days=1)
                    )

                await session.commit()
                return {
                    "report_id": str(report.id),
                    "title": report.title,
                    "report_type": report.report_type.value,
                }

        result = _run_async(_generate())

        elapsed_ms = int((time.monotonic() - start) * 1000)
        log.info(
            "report_task.completed",
            report_id=result.get("report_id"),
            duration_ms=elapsed_ms,
        )
        return result

    except Exception as exc:
        log.exception("report_task.error", error=str(exc))
        raise self.retry(exc=exc, countdown=60)


# ---------------------------------------------------------------------------
# Data cleanup
# ---------------------------------------------------------------------------

@celery_app.task(
    bind=True,
    name="src.tasks.enterprise_tasks.cleanup_old_data_task",
    max_retries=1,
    soft_time_limit=300,
    time_limit=600,
)
def cleanup_old_data_task(
    self: Task,
    retention_days: int = 90,
) -> dict[str, Any]:
    """Archive/delete old audit logs and agent results beyond retention period.

    Runs daily at 3 AM via beat_schedule.

    Args:
        retention_days: Number of days to retain data (default 90).

    Returns:
        Dict with deleted counts per table.
    """
    log = logger.bind(
        task_id=self.request.id,
        retention_days=retention_days,
    )
    log.info("cleanup_task.starting")
    start = time.monotonic()

    from src.database.models import AgentResult, AuditLog, TimelineEvent

    session = _get_sync_session()
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)

    audit_deleted = 0
    agent_results_deleted = 0
    timeline_deleted = 0

    try:
        # Delete old audit logs
        audit_stmt = delete(AuditLog).where(AuditLog.created_at < cutoff)
        result = session.execute(audit_stmt)
        audit_deleted = result.rowcount or 0

        # Delete old agent results (keep results for non-archived incidents)
        from src.database.models import Incident, IncidentStatusEnum
        # Find resolved incidents older than retention
        old_resolved_stmt = select(Incident.id).where(
            Incident.resolved_at.isnot(None),
            Incident.resolved_at < cutoff,
        )
        old_incident_ids = [
            row[0] for row in session.execute(old_resolved_stmt).all()
        ]

        if old_incident_ids:
            agent_del_stmt = delete(AgentResult).where(
                AgentResult.incident_id.in_(old_incident_ids)
            )
            result = session.execute(agent_del_stmt)
            agent_results_deleted = result.rowcount or 0

            # Delete old timeline events for those incidents
            timeline_del_stmt = delete(TimelineEvent).where(
                TimelineEvent.incident_id.in_(old_incident_ids),
                TimelineEvent.created_at < cutoff,
            )
            result = session.execute(timeline_del_stmt)
            timeline_deleted = result.rowcount or 0

        session.commit()

        elapsed_ms = int((time.monotonic() - start) * 1000)
        log.info(
            "cleanup_task.completed",
            audit_deleted=audit_deleted,
            agent_results_deleted=agent_results_deleted,
            timeline_deleted=timeline_deleted,
            duration_ms=elapsed_ms,
        )
        return {
            "audit_logs_deleted": audit_deleted,
            "agent_results_deleted": agent_results_deleted,
            "timeline_events_deleted": timeline_deleted,
            "retention_days": retention_days,
        }

    except Exception as exc:
        session.rollback()
        log.exception("cleanup_task.error", error=str(exc))
        raise self.retry(exc=exc, countdown=300)
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Runbook step execution
# ---------------------------------------------------------------------------

@celery_app.task(
    bind=True,
    name="src.tasks.enterprise_tasks.execute_runbook_step_task",
    max_retries=2,
    soft_time_limit=300,
    time_limit=600,
)
def execute_runbook_step_task(
    self: Task,
    execution_id: str,
    step_index: int,
) -> dict[str, Any]:
    """Execute a single runbook step as a background task.

    Args:
        execution_id: RunbookExecution UUID string.
        step_index: Zero-based index of the step to execute.

    Returns:
        Dict with step execution results.
    """
    log = logger.bind(
        task_id=self.request.id,
        execution_id=execution_id,
        step_index=step_index,
    )
    log.info("runbook_step_task.starting")
    start = time.monotonic()

    try:
        exec_uuid = uuid.UUID(execution_id)

        async def _execute():
            async with _get_async_session() as session:
                from src.enterprise.runbook_automation import RunbookExecutor

                executor = RunbookExecutor(session)
                result = await executor.execute_step(exec_uuid, step_index)
                await session.commit()
                return {
                    "success": result.success,
                    "output": result.output[:2000] if result.output else "",
                    "duration_ms": result.duration_ms,
                    "error": result.error,
                }

        result = _run_async(_execute())

        elapsed_ms = int((time.monotonic() - start) * 1000)
        log.info(
            "runbook_step_task.completed",
            success=result.get("success"),
            duration_ms=elapsed_ms,
        )
        return result

    except Exception as exc:
        log.exception("runbook_step_task.error", error=str(exc))
        raise self.retry(exc=exc, countdown=30)
