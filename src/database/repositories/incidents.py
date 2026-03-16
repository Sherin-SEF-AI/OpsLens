"""Async repository for Incident, TimelineEvent, and AgentResult models."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

from sqlalchemy import case, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.database.models import (
    AgentResult,
    AgentTypeEnum,
    Incident,
    IncidentStatusEnum,
    TimelineEvent,
    TimelineEventTypeEnum,
)


class IncidentRepository:
    """Data-access layer for incidents and related child records."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Incident CRUD
    # ------------------------------------------------------------------

    async def create(self, data: dict[str, Any]) -> Incident:
        """Create a new incident from a dictionary of column values.

        Args:
            data: Dictionary whose keys match ``Incident`` column names.

        Returns:
            The newly-created ``Incident`` instance (already flushed so
            ``id`` is populated).
        """
        incident = Incident(**data)
        self._session.add(incident)
        await self._session.flush()
        await self._session.refresh(incident)
        return incident

    async def get_by_id(self, incident_id: str) -> Incident | None:
        """Look up an incident by its human-readable ID (e.g. ``OPSLENS-0001``).

        Args:
            incident_id: The ``incident_id`` string (not the UUID primary key).

        Returns:
            The matching ``Incident`` or ``None``.
        """
        stmt = (
            select(Incident)
            .options(
                selectinload(Incident.timeline_events),
                selectinload(Incident.agent_results),
            )
            .where(Incident.incident_id == incident_id)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_uuid(self, id: uuid.UUID) -> Incident | None:
        """Look up an incident by its UUID primary key.

        Args:
            id: The UUID primary key.

        Returns:
            The matching ``Incident`` or ``None``.
        """
        stmt = (
            select(Incident)
            .options(
                selectinload(Incident.timeline_events),
                selectinload(Incident.agent_results),
            )
            .where(Incident.id == id)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_all(
        self,
        org_id: uuid.UUID,
        *,
        status: Optional[IncidentStatusEnum] = None,
        severity: Optional[str] = None,
        service: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Sequence[Incident]:
        """Return a filtered, paginated list of incidents for an organization.

        Args:
            org_id: Organization UUID to scope results.
            status: Optional status filter.
            severity: Optional severity string filter (e.g. ``"P0-Critical"``).
            service: Optional service name filter.
            limit: Maximum rows to return.
            offset: Number of rows to skip.

        Returns:
            Sequence of ``Incident`` objects ordered newest-first.
        """
        stmt = select(Incident).where(Incident.org_id == org_id)

        if status is not None:
            stmt = stmt.where(Incident.status == status)
        if severity is not None:
            stmt = stmt.where(Incident.severity == severity)
        if service is not None:
            stmt = stmt.where(Incident.service == service)

        stmt = stmt.order_by(Incident.created_at.desc()).limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def list_active(self, org_id: uuid.UUID) -> Sequence[Incident]:
        """Return all non-resolved / non-postmortem incidents for an org.

        Args:
            org_id: Organization UUID to scope results.

        Returns:
            Sequence of active ``Incident`` objects ordered newest-first.
        """
        active_statuses = {
            IncidentStatusEnum.TRIGGERED,
            IncidentStatusEnum.TRIAGED,
            IncidentStatusEnum.INVESTIGATING,
            IncidentStatusEnum.MITIGATED,
        }
        stmt = (
            select(Incident)
            .where(Incident.org_id == org_id, Incident.status.in_(active_statuses))
            .order_by(Incident.created_at.desc())
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def update(self, incident_id: str, **fields: Any) -> Incident | None:
        """Update an incident's fields by its human-readable ID.

        Args:
            incident_id: The ``incident_id`` string.
            **fields: Column names and their new values.

        Returns:
            The updated ``Incident`` or ``None`` if not found.
        """
        incident = await self.get_by_id(incident_id)
        if incident is None:
            return None
        for key, value in fields.items():
            setattr(incident, key, value)
        incident.updated_at = datetime.now(timezone.utc)
        await self._session.flush()
        await self._session.refresh(incident)
        return incident

    # ------------------------------------------------------------------
    # Timeline Events
    # ------------------------------------------------------------------

    async def add_timeline_event(
        self,
        incident_id: str,
        event_type: TimelineEventTypeEnum | str,
        message: str,
        actor: str = "system",
        metadata: Optional[dict[str, Any]] = None,
    ) -> TimelineEvent:
        """Append a timeline event to an incident.

        Args:
            incident_id: The human-readable incident ID.
            event_type: Event type enum value or string.
            message: Descriptive message.
            actor: Who/what produced the event.
            metadata: Optional JSON metadata.

        Returns:
            The created ``TimelineEvent``.

        Raises:
            ValueError: If the incident is not found.
        """
        incident = await self.get_by_id(incident_id)
        if incident is None:
            raise ValueError(f"Incident {incident_id} not found")

        if isinstance(event_type, str):
            event_type = TimelineEventTypeEnum(event_type)

        event = TimelineEvent(
            incident_id=incident.id,
            event_type=event_type,
            message=message,
            actor=actor,
            metadata_=metadata or {},
        )
        self._session.add(event)
        await self._session.flush()
        await self._session.refresh(event)
        return event

    async def get_timeline(self, incident_id: str) -> Sequence[TimelineEvent]:
        """Retrieve the full timeline for an incident ordered chronologically.

        Args:
            incident_id: The human-readable incident ID.

        Returns:
            Sequence of ``TimelineEvent`` rows.

        Raises:
            ValueError: If the incident is not found.
        """
        incident = await self.get_by_id(incident_id)
        if incident is None:
            raise ValueError(f"Incident {incident_id} not found")

        stmt = (
            select(TimelineEvent)
            .where(TimelineEvent.incident_id == incident.id)
            .order_by(TimelineEvent.created_at.asc())
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    # ------------------------------------------------------------------
    # Agent Results
    # ------------------------------------------------------------------

    async def add_agent_result(
        self,
        incident_id: str,
        agent_type: AgentTypeEnum | str,
        analysis: str,
        confidence: float,
        model_used: Optional[str] = None,
        duration_ms: Optional[int] = None,
        tool_calls: Optional[dict[str, Any]] = None,
    ) -> AgentResult:
        """Store the output of an AI agent run against an incident.

        Args:
            incident_id: Human-readable incident ID.
            agent_type: Which agent produced this result.
            analysis: Free-text analysis produced by the agent.
            confidence: Confidence score (0.0 - 1.0).
            model_used: LLM model identifier.
            duration_ms: Wall-clock time of the agent run in milliseconds.
            tool_calls: Optional JSON of tool calls made.

        Returns:
            The created ``AgentResult``.

        Raises:
            ValueError: If the incident is not found.
        """
        incident = await self.get_by_id(incident_id)
        if incident is None:
            raise ValueError(f"Incident {incident_id} not found")

        if isinstance(agent_type, str):
            agent_type = AgentTypeEnum(agent_type)

        result_row = AgentResult(
            incident_id=incident.id,
            agent_type=agent_type,
            analysis=analysis,
            confidence=confidence,
            model_used=model_used,
            duration_ms=duration_ms,
            tool_calls=tool_calls or {},
        )
        self._session.add(result_row)
        await self._session.flush()
        await self._session.refresh(result_row)
        return result_row

    async def get_agent_results(self, incident_id: str) -> Sequence[AgentResult]:
        """Get all agent results for an incident.

        Args:
            incident_id: Human-readable incident ID.

        Returns:
            Sequence of ``AgentResult`` rows ordered by creation time.

        Raises:
            ValueError: If the incident is not found.
        """
        incident = await self.get_by_id(incident_id)
        if incident is None:
            raise ValueError(f"Incident {incident_id} not found")

        stmt = (
            select(AgentResult)
            .where(AgentResult.incident_id == incident.id)
            .order_by(AgentResult.created_at.asc())
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    async def get_stats(self, org_id: uuid.UUID) -> dict[str, Any]:
        """Compute aggregate incident statistics for an organization.

        Args:
            org_id: Organization UUID.

        Returns:
            Dictionary with keys: ``total``, ``active``, ``resolved``,
            ``mttr_seconds``, ``by_severity``, ``by_service``.
        """
        base = select(Incident).where(Incident.org_id == org_id)

        # Total count
        total_q = select(func.count()).select_from(base.subquery())
        total: int = (await self._session.execute(total_q)).scalar_one()

        # Active count
        active_statuses = {
            IncidentStatusEnum.TRIGGERED,
            IncidentStatusEnum.TRIAGED,
            IncidentStatusEnum.INVESTIGATING,
            IncidentStatusEnum.MITIGATED,
        }
        active_q = select(func.count()).select_from(
            select(Incident)
            .where(Incident.org_id == org_id, Incident.status.in_(active_statuses))
            .subquery()
        )
        active: int = (await self._session.execute(active_q)).scalar_one()

        # Resolved count
        resolved_q = select(func.count()).select_from(
            select(Incident)
            .where(
                Incident.org_id == org_id,
                Incident.status.in_({IncidentStatusEnum.RESOLVED, IncidentStatusEnum.POSTMORTEM}),
            )
            .subquery()
        )
        resolved: int = (await self._session.execute(resolved_q)).scalar_one()

        # MTTR (mean time to resolution in seconds) for resolved incidents
        mttr_q = select(
            func.avg(
                func.extract("epoch", Incident.resolved_at) - func.extract("epoch", Incident.created_at)
            )
        ).where(
            Incident.org_id == org_id,
            Incident.resolved_at.is_not(None),
        )
        mttr_result = (await self._session.execute(mttr_q)).scalar_one_or_none()
        mttr_seconds: float = float(mttr_result) if mttr_result is not None else 0.0

        # By severity
        sev_q = (
            select(Incident.severity, func.count())
            .where(Incident.org_id == org_id)
            .group_by(Incident.severity)
        )
        sev_rows = (await self._session.execute(sev_q)).all()
        by_severity: dict[str, int] = {row[0]: row[1] for row in sev_rows}

        # By service
        svc_q = (
            select(Incident.service, func.count())
            .where(Incident.org_id == org_id, Incident.service.is_not(None))
            .group_by(Incident.service)
        )
        svc_rows = (await self._session.execute(svc_q)).all()
        by_service: dict[str, int] = {row[0]: row[1] for row in svc_rows}

        return {
            "total": total,
            "active": active,
            "resolved": resolved,
            "mttr_seconds": mttr_seconds,
            "by_severity": by_severity,
            "by_service": by_service,
        }

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    async def search(
        self,
        query: str,
        org_id: uuid.UUID,
        *,
        limit: int = 50,
    ) -> Sequence[Incident]:
        """Full-text search across incident title, description, and incident_id.

        Args:
            query: Search string (case-insensitive ILIKE).
            org_id: Organization UUID scope.
            limit: Maximum results.

        Returns:
            Sequence of matching ``Incident`` objects.
        """
        pattern = f"%{query}%"
        stmt = (
            select(Incident)
            .where(
                Incident.org_id == org_id,
                or_(
                    Incident.title.ilike(pattern),
                    Incident.description.ilike(pattern),
                    Incident.incident_id.ilike(pattern),
                    Incident.service.ilike(pattern),
                ),
            )
            .order_by(Incident.created_at.desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()
