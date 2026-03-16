"""SLA tracking, breach detection, and compliance analytics."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import structlog
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import (
    AgentResult,
    Incident,
    IncidentStatusEnum,
    SLABreach,
    SLABreachTypeEnum,
    SLAPolicy,
    TimelineEvent,
    TimelineEventTypeEnum,
)

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class SLAStatus:
    """Current SLA status for a single incident."""

    incident_id: uuid.UUID
    incident_title: str
    severity: str
    is_breached: bool
    breach_type: Optional[str]
    time_remaining_seconds: float
    percentage_elapsed: float
    policy: Optional[dict[str, Any]]


# ---------------------------------------------------------------------------
# Default SLA policies
# ---------------------------------------------------------------------------

DEFAULT_SLA_POLICIES: list[dict[str, Any]] = [
    {
        "name": "P0-Critical SLA",
        "severity": "P0-Critical",
        "response_time_minutes": 5,
        "acknowledge_time_minutes": 10,
        "resolution_time_minutes": 60,
    },
    {
        "name": "P1-High SLA",
        "severity": "P1-High",
        "response_time_minutes": 15,
        "acknowledge_time_minutes": 30,
        "resolution_time_minutes": 240,
    },
    {
        "name": "P2-Medium SLA",
        "severity": "P2-Medium",
        "response_time_minutes": 60,
        "acknowledge_time_minutes": 120,
        "resolution_time_minutes": 1440,
    },
    {
        "name": "P3-Low SLA",
        "severity": "P3-Low",
        "response_time_minutes": 240,
        "acknowledge_time_minutes": 480,
        "resolution_time_minutes": 4320,
    },
]


# ---------------------------------------------------------------------------
# SLATracker
# ---------------------------------------------------------------------------

class SLATracker:
    """SLA policy management, breach detection, and compliance metrics."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Policy CRUD
    # ------------------------------------------------------------------

    async def create_policy(
        self,
        name: str,
        severity: str,
        response_minutes: int,
        ack_minutes: int,
        resolution_minutes: int,
        org_id: uuid.UUID,
    ) -> SLAPolicy:
        """Create a new SLA policy.

        Args:
            name: Human-readable policy name.
            severity: Severity string the policy applies to (e.g. ``P0-Critical``).
            response_minutes: Max minutes for initial response.
            ack_minutes: Max minutes for acknowledgment.
            resolution_minutes: Max minutes for resolution.
            org_id: Owning organization UUID.

        Returns:
            The persisted ``SLAPolicy``.
        """
        policy = SLAPolicy(
            name=name,
            severity=severity,
            response_time_minutes=response_minutes,
            acknowledge_time_minutes=ack_minutes,
            resolution_time_minutes=resolution_minutes,
            org_id=org_id,
        )
        self._session.add(policy)
        await self._session.flush()
        await self._session.refresh(policy)
        logger.info(
            "sla.policy_created",
            name=name,
            severity=severity,
            response=response_minutes,
            ack=ack_minutes,
            resolution=resolution_minutes,
        )
        return policy

    async def get_policy_for_severity(
        self, severity: str, org_id: uuid.UUID
    ) -> SLAPolicy | None:
        """Retrieve the active SLA policy for a given severity.

        Returns:
            The first matching active ``SLAPolicy``, or ``None``.
        """
        stmt = (
            select(SLAPolicy)
            .where(
                SLAPolicy.org_id == org_id,
                SLAPolicy.severity == severity,
                SLAPolicy.is_active.is_(True),
            )
            .order_by(SLAPolicy.created_at.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def seed_default_policies(self, org_id: uuid.UUID) -> list[SLAPolicy]:
        """Create the default SLA policies for an organization if they don't exist.

        Returns:
            List of created or existing policies.
        """
        policies: list[SLAPolicy] = []
        for defaults in DEFAULT_SLA_POLICIES:
            existing = await self.get_policy_for_severity(defaults["severity"], org_id)
            if existing:
                policies.append(existing)
            else:
                p = await self.create_policy(
                    name=defaults["name"],
                    severity=defaults["severity"],
                    response_minutes=defaults["response_time_minutes"],
                    ack_minutes=defaults["acknowledge_time_minutes"],
                    resolution_minutes=defaults["resolution_time_minutes"],
                    org_id=org_id,
                )
                policies.append(p)
        return policies

    # ------------------------------------------------------------------
    # SLA checking
    # ------------------------------------------------------------------

    async def check_incident_sla(self, incident: Incident) -> SLAStatus:
        """Check the SLA status for a single incident.

        Determines the most urgent breach (response > acknowledge > resolution)
        and returns remaining time and percentage elapsed.

        Args:
            incident: The ``Incident`` ORM model.

        Returns:
            An ``SLAStatus`` instance.
        """
        now = datetime.now(timezone.utc)
        created = incident.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)

        policy = await self.get_policy_for_severity(incident.severity, incident.org_id)
        if policy is None:
            return SLAStatus(
                incident_id=incident.id,
                incident_title=incident.title,
                severity=incident.severity,
                is_breached=False,
                breach_type=None,
                time_remaining_seconds=-1,
                percentage_elapsed=0.0,
                policy=None,
            )

        elapsed_seconds = (now - created).total_seconds()
        is_resolved = incident.status in (
            IncidentStatusEnum.RESOLVED,
            IncidentStatusEnum.POSTMORTEM,
        )

        # Determine acknowledgment time (first status change away from TRIGGERED)
        ack_time = await self._get_ack_time(incident.id)

        # Check each SLA type and find the most critical breach
        breach_type: Optional[str] = None
        worst_remaining = float("inf")
        worst_percentage = 0.0

        # Response SLA
        response_limit = policy.response_time_minutes * 60
        response_remaining = response_limit - elapsed_seconds
        response_pct = (elapsed_seconds / response_limit * 100) if response_limit > 0 else 100
        if response_remaining < 0 and not is_resolved:
            breach_type = SLABreachTypeEnum.RESPONSE.value
            worst_remaining = response_remaining
            worst_percentage = response_pct

        # Acknowledge SLA
        ack_limit = policy.acknowledge_time_minutes * 60
        if ack_time is not None:
            ack_elapsed = (ack_time - created).total_seconds()
        else:
            ack_elapsed = elapsed_seconds
        ack_remaining = ack_limit - ack_elapsed
        ack_pct = (ack_elapsed / ack_limit * 100) if ack_limit > 0 else 100
        if ack_remaining < 0 and ack_time is None and not is_resolved:
            if breach_type is None or ack_remaining < worst_remaining:
                breach_type = SLABreachTypeEnum.ACKNOWLEDGE.value
                worst_remaining = ack_remaining
                worst_percentage = ack_pct

        # Resolution SLA
        resolution_limit = policy.resolution_time_minutes * 60
        if is_resolved and incident.resolved_at is not None:
            resolved_at = incident.resolved_at
            if resolved_at.tzinfo is None:
                resolved_at = resolved_at.replace(tzinfo=timezone.utc)
            resolution_elapsed = (resolved_at - created).total_seconds()
        else:
            resolution_elapsed = elapsed_seconds
        resolution_remaining = resolution_limit - resolution_elapsed
        resolution_pct = (
            (resolution_elapsed / resolution_limit * 100) if resolution_limit > 0 else 100
        )
        if resolution_remaining < 0 and not is_resolved:
            if breach_type is None or resolution_remaining < worst_remaining:
                breach_type = SLABreachTypeEnum.RESOLUTION.value
                worst_remaining = resolution_remaining
                worst_percentage = resolution_pct

        # If no breach found, report most urgent remaining time
        if breach_type is None:
            # Pick the SLA type with least remaining time
            candidates = []
            if not is_resolved:
                candidates.append(("response", response_remaining, response_pct))
                if ack_time is None:
                    candidates.append(("acknowledge", ack_remaining, ack_pct))
                candidates.append(("resolution", resolution_remaining, resolution_pct))
            if candidates:
                candidates.sort(key=lambda c: c[1])
                worst_remaining = candidates[0][1]
                worst_percentage = candidates[0][2]
            else:
                worst_remaining = resolution_remaining
                worst_percentage = resolution_pct

        return SLAStatus(
            incident_id=incident.id,
            incident_title=incident.title,
            severity=incident.severity,
            is_breached=breach_type is not None,
            breach_type=breach_type,
            time_remaining_seconds=worst_remaining,
            percentage_elapsed=min(worst_percentage, 100.0),
            policy={
                "id": str(policy.id),
                "name": policy.name,
                "response_minutes": policy.response_time_minutes,
                "acknowledge_minutes": policy.acknowledge_time_minutes,
                "resolution_minutes": policy.resolution_time_minutes,
            },
        )

    async def check_all_active_incidents(
        self, org_id: uuid.UUID
    ) -> list[SLAStatus]:
        """Batch-check SLA status for all active incidents in an organization.

        Returns:
            List of ``SLAStatus`` for each active incident.
        """
        stmt = select(Incident).where(
            Incident.org_id == org_id,
            Incident.status.notin_([
                IncidentStatusEnum.RESOLVED,
                IncidentStatusEnum.POSTMORTEM,
            ]),
        )
        result = await self._session.execute(stmt)
        incidents = result.scalars().all()

        statuses: list[SLAStatus] = []
        for incident in incidents:
            status = await self.check_incident_sla(incident)
            statuses.append(status)

        # Sort: breached first, then by remaining time ascending
        statuses.sort(key=lambda s: (not s.is_breached, s.time_remaining_seconds))
        return statuses

    # ------------------------------------------------------------------
    # Breach recording
    # ------------------------------------------------------------------

    async def record_breach(
        self,
        incident_id: uuid.UUID,
        sla_policy_id: uuid.UUID,
        breach_type: str,
    ) -> SLABreach:
        """Record an SLA breach event.

        Args:
            incident_id: The incident that breached.
            sla_policy_id: The policy that was breached.
            breach_type: One of ``response``, ``acknowledge``, ``resolution``.

        Returns:
            The persisted ``SLABreach``.
        """
        try:
            bt = SLABreachTypeEnum(breach_type)
        except ValueError:
            bt = SLABreachTypeEnum.RESPONSE

        # Check if this exact breach was already recorded
        stmt = select(SLABreach).where(
            SLABreach.incident_id == incident_id,
            SLABreach.sla_policy_id == sla_policy_id,
            SLABreach.breach_type == bt,
        )
        result = await self._session.execute(stmt)
        existing = result.scalar_one_or_none()
        if existing is not None:
            return existing

        breach = SLABreach(
            incident_id=incident_id,
            sla_policy_id=sla_policy_id,
            breach_type=bt,
        )
        self._session.add(breach)
        await self._session.flush()
        await self._session.refresh(breach)

        logger.warning(
            "sla.breach_recorded",
            incident_id=str(incident_id),
            policy_id=str(sla_policy_id),
            breach_type=breach_type,
        )
        return breach

    async def get_breaches(
        self,
        org_id: uuid.UUID,
        start_date: datetime,
        end_date: datetime,
    ) -> list[SLABreach]:
        """Retrieve SLA breaches within a date range.

        Args:
            org_id: Organization UUID.
            start_date: Start of the range (inclusive).
            end_date: End of the range (inclusive).

        Returns:
            List of ``SLABreach`` rows.
        """
        stmt = (
            select(SLABreach)
            .join(Incident, SLABreach.incident_id == Incident.id)
            .where(
                Incident.org_id == org_id,
                SLABreach.breached_at >= start_date,
                SLABreach.breached_at <= end_date,
            )
            .order_by(SLABreach.breached_at.desc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    # ------------------------------------------------------------------
    # Compliance analytics
    # ------------------------------------------------------------------

    async def calculate_compliance_rate(
        self,
        org_id: uuid.UUID,
        start_date: datetime,
        end_date: datetime,
    ) -> dict[str, Any]:
        """Calculate SLA compliance rate for a date range.

        Returns:
            Dict with ``total_incidents``, ``within_sla``, ``breached``,
            ``compliance_percentage``, and ``by_severity`` breakdown.
        """
        # Total incidents in range
        stmt_total = select(Incident).where(
            Incident.org_id == org_id,
            Incident.created_at >= start_date,
            Incident.created_at <= end_date,
        )
        result_total = await self._session.execute(stmt_total)
        all_incidents = list(result_total.scalars().all())
        total = len(all_incidents)

        # Distinct incidents with breaches
        stmt_breached = (
            select(func.count(func.distinct(SLABreach.incident_id)))
            .join(Incident, SLABreach.incident_id == Incident.id)
            .where(
                Incident.org_id == org_id,
                Incident.created_at >= start_date,
                Incident.created_at <= end_date,
            )
        )
        result_breached = await self._session.execute(stmt_breached)
        breached_count = result_breached.scalar() or 0

        within_sla = total - breached_count
        compliance_pct = (within_sla / total * 100) if total > 0 else 100.0

        # By severity breakdown
        by_severity: dict[str, dict[str, Any]] = {}
        severity_groups: dict[str, list[Incident]] = {}
        for inc in all_incidents:
            severity_groups.setdefault(inc.severity, []).append(inc)

        for sev, incidents in severity_groups.items():
            sev_total = len(incidents)
            sev_incident_ids = [inc.id for inc in incidents]
            stmt_sev_breach = select(
                func.count(func.distinct(SLABreach.incident_id))
            ).where(SLABreach.incident_id.in_(sev_incident_ids))
            result_sev = await self._session.execute(stmt_sev_breach)
            sev_breached = result_sev.scalar() or 0
            sev_within = sev_total - sev_breached
            by_severity[sev] = {
                "total": sev_total,
                "within_sla": sev_within,
                "breached": sev_breached,
                "compliance_percentage": round(
                    (sev_within / sev_total * 100) if sev_total > 0 else 100.0, 2
                ),
            }

        return {
            "total_incidents": total,
            "within_sla": within_sla,
            "breached": breached_count,
            "compliance_percentage": round(compliance_pct, 2),
            "by_severity": by_severity,
        }

    async def calculate_mtta(
        self,
        org_id: uuid.UUID,
        start_date: datetime,
        end_date: datetime,
    ) -> float:
        """Calculate Mean Time To Acknowledge in seconds.

        Measures the average time from incident creation to the first
        status change (away from TRIGGERED).

        Returns:
            Average seconds to acknowledge, or ``0.0`` if no data.
        """
        stmt = select(Incident).where(
            Incident.org_id == org_id,
            Incident.created_at >= start_date,
            Incident.created_at <= end_date,
        )
        result = await self._session.execute(stmt)
        incidents = result.scalars().all()

        ack_times: list[float] = []
        for incident in incidents:
            ack_time = await self._get_ack_time(incident.id)
            if ack_time is not None:
                created = incident.created_at
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                if ack_time.tzinfo is None:
                    ack_time = ack_time.replace(tzinfo=timezone.utc)
                delta = (ack_time - created).total_seconds()
                if delta >= 0:
                    ack_times.append(delta)

        return (sum(ack_times) / len(ack_times)) if ack_times else 0.0

    async def calculate_mttr(
        self,
        org_id: uuid.UUID,
        start_date: datetime,
        end_date: datetime,
    ) -> float:
        """Calculate Mean Time To Resolve in seconds.

        Measures the average time from incident creation to resolution.

        Returns:
            Average seconds to resolve, or ``0.0`` if no data.
        """
        stmt = select(Incident).where(
            Incident.org_id == org_id,
            Incident.created_at >= start_date,
            Incident.created_at <= end_date,
            Incident.resolved_at.isnot(None),
        )
        result = await self._session.execute(stmt)
        incidents = result.scalars().all()

        resolve_times: list[float] = []
        for incident in incidents:
            created = incident.created_at
            resolved = incident.resolved_at
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            if resolved is not None:
                if resolved.tzinfo is None:
                    resolved = resolved.replace(tzinfo=timezone.utc)
                delta = (resolved - created).total_seconds()
                if delta >= 0:
                    resolve_times.append(delta)

        return (sum(resolve_times) / len(resolve_times)) if resolve_times else 0.0

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get_ack_time(self, incident_id: uuid.UUID) -> datetime | None:
        """Get the timestamp of the first status change for an incident."""
        stmt = (
            select(TimelineEvent.created_at)
            .where(
                TimelineEvent.incident_id == incident_id,
                TimelineEvent.event_type == TimelineEventTypeEnum.STATUS_CHANGE,
            )
            .order_by(TimelineEvent.created_at.asc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        return row
