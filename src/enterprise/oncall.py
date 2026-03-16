"""On-call scheduling, rotation, and escalation management."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import (
    Incident,
    IncidentStatusEnum,
    OnCallSchedule,
    RotationTypeEnum,
    TimelineEvent,
    TimelineEventTypeEnum,
)

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class EscalationPolicy:
    """Configuration for escalation behaviour."""

    timeout_minutes_per_level: list[int] = field(
        default_factory=lambda: [15, 30, 60, 120]
    )
    max_levels: int = 4
    notify_methods: list[str] = field(
        default_factory=lambda: ["slack", "email", "phone"]
    )


# ---------------------------------------------------------------------------
# OnCallManager
# ---------------------------------------------------------------------------

class OnCallManager:
    """Manages on-call schedules, rotations, and escalation chains."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Schedule CRUD
    # ------------------------------------------------------------------

    async def create_schedule(
        self,
        team_name: str,
        rotation_type: str,
        members: list[dict[str, Any]],
        escalation_policy: dict[str, Any],
        org_id: uuid.UUID,
    ) -> OnCallSchedule:
        """Create a new on-call schedule for a team.

        Args:
            team_name: Unique team identifier within the org.
            rotation_type: One of ``daily``, ``weekly``, ``custom``.
            members: List of member dicts, each with at least
                ``name``, ``email``, and optionally ``phone``, ``role``.
            escalation_policy: Escalation configuration stored as JSON.
            org_id: Owning organization UUID.

        Returns:
            The persisted ``OnCallSchedule``.
        """
        try:
            rot = RotationTypeEnum(rotation_type)
        except ValueError:
            rot = RotationTypeEnum.WEEKLY

        schedule = OnCallSchedule(
            team_name=team_name,
            rotation_type=rot,
            members=members,
            current_index=0,
            escalation_policy=escalation_policy,
            org_id=org_id,
        )
        self._session.add(schedule)
        await self._session.flush()
        await self._session.refresh(schedule)
        logger.info(
            "oncall.schedule_created",
            team=team_name,
            members_count=len(members),
            rotation=rotation_type,
        )
        return schedule

    async def get_current_oncall(
        self, team_name: str, org_id: uuid.UUID
    ) -> dict[str, Any] | None:
        """Return the member currently on call for a given team.

        Returns:
            Dict with ``name``, ``email``, ``phone``, ``role``, plus
            schedule metadata, or ``None`` if no schedule exists.
        """
        schedule = await self._get_schedule(team_name, org_id)
        if schedule is None:
            return None

        members: list[dict[str, Any]] = (
            schedule.members if isinstance(schedule.members, list) else []
        )
        if not members:
            return {
                "team_name": team_name,
                "name": None,
                "email": None,
                "phone": None,
                "role": None,
                "schedule_id": str(schedule.id),
                "rotation_type": schedule.rotation_type.value,
                "current_index": schedule.current_index,
            }

        current = members[schedule.current_index % len(members)]
        return {
            "team_name": team_name,
            "name": current.get("name"),
            "email": current.get("email"),
            "phone": current.get("phone"),
            "role": current.get("role", "responder"),
            "schedule_id": str(schedule.id),
            "rotation_type": schedule.rotation_type.value,
            "current_index": schedule.current_index,
        }

    # ------------------------------------------------------------------
    # Rotation
    # ------------------------------------------------------------------

    async def rotate(
        self, team_name: str, org_id: uuid.UUID
    ) -> OnCallSchedule | None:
        """Advance the on-call rotation to the next member.

        Returns:
            Updated ``OnCallSchedule`` or ``None`` if not found.
        """
        schedule = await self._get_schedule(team_name, org_id)
        if schedule is None:
            return None

        members: list[dict[str, Any]] = (
            schedule.members if isinstance(schedule.members, list) else []
        )
        if not members:
            return schedule

        old_index = schedule.current_index
        schedule.current_index = (schedule.current_index + 1) % len(members)
        schedule.updated_at = datetime.now(timezone.utc)
        await self._session.flush()
        await self._session.refresh(schedule)

        logger.info(
            "oncall.rotated",
            team=team_name,
            old_index=old_index,
            new_index=schedule.current_index,
            new_member=members[schedule.current_index].get("name"),
        )
        return schedule

    async def auto_rotate_if_needed(self, schedule: OnCallSchedule) -> bool:
        """Check if the rotation period has elapsed and rotate if so.

        Uses ``updated_at`` as the last rotation timestamp.

        Returns:
            ``True`` if a rotation was performed, ``False`` otherwise.
        """
        members: list[dict[str, Any]] = (
            schedule.members if isinstance(schedule.members, list) else []
        )
        if not members:
            return False

        now = datetime.now(timezone.utc)
        last_rotation = schedule.updated_at
        if last_rotation.tzinfo is None:
            last_rotation = last_rotation.replace(tzinfo=timezone.utc)

        if schedule.rotation_type == RotationTypeEnum.DAILY:
            rotation_period = timedelta(days=1)
        elif schedule.rotation_type == RotationTypeEnum.WEEKLY:
            rotation_period = timedelta(weeks=1)
        else:
            # Custom: check escalation_policy for period_hours, default weekly
            policy = schedule.escalation_policy or {}
            hours = policy.get("rotation_period_hours", 168)
            rotation_period = timedelta(hours=hours)

        if (now - last_rotation) >= rotation_period:
            old_index = schedule.current_index
            schedule.current_index = (schedule.current_index + 1) % len(members)
            schedule.updated_at = now
            await self._session.flush()
            await self._session.refresh(schedule)
            logger.info(
                "oncall.auto_rotated",
                team=schedule.team_name,
                old_index=old_index,
                new_index=schedule.current_index,
            )
            return True

        return False

    # ------------------------------------------------------------------
    # Escalation
    # ------------------------------------------------------------------

    async def escalate(
        self,
        incident_id: uuid.UUID,
        team_name: str,
        org_id: uuid.UUID,
        level: int = 0,
    ) -> dict[str, Any]:
        """Escalate an incident through the on-call chain.

        Escalation levels:
            0 - Notify current on-call responder.
            1 - Notify on-call responder + team lead.
            2 - Notify all team members.
            3 - Notify all team members + engineering manager.

        Args:
            incident_id: The incident to escalate.
            team_name: Target on-call team.
            org_id: Organization UUID.
            level: Escalation level (0-3).

        Returns:
            Dict describing who was notified and at what level.
        """
        schedule = await self._get_schedule(team_name, org_id)
        if schedule is None:
            return {
                "success": False,
                "error": f"No on-call schedule found for team '{team_name}'",
                "level": level,
                "notified": [],
            }

        members: list[dict[str, Any]] = (
            schedule.members if isinstance(schedule.members, list) else []
        )
        if not members:
            return {
                "success": False,
                "error": "Schedule has no members",
                "level": level,
                "notified": [],
            }

        policy = schedule.escalation_policy or {}
        notified: list[dict[str, Any]] = []

        # Current on-call
        current = members[schedule.current_index % len(members)]

        if level >= 0:
            notified.append({
                "name": current.get("name"),
                "email": current.get("email"),
                "phone": current.get("phone"),
                "role": "on-call",
                "notify_reason": "Current on-call responder",
            })

        if level >= 1:
            # Add team lead(s)
            for m in members:
                if m.get("role") in ("lead", "team_lead") and m.get("email") != current.get("email"):
                    notified.append({
                        "name": m.get("name"),
                        "email": m.get("email"),
                        "phone": m.get("phone"),
                        "role": "team_lead",
                        "notify_reason": "Escalation level 1 - team lead",
                    })
            # If no explicit lead, add the next person in rotation
            if len(notified) == 1 and len(members) > 1:
                next_idx = (schedule.current_index + 1) % len(members)
                backup = members[next_idx]
                notified.append({
                    "name": backup.get("name"),
                    "email": backup.get("email"),
                    "phone": backup.get("phone"),
                    "role": "backup",
                    "notify_reason": "Escalation level 1 - backup responder",
                })

        if level >= 2:
            # All team members
            existing_emails = {n.get("email") for n in notified}
            for m in members:
                if m.get("email") not in existing_emails:
                    notified.append({
                        "name": m.get("name"),
                        "email": m.get("email"),
                        "phone": m.get("phone"),
                        "role": m.get("role", "responder"),
                        "notify_reason": "Escalation level 2 - full team",
                    })

        if level >= 3:
            # Engineering manager from policy
            eng_manager = policy.get("engineering_manager")
            if eng_manager:
                existing_emails = {n.get("email") for n in notified}
                if eng_manager.get("email") not in existing_emails:
                    notified.append({
                        "name": eng_manager.get("name"),
                        "email": eng_manager.get("email"),
                        "phone": eng_manager.get("phone"),
                        "role": "engineering_manager",
                        "notify_reason": "Escalation level 3 - engineering manager",
                    })

        # Record escalation timeline event
        event = TimelineEvent(
            incident_id=incident_id,
            event_type=TimelineEventTypeEnum.ESCALATION,
            message=(
                f"Incident escalated to level {level} for team '{team_name}'. "
                f"Notified {len(notified)} contact(s)."
            ),
            actor="oncall-manager",
            metadata_={
                "escalation_level": level,
                "team_name": team_name,
                "notified_contacts": [n.get("email") for n in notified],
            },
        )
        self._session.add(event)
        await self._session.flush()

        logger.info(
            "oncall.escalated",
            incident_id=str(incident_id),
            team=team_name,
            level=level,
            notified_count=len(notified),
        )

        return {
            "success": True,
            "incident_id": str(incident_id),
            "team_name": team_name,
            "level": level,
            "notified": notified,
            "notify_methods": policy.get(
                "notify_methods", ["slack", "email"]
            ),
        }

    async def get_escalation_chain(
        self, team_name: str, org_id: uuid.UUID
    ) -> list[dict[str, Any]]:
        """Return the full escalation chain for a team, ordered by level.

        Returns:
            List of contact dicts, each annotated with ``level``.
        """
        schedule = await self._get_schedule(team_name, org_id)
        if schedule is None:
            return []

        members: list[dict[str, Any]] = (
            schedule.members if isinstance(schedule.members, list) else []
        )
        if not members:
            return []

        policy = schedule.escalation_policy or {}
        chain: list[dict[str, Any]] = []

        # Level 0: current on-call
        current = members[schedule.current_index % len(members)]
        chain.append({
            "level": 0,
            "name": current.get("name"),
            "email": current.get("email"),
            "phone": current.get("phone"),
            "role": "on-call",
        })

        # Level 1: team lead or backup
        leads = [m for m in members if m.get("role") in ("lead", "team_lead")]
        if leads:
            for lead in leads:
                if lead.get("email") != current.get("email"):
                    chain.append({
                        "level": 1,
                        "name": lead.get("name"),
                        "email": lead.get("email"),
                        "phone": lead.get("phone"),
                        "role": "team_lead",
                    })
        else:
            # Next in rotation as backup
            if len(members) > 1:
                next_idx = (schedule.current_index + 1) % len(members)
                backup = members[next_idx]
                chain.append({
                    "level": 1,
                    "name": backup.get("name"),
                    "email": backup.get("email"),
                    "phone": backup.get("phone"),
                    "role": "backup",
                })

        # Level 2: remaining team members
        chain_emails = {c.get("email") for c in chain}
        for m in members:
            if m.get("email") not in chain_emails:
                chain.append({
                    "level": 2,
                    "name": m.get("name"),
                    "email": m.get("email"),
                    "phone": m.get("phone"),
                    "role": m.get("role", "responder"),
                })

        # Level 3: engineering manager
        eng_manager = policy.get("engineering_manager")
        if eng_manager:
            chain.append({
                "level": 3,
                "name": eng_manager.get("name"),
                "email": eng_manager.get("email"),
                "phone": eng_manager.get("phone"),
                "role": "engineering_manager",
            })

        return chain

    async def check_acknowledgment(
        self, incident_id: uuid.UUID, timeout_minutes: int = 15
    ) -> bool:
        """Check if an incident was acknowledged within the SLA timeout.

        Looks for a status transition away from ``TRIGGERED`` within
        ``timeout_minutes`` of creation.

        Returns:
            ``True`` if acknowledged in time, ``False`` otherwise.
        """
        stmt = select(Incident).where(Incident.id == incident_id)
        result = await self._session.execute(stmt)
        incident = result.scalar_one_or_none()
        if incident is None:
            return False

        # If still in TRIGGERED state, check elapsed time
        if incident.status == IncidentStatusEnum.TRIGGERED:
            now = datetime.now(timezone.utc)
            created = incident.created_at
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            elapsed = (now - created).total_seconds() / 60.0
            return elapsed <= timeout_minutes

        # Check timeline for first status change
        stmt_events = (
            select(TimelineEvent)
            .where(
                TimelineEvent.incident_id == incident_id,
                TimelineEvent.event_type == TimelineEventTypeEnum.STATUS_CHANGE,
            )
            .order_by(TimelineEvent.created_at.asc())
            .limit(1)
        )
        result_events = await self._session.execute(stmt_events)
        first_change = result_events.scalar_one_or_none()

        if first_change is None:
            return False

        created = incident.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        ack_time = first_change.created_at
        if ack_time.tzinfo is None:
            ack_time = ack_time.replace(tzinfo=timezone.utc)

        elapsed = (ack_time - created).total_seconds() / 60.0
        return elapsed <= timeout_minutes

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get_schedule(
        self, team_name: str, org_id: uuid.UUID
    ) -> OnCallSchedule | None:
        """Fetch a schedule by team name and org."""
        stmt = select(OnCallSchedule).where(
            OnCallSchedule.team_name == team_name,
            OnCallSchedule.org_id == org_id,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()
