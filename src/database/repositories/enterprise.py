"""Async repository for enterprise models: AlertRule, OnCallSchedule,
SLAPolicy, SLABreach, RunbookExecution, IncidentReport."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import (
    AlertRule,
    Incident,
    IncidentReport,
    IncidentStatusEnum,
    OnCallSchedule,
    ReportTypeEnum,
    RunbookExecution,
    RunbookStatusEnum,
    SLABreach,
    SLABreachTypeEnum,
    SLAPolicy,
)


class EnterpriseRepository:
    """Data-access layer for enterprise / operational models."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ==================================================================
    # Alert Rules
    # ==================================================================

    async def create_alert_rule(self, data: dict[str, Any]) -> AlertRule:
        """Create a new alert rule.

        Args:
            data: Column values for ``AlertRule``.

        Returns:
            The created ``AlertRule``.
        """
        rule = AlertRule(**data)
        self._session.add(rule)
        await self._session.flush()
        await self._session.refresh(rule)
        return rule

    async def list_alert_rules(
        self,
        org_id: uuid.UUID,
        *,
        active_only: bool = False,
    ) -> Sequence[AlertRule]:
        """List alert rules for an organization.

        Args:
            org_id: Organization UUID.
            active_only: If ``True``, return only active rules.

        Returns:
            Sequence of ``AlertRule`` rows.
        """
        stmt = select(AlertRule).where(AlertRule.org_id == org_id)
        if active_only:
            stmt = stmt.where(AlertRule.is_active.is_(True))
        stmt = stmt.order_by(AlertRule.created_at.desc())
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def update_alert_rule(
        self, rule_id: uuid.UUID, **fields: Any
    ) -> AlertRule | None:
        """Update fields on an alert rule.

        Args:
            rule_id: AlertRule UUID.
            **fields: Column names and new values.

        Returns:
            Updated ``AlertRule`` or ``None`` if not found.
        """
        stmt = select(AlertRule).where(AlertRule.id == rule_id)
        result = await self._session.execute(stmt)
        rule = result.scalar_one_or_none()
        if rule is None:
            return None
        for key, value in fields.items():
            setattr(rule, key, value)
        rule.updated_at = datetime.now(timezone.utc)
        await self._session.flush()
        await self._session.refresh(rule)
        return rule

    async def delete_alert_rule(self, rule_id: uuid.UUID) -> bool:
        """Delete an alert rule by UUID.

        Args:
            rule_id: AlertRule UUID.

        Returns:
            ``True`` if a row was deleted, ``False`` otherwise.
        """
        stmt = select(AlertRule).where(AlertRule.id == rule_id)
        result = await self._session.execute(stmt)
        rule = result.scalar_one_or_none()
        if rule is None:
            return False
        await self._session.delete(rule)
        await self._session.flush()
        return True

    # ==================================================================
    # On-Call Schedules
    # ==================================================================

    async def create_oncall_schedule(self, data: dict[str, Any]) -> OnCallSchedule:
        """Create a new on-call schedule.

        Args:
            data: Column values for ``OnCallSchedule``.

        Returns:
            The created ``OnCallSchedule``.
        """
        schedule = OnCallSchedule(**data)
        self._session.add(schedule)
        await self._session.flush()
        await self._session.refresh(schedule)
        return schedule

    async def get_oncall_schedule(self, schedule_id: uuid.UUID) -> OnCallSchedule | None:
        """Get an on-call schedule by UUID.

        Args:
            schedule_id: Schedule UUID.

        Returns:
            ``OnCallSchedule`` or ``None``.
        """
        stmt = select(OnCallSchedule).where(OnCallSchedule.id == schedule_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_oncall_schedules(self, org_id: uuid.UUID) -> Sequence[OnCallSchedule]:
        """List all on-call schedules for an organization.

        Args:
            org_id: Organization UUID.

        Returns:
            Sequence of ``OnCallSchedule`` rows.
        """
        stmt = (
            select(OnCallSchedule)
            .where(OnCallSchedule.org_id == org_id)
            .order_by(OnCallSchedule.team_name.asc())
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def rotate_oncall(self, schedule_id: uuid.UUID) -> OnCallSchedule | None:
        """Advance the on-call rotation to the next member.

        Increments ``current_index`` modulo the number of members.

        Args:
            schedule_id: Schedule UUID.

        Returns:
            Updated ``OnCallSchedule`` or ``None``.
        """
        schedule = await self.get_oncall_schedule(schedule_id)
        if schedule is None:
            return None
        members = schedule.members if isinstance(schedule.members, list) else []
        if not members:
            return schedule
        schedule.current_index = (schedule.current_index + 1) % len(members)
        schedule.updated_at = datetime.now(timezone.utc)
        await self._session.flush()
        await self._session.refresh(schedule)
        return schedule

    async def get_current_oncall(
        self, team_name: str, org_id: uuid.UUID
    ) -> dict[str, Any] | None:
        """Return the currently on-call member for a team.

        Args:
            team_name: Name of the on-call team.
            org_id: Organization UUID.

        Returns:
            Dictionary with ``team_name``, ``current_member``, and
            ``schedule_id``, or ``None`` if the team is not found.
        """
        stmt = select(OnCallSchedule).where(
            OnCallSchedule.team_name == team_name,
            OnCallSchedule.org_id == org_id,
        )
        result = await self._session.execute(stmt)
        schedule = result.scalar_one_or_none()
        if schedule is None:
            return None
        members = schedule.members if isinstance(schedule.members, list) else []
        current_member = members[schedule.current_index] if members else None
        return {
            "team_name": schedule.team_name,
            "current_member": current_member,
            "schedule_id": schedule.id,
            "rotation_type": schedule.rotation_type.value if schedule.rotation_type else None,
        }

    # ==================================================================
    # SLA Policies
    # ==================================================================

    async def create_sla_policy(self, data: dict[str, Any]) -> SLAPolicy:
        """Create a new SLA policy.

        Args:
            data: Column values for ``SLAPolicy``.

        Returns:
            The created ``SLAPolicy``.
        """
        policy = SLAPolicy(**data)
        self._session.add(policy)
        await self._session.flush()
        await self._session.refresh(policy)
        return policy

    async def list_sla_policies(
        self,
        org_id: uuid.UUID,
        *,
        active_only: bool = False,
    ) -> Sequence[SLAPolicy]:
        """List SLA policies for an organization.

        Args:
            org_id: Organization UUID.
            active_only: If ``True``, return only active policies.

        Returns:
            Sequence of ``SLAPolicy`` rows.
        """
        stmt = select(SLAPolicy).where(SLAPolicy.org_id == org_id)
        if active_only:
            stmt = stmt.where(SLAPolicy.is_active.is_(True))
        stmt = stmt.order_by(SLAPolicy.severity.asc())
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def check_sla_breach(
        self, incident: Incident, org_id: uuid.UUID
    ) -> list[dict[str, Any]]:
        """Check whether an incident has breached any active SLA policies.

        Compares elapsed time since ``created_at`` against the thresholds
        defined for the incident's severity.

        Args:
            incident: The ``Incident`` ORM object to check.
            org_id: Organization UUID (to look up policies).

        Returns:
            List of breach descriptors (may be empty if no breaches).
        """
        now = datetime.now(timezone.utc)
        elapsed_minutes = (now - incident.created_at).total_seconds() / 60.0

        stmt = select(SLAPolicy).where(
            SLAPolicy.org_id == org_id,
            SLAPolicy.severity == incident.severity,
            SLAPolicy.is_active.is_(True),
        )
        result = await self._session.execute(stmt)
        policies = result.scalars().all()

        breaches: list[dict[str, Any]] = []
        for policy in policies:
            if elapsed_minutes > policy.response_time_minutes:
                breaches.append({
                    "policy_id": policy.id,
                    "policy_name": policy.name,
                    "breach_type": SLABreachTypeEnum.RESPONSE,
                    "threshold_minutes": policy.response_time_minutes,
                    "elapsed_minutes": elapsed_minutes,
                })
            if elapsed_minutes > policy.acknowledge_time_minutes:
                breaches.append({
                    "policy_id": policy.id,
                    "policy_name": policy.name,
                    "breach_type": SLABreachTypeEnum.ACKNOWLEDGE,
                    "threshold_minutes": policy.acknowledge_time_minutes,
                    "elapsed_minutes": elapsed_minutes,
                })
            if (
                incident.resolved_at is None
                and elapsed_minutes > policy.resolution_time_minutes
            ):
                breaches.append({
                    "policy_id": policy.id,
                    "policy_name": policy.name,
                    "breach_type": SLABreachTypeEnum.RESOLUTION,
                    "threshold_minutes": policy.resolution_time_minutes,
                    "elapsed_minutes": elapsed_minutes,
                })
        return breaches

    async def record_breach(
        self,
        incident_id: uuid.UUID,
        sla_policy_id: uuid.UUID,
        breach_type: SLABreachTypeEnum | str,
    ) -> SLABreach:
        """Record an SLA breach event.

        Args:
            incident_id: Incident UUID (FK).
            sla_policy_id: SLAPolicy UUID (FK).
            breach_type: Type of SLA breach.

        Returns:
            The created ``SLABreach``.
        """
        if isinstance(breach_type, str):
            breach_type = SLABreachTypeEnum(breach_type)

        breach = SLABreach(
            incident_id=incident_id,
            sla_policy_id=sla_policy_id,
            breach_type=breach_type,
        )
        self._session.add(breach)
        await self._session.flush()
        await self._session.refresh(breach)
        return breach

    # ==================================================================
    # Runbook Executions
    # ==================================================================

    async def create_runbook_execution(self, data: dict[str, Any]) -> RunbookExecution:
        """Create a runbook execution record.

        Args:
            data: Column values for ``RunbookExecution``.

        Returns:
            The created ``RunbookExecution``.
        """
        execution = RunbookExecution(**data)
        self._session.add(execution)
        await self._session.flush()
        await self._session.refresh(execution)
        return execution

    async def update_execution_progress(
        self,
        execution_id: uuid.UUID,
        *,
        steps_completed: Optional[int] = None,
        status: Optional[RunbookStatusEnum | str] = None,
        output: Optional[dict[str, Any]] = None,
        completed_at: Optional[datetime] = None,
    ) -> RunbookExecution | None:
        """Update a runbook execution's progress.

        Args:
            execution_id: RunbookExecution UUID.
            steps_completed: Number of steps completed so far.
            status: New status value.
            output: Updated output JSON.
            completed_at: Completion timestamp.

        Returns:
            Updated ``RunbookExecution`` or ``None``.
        """
        stmt = select(RunbookExecution).where(RunbookExecution.id == execution_id)
        result = await self._session.execute(stmt)
        execution = result.scalar_one_or_none()
        if execution is None:
            return None

        if steps_completed is not None:
            execution.steps_completed = steps_completed
        if status is not None:
            if isinstance(status, str):
                status = RunbookStatusEnum(status)
            execution.status = status
        if output is not None:
            execution.output = output
        if completed_at is not None:
            execution.completed_at = completed_at

        await self._session.flush()
        await self._session.refresh(execution)
        return execution

    async def get_execution(self, execution_id: uuid.UUID) -> RunbookExecution | None:
        """Get a single runbook execution by UUID.

        Args:
            execution_id: RunbookExecution UUID.

        Returns:
            ``RunbookExecution`` or ``None``.
        """
        stmt = select(RunbookExecution).where(RunbookExecution.id == execution_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_executions(self, incident_id: uuid.UUID) -> Sequence[RunbookExecution]:
        """List all runbook executions for an incident.

        Args:
            incident_id: Incident UUID (the PK, not the human-readable ID).

        Returns:
            Sequence of ``RunbookExecution`` rows ordered by creation time.
        """
        stmt = (
            select(RunbookExecution)
            .where(RunbookExecution.incident_id == incident_id)
            .order_by(RunbookExecution.created_at.desc())
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    # ==================================================================
    # Incident Reports
    # ==================================================================

    async def create_report(self, data: dict[str, Any]) -> IncidentReport:
        """Create an incident report.

        Args:
            data: Column values for ``IncidentReport``.

        Returns:
            The created ``IncidentReport``.
        """
        report = IncidentReport(**data)
        self._session.add(report)
        await self._session.flush()
        await self._session.refresh(report)
        return report

    async def get_report(self, report_id: uuid.UUID) -> IncidentReport | None:
        """Get a single report by UUID.

        Args:
            report_id: IncidentReport UUID.

        Returns:
            ``IncidentReport`` or ``None``.
        """
        stmt = select(IncidentReport).where(IncidentReport.id == report_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_reports(
        self,
        org_id: uuid.UUID,
        *,
        report_type: Optional[ReportTypeEnum | str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Sequence[IncidentReport]:
        """List reports for an organization.

        Args:
            org_id: Organization UUID.
            report_type: Optional filter by report type.
            limit: Maximum rows.
            offset: Pagination offset.

        Returns:
            Sequence of ``IncidentReport`` rows ordered newest-first.
        """
        stmt = select(IncidentReport).where(IncidentReport.org_id == org_id)
        if report_type is not None:
            if isinstance(report_type, str):
                report_type = ReportTypeEnum(report_type)
            stmt = stmt.where(IncidentReport.report_type == report_type)
        stmt = stmt.order_by(IncidentReport.created_at.desc()).limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        return result.scalars().all()
