"""Async repository for AuditLog model."""

from __future__ import annotations

import uuid
from typing import Any, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import AuditLog


class AuditRepository:
    """Data-access layer for the audit trail."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def log(
        self,
        action: str,
        *,
        user_id: Optional[uuid.UUID] = None,
        resource_type: Optional[str] = None,
        resource_id: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> AuditLog:
        """Record an audit log entry.

        Args:
            action: Short action identifier (e.g. ``"incident.create"``).
            user_id: UUID of the acting user (``None`` for system actions).
            resource_type: Type of resource affected (e.g. ``"incident"``).
            resource_id: Identifier of the affected resource.
            details: Arbitrary JSON payload with extra context.
            ip_address: Client IP address.
            user_agent: Client User-Agent header.

        Returns:
            The persisted ``AuditLog`` row.
        """
        entry = AuditLog(
            user_id=user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            details=details or {},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self._session.add(entry)
        await self._session.flush()
        await self._session.refresh(entry)
        return entry

    async def list_all(
        self,
        *,
        org_id: Optional[uuid.UUID] = None,
        incident_id: Optional[str] = None,
        user_id: Optional[uuid.UUID] = None,
        action: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[AuditLog]:
        """Retrieve audit log entries with optional filters.

        Args:
            org_id: Not stored on AuditLog directly; filters via resource if
                needed.  Included for API symmetry (currently unused in query).
            incident_id: Filter by ``resource_id`` when ``resource_type`` is
                ``"incident"``.
            user_id: Filter by the acting user.
            action: Filter by action string.
            limit: Maximum rows.
            offset: Pagination offset.

        Returns:
            Sequence of ``AuditLog`` entries ordered newest-first.
        """
        stmt = select(AuditLog)

        if user_id is not None:
            stmt = stmt.where(AuditLog.user_id == user_id)
        if action is not None:
            stmt = stmt.where(AuditLog.action == action)
        if incident_id is not None:
            stmt = stmt.where(
                AuditLog.resource_type == "incident",
                AuditLog.resource_id == incident_id,
            )

        stmt = stmt.order_by(AuditLog.created_at.desc()).limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def get_incident_trail(self, incident_id: str) -> Sequence[AuditLog]:
        """Return the full audit trail for a single incident.

        Args:
            incident_id: The human-readable incident ID (e.g.
                ``"OPSLENS-0001"``).

        Returns:
            Sequence of ``AuditLog`` entries in chronological order.
        """
        stmt = (
            select(AuditLog)
            .where(
                AuditLog.resource_type == "incident",
                AuditLog.resource_id == incident_id,
            )
            .order_by(AuditLog.created_at.asc())
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()
