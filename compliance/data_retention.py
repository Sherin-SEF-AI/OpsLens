"""Data retention policy enforcement and GDPR compliance for OpsLens.

Provides automated cleanup of aged data, GDPR data export (right of access),
and GDPR data deletion (right to erasure / right to be forgotten).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import (
    AgentResult,
    AuditLog,
    Incident,
    IncidentStatusEnum,
    TimelineEvent,
    User,
)

logger = structlog.get_logger(__name__)


class DataRetentionManager:
    """Manages data retention policies and GDPR compliance operations.

    Args:
        audit_log_retention_days: Days to retain audit logs before deletion.
        incident_archive_days: Days after resolution before archiving incidents.
    """

    def __init__(
        self,
        audit_log_retention_days: int = 365,
        incident_archive_days: int = 180,
    ) -> None:
        self.audit_log_retention_days = audit_log_retention_days
        self.incident_archive_days = incident_archive_days

    async def apply_retention_policies(
        self, session: AsyncSession
    ) -> dict[str, int]:
        """Apply all data retention policies.

        Deletes audit logs older than the retention period, archives resolved
        incidents older than the archive period, and removes agent results
        for archived incidents.

        Args:
            session: An active async database session.

        Returns:
            A dict with counts: audit_logs_deleted, incidents_archived,
            agent_results_deleted.
        """
        now = datetime.now(timezone.utc)

        # --- Delete old audit logs ---
        audit_cutoff = now - timedelta(days=self.audit_log_retention_days)
        audit_result = await session.execute(
            delete(AuditLog).where(AuditLog.created_at < audit_cutoff)
        )
        audit_logs_deleted = audit_result.rowcount  # type: ignore[union-attr]

        logger.info(
            "retention.audit_logs_deleted",
            count=audit_logs_deleted,
            cutoff=audit_cutoff.isoformat(),
        )

        # --- Archive old resolved incidents ---
        archive_cutoff = now - timedelta(days=self.incident_archive_days)
        resolved_statuses = {
            IncidentStatusEnum.RESOLVED,
            IncidentStatusEnum.POSTMORTEM,
        }

        # Find incidents eligible for archival
        archive_query = select(Incident.id).where(
            Incident.status.in_(resolved_statuses),
            Incident.resolved_at.isnot(None),
            Incident.resolved_at < archive_cutoff,
        )
        archive_rows = await session.execute(archive_query)
        incident_ids_to_archive = [row[0] for row in archive_rows.all()]

        incidents_archived = 0
        agent_results_deleted = 0

        if incident_ids_to_archive:
            # Delete agent results for archived incidents
            agent_delete_result = await session.execute(
                delete(AgentResult).where(
                    AgentResult.incident_id.in_(incident_ids_to_archive)
                )
            )
            agent_results_deleted = agent_delete_result.rowcount  # type: ignore[union-attr]

            # Delete timeline events for archived incidents
            await session.execute(
                delete(TimelineEvent).where(
                    TimelineEvent.incident_id.in_(incident_ids_to_archive)
                )
            )

            # Delete the archived incidents themselves
            incident_delete_result = await session.execute(
                delete(Incident).where(
                    Incident.id.in_(incident_ids_to_archive)
                )
            )
            incidents_archived = incident_delete_result.rowcount  # type: ignore[union-attr]

        logger.info(
            "retention.incidents_archived",
            count=incidents_archived,
            agent_results_deleted=agent_results_deleted,
            cutoff=archive_cutoff.isoformat(),
        )

        await session.commit()

        return {
            "audit_logs_deleted": audit_logs_deleted,
            "incidents_archived": incidents_archived,
            "agent_results_deleted": agent_results_deleted,
        }

    async def export_user_data(
        self, user_id: uuid.UUID, session: AsyncSession
    ) -> dict[str, Any]:
        """Export all data associated with a user (GDPR right of access).

        Gathers the user's profile, audit logs, timeline comments, and
        incident assignments into a single dictionary suitable for JSON export.

        Args:
            user_id: The UUID of the user whose data to export.
            session: An active async database session.

        Returns:
            A dictionary containing all user-related data.
        """
        # Fetch user profile
        user_result = await session.execute(
            select(User).where(User.id == user_id)
        )
        user = user_result.scalar_one_or_none()

        if user is None:
            logger.warning("gdpr.export_user_not_found", user_id=str(user_id))
            return {"error": "User not found", "user_id": str(user_id)}

        profile = {
            "id": str(user.id),
            "email": user.email,
            "name": user.name,
            "role": user.role.value if hasattr(user.role, "value") else str(user.role),
            "provider": user.provider.value if hasattr(user.provider, "value") else str(user.provider),
            "is_active": user.is_active,
            "avatar_url": user.avatar_url,
            "org_id": str(user.org_id),
            "created_at": user.created_at.isoformat(),
            "updated_at": user.updated_at.isoformat(),
        }

        # Fetch audit logs
        audit_result = await session.execute(
            select(AuditLog)
            .where(AuditLog.user_id == user_id)
            .order_by(AuditLog.created_at.desc())
        )
        audit_logs = [
            {
                "id": str(log.id),
                "action": log.action,
                "resource_type": log.resource_type,
                "resource_id": log.resource_id,
                "details": log.details,
                "ip_address": log.ip_address,
                "user_agent": log.user_agent,
                "created_at": log.created_at.isoformat(),
            }
            for log in audit_result.scalars().all()
        ]

        # Fetch timeline comments/actions by this user
        timeline_result = await session.execute(
            select(TimelineEvent)
            .where(TimelineEvent.actor == str(user_id))
            .order_by(TimelineEvent.created_at.desc())
        )
        comments = [
            {
                "id": str(evt.id),
                "incident_id": str(evt.incident_id),
                "event_type": evt.event_type.value if hasattr(evt.event_type, "value") else str(evt.event_type),
                "message": evt.message,
                "metadata": evt.metadata_,
                "created_at": evt.created_at.isoformat(),
            }
            for evt in timeline_result.scalars().all()
        ]

        # Fetch assigned incidents
        incidents_result = await session.execute(
            select(Incident)
            .where(Incident.assigned_to == user_id)
            .order_by(Incident.created_at.desc())
        )
        assigned_incidents = [
            {
                "id": str(inc.id),
                "incident_id": inc.incident_id,
                "title": inc.title,
                "status": inc.status.value if hasattr(inc.status, "value") else str(inc.status),
                "severity": inc.severity,
                "service": inc.service,
                "created_at": inc.created_at.isoformat(),
            }
            for inc in incidents_result.scalars().all()
        ]

        export_data = {
            "export_timestamp": datetime.now(timezone.utc).isoformat(),
            "user_id": str(user_id),
            "profile": profile,
            "audit_logs": audit_logs,
            "audit_logs_count": len(audit_logs),
            "comments_and_actions": comments,
            "comments_count": len(comments),
            "assigned_incidents": assigned_incidents,
            "assigned_incidents_count": len(assigned_incidents),
        }

        logger.info(
            "gdpr.data_exported",
            user_id=str(user_id),
            audit_logs=len(audit_logs),
            comments=len(comments),
            incidents=len(assigned_incidents),
        )

        return export_data

    async def delete_user_data(
        self, user_id: uuid.UUID, session: AsyncSession
    ) -> dict[str, Any]:
        """Delete/anonymize all user data (GDPR right to erasure).

        Anonymizes audit logs by replacing the user_id with a sentinel value,
        unassigns incidents, anonymizes timeline events, and finally deletes
        the user profile.

        Args:
            user_id: The UUID of the user to delete.
            session: An active async database session.

        Returns:
            A summary dict with counts of affected records.
        """
        deleted_user_sentinel = "deleted-user"

        # Verify user exists
        user_result = await session.execute(
            select(User).where(User.id == user_id)
        )
        user = user_result.scalar_one_or_none()

        if user is None:
            logger.warning("gdpr.delete_user_not_found", user_id=str(user_id))
            return {"error": "User not found", "user_id": str(user_id)}

        user_email = user.email

        # Anonymize audit logs - set user_id to NULL, store sentinel in details
        audit_update_result = await session.execute(
            update(AuditLog)
            .where(AuditLog.user_id == user_id)
            .values(
                user_id=None,
                details={"anonymized": True, "original_user": deleted_user_sentinel},
                ip_address=None,
                user_agent=None,
            )
        )
        audit_logs_anonymized = audit_update_result.rowcount  # type: ignore[union-attr]

        # Unassign incidents from this user
        incidents_update_result = await session.execute(
            update(Incident)
            .where(Incident.assigned_to == user_id)
            .values(assigned_to=None)
        )
        incidents_unassigned = incidents_update_result.rowcount  # type: ignore[union-attr]

        # Anonymize timeline events authored by this user
        timeline_update_result = await session.execute(
            update(TimelineEvent)
            .where(TimelineEvent.actor == str(user_id))
            .values(actor=deleted_user_sentinel)
        )
        timeline_events_anonymized = timeline_update_result.rowcount  # type: ignore[union-attr]

        # Delete the user profile
        await session.execute(
            delete(User).where(User.id == user_id)
        )

        await session.commit()

        summary = {
            "user_id": str(user_id),
            "user_deleted": True,
            "email_deleted": user_email,
            "audit_logs_anonymized": audit_logs_anonymized,
            "incidents_unassigned": incidents_unassigned,
            "timeline_events_anonymized": timeline_events_anonymized,
            "deleted_at": datetime.now(timezone.utc).isoformat(),
        }

        logger.info(
            "gdpr.user_data_deleted",
            user_id=str(user_id),
            audit_logs_anonymized=audit_logs_anonymized,
            incidents_unassigned=incidents_unassigned,
            timeline_events_anonymized=timeline_events_anonymized,
        )

        return summary
