"""FastAPI router for all enterprise endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.middleware import get_current_active_user, require_role
from src.database.engine import get_db
from src.database.models import User, UserRole
from src.database.repositories.enterprise import EnterpriseRepository
from src.enterprise.alert_rules import AlertRuleEngine
from src.enterprise.oncall import OnCallManager
from src.enterprise.reporting import ReportGenerator
from src.enterprise.runbook_automation import RunbookExecutor, RunbookStep
from src.enterprise.sla import SLATracker

logger = structlog.get_logger()

router = APIRouter(prefix="/api/enterprise", tags=["enterprise"])


# ======================================================================
# Pydantic request / response schemas
# ======================================================================

# -- On-Call --

class OnCallMemberSchema(BaseModel):
    name: str
    email: str
    phone: Optional[str] = None
    role: Optional[str] = "responder"


class CreateScheduleRequest(BaseModel):
    team_name: str
    rotation_type: str = "weekly"
    members: list[OnCallMemberSchema]
    escalation_policy: dict[str, Any] = Field(default_factory=dict)


class EscalateRequest(BaseModel):
    incident_id: str
    team_name: str
    level: int = 0


class ScheduleResponse(BaseModel):
    id: str
    team_name: str
    rotation_type: str
    members: list[dict[str, Any]]
    current_index: int
    escalation_policy: dict[str, Any] | None
    created_at: str
    updated_at: str

    class Config:
        from_attributes = True


# -- SLA --

class CreateSLAPolicyRequest(BaseModel):
    name: str
    severity: str
    response_minutes: int
    ack_minutes: int
    resolution_minutes: int


class SLAPolicyResponse(BaseModel):
    id: str
    name: str
    severity: str
    response_time_minutes: int
    acknowledge_time_minutes: int
    resolution_time_minutes: int
    is_active: bool
    created_at: str

    class Config:
        from_attributes = True


class SLAStatusResponse(BaseModel):
    incident_id: str
    incident_title: str
    severity: str
    is_breached: bool
    breach_type: Optional[str]
    time_remaining_seconds: float
    percentage_elapsed: float
    policy: Optional[dict[str, Any]]


# -- Alert Rules --

class CreateAlertRuleRequest(BaseModel):
    name: str
    description: str = ""
    condition_type: str
    condition_config: dict[str, Any]
    action_type: str
    action_config: dict[str, Any] = Field(default_factory=dict)


class UpdateAlertRuleRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    condition_type: Optional[str] = None
    condition_config: Optional[dict[str, Any]] = None
    action_type: Optional[str] = None
    action_config: Optional[dict[str, Any]] = None


class AlertRuleResponse(BaseModel):
    id: str
    name: str
    description: Optional[str]
    condition_type: str
    condition_config: dict[str, Any]
    action_type: str
    action_config: dict[str, Any]
    is_active: bool
    created_at: str
    updated_at: str

    class Config:
        from_attributes = True


# -- Runbook --

class RunbookStepRequest(BaseModel):
    index: int
    name: str
    type: str  # manual, command, api_call, k8s, approval
    config: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int = 300


class StartRunbookRequest(BaseModel):
    incident_id: str
    runbook_name: str
    runbook_notion_id: Optional[str] = None
    steps: list[RunbookStepRequest]


class ApproveRejectRequest(BaseModel):
    user: str = "admin"
    reason: str = ""


class CancelRequest(BaseModel):
    reason: str = ""


# -- Reporting --

class GenerateReportRequest(BaseModel):
    report_type: str  # daily, weekly, monthly, custom
    date: Optional[str] = None  # ISO format date
    week_start: Optional[str] = None
    month: Optional[int] = None
    year: Optional[int] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    filters: Optional[dict[str, Any]] = None


class ReportResponse(BaseModel):
    id: str
    report_type: str
    title: str
    content: Optional[str]
    data: Optional[dict[str, Any]]
    period_start: Optional[str]
    period_end: Optional[str]
    created_at: str

    class Config:
        from_attributes = True


# ======================================================================
# Helper functions
# ======================================================================

def _schedule_to_response(schedule) -> dict[str, Any]:
    """Convert an OnCallSchedule ORM object to a response dict."""
    return {
        "id": str(schedule.id),
        "team_name": schedule.team_name,
        "rotation_type": schedule.rotation_type.value if schedule.rotation_type else "weekly",
        "members": schedule.members if isinstance(schedule.members, list) else [],
        "current_index": schedule.current_index,
        "escalation_policy": schedule.escalation_policy,
        "created_at": schedule.created_at.isoformat(),
        "updated_at": schedule.updated_at.isoformat(),
    }


def _policy_to_response(policy) -> dict[str, Any]:
    """Convert an SLAPolicy ORM object to a response dict."""
    return {
        "id": str(policy.id),
        "name": policy.name,
        "severity": policy.severity,
        "response_time_minutes": policy.response_time_minutes,
        "acknowledge_time_minutes": policy.acknowledge_time_minutes,
        "resolution_time_minutes": policy.resolution_time_minutes,
        "is_active": policy.is_active,
        "created_at": policy.created_at.isoformat(),
    }


def _rule_to_response(rule) -> dict[str, Any]:
    """Convert an AlertRule ORM object to a response dict."""
    return {
        "id": str(rule.id),
        "name": rule.name,
        "description": rule.description,
        "condition_type": rule.condition_type.value if rule.condition_type else "threshold",
        "condition_config": rule.condition_config or {},
        "action_type": rule.action_type.value if rule.action_type else "create_incident",
        "action_config": rule.action_config or {},
        "is_active": rule.is_active,
        "created_at": rule.created_at.isoformat(),
        "updated_at": rule.updated_at.isoformat(),
    }


def _report_to_response(report) -> dict[str, Any]:
    """Convert an IncidentReport ORM object to a response dict."""
    return {
        "id": str(report.id),
        "report_type": report.report_type.value if report.report_type else "custom",
        "title": report.title,
        "content": report.content,
        "data": report.data,
        "period_start": report.period_start.isoformat() if report.period_start else None,
        "period_end": report.period_end.isoformat() if report.period_end else None,
        "created_at": report.created_at.isoformat(),
    }


def _parse_uuid(value: str, label: str = "ID") -> uuid.UUID:
    """Parse a string to UUID, raising 400 on failure."""
    try:
        return uuid.UUID(value)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid {label}: {value}",
        )


def _parse_datetime(value: str, label: str = "date") -> datetime:
    """Parse an ISO date/datetime string, raising 400 on failure."""
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid {label} format: {value}. Use ISO 8601 (e.g. 2026-03-15).",
        )


# ======================================================================
# On-Call Endpoints
# ======================================================================

@router.post("/oncall/schedules", response_model=ScheduleResponse, status_code=201)
async def create_oncall_schedule(
    req: CreateScheduleRequest,
    user: User = Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    """Create a new on-call schedule. Requires admin role."""
    mgr = OnCallManager(db)
    schedule = await mgr.create_schedule(
        team_name=req.team_name,
        rotation_type=req.rotation_type,
        members=[m.model_dump() for m in req.members],
        escalation_policy=req.escalation_policy,
        org_id=user.org_id,
    )
    return _schedule_to_response(schedule)


@router.get("/oncall/schedules")
async def list_oncall_schedules(
    user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """List all on-call schedules for the user's organization."""
    repo = EnterpriseRepository(db)
    schedules = await repo.list_oncall_schedules(user.org_id)
    return [_schedule_to_response(s) for s in schedules]


@router.get("/oncall/schedules/{team}")
async def get_oncall_schedule(
    team: str,
    user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a specific on-call schedule with the current on-call member."""
    mgr = OnCallManager(db)
    current = await mgr.get_current_oncall(team, user.org_id)
    if current is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No on-call schedule found for team '{team}'",
        )
    return current


@router.post("/oncall/schedules/{team}/rotate")
async def rotate_oncall(
    team: str,
    user: User = Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    """Manually rotate the on-call schedule for a team. Requires admin role."""
    mgr = OnCallManager(db)
    schedule = await mgr.rotate(team, user.org_id)
    if schedule is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No on-call schedule found for team '{team}'",
        )
    return _schedule_to_response(schedule)


@router.post("/oncall/escalate")
async def escalate_incident(
    req: EscalateRequest,
    user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Escalate an incident to the on-call team at a specified level."""
    incident_uuid = _parse_uuid(req.incident_id, "incident_id")
    mgr = OnCallManager(db)
    result = await mgr.escalate(
        incident_id=incident_uuid,
        team_name=req.team_name,
        org_id=user.org_id,
        level=req.level,
    )
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=result.get("error", "Escalation failed"),
        )
    return result


# ======================================================================
# SLA Endpoints
# ======================================================================

@router.post("/sla/policies", response_model=SLAPolicyResponse, status_code=201)
async def create_sla_policy(
    req: CreateSLAPolicyRequest,
    user: User = Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    """Create a new SLA policy. Requires admin role."""
    tracker = SLATracker(db)
    policy = await tracker.create_policy(
        name=req.name,
        severity=req.severity,
        response_minutes=req.response_minutes,
        ack_minutes=req.ack_minutes,
        resolution_minutes=req.resolution_minutes,
        org_id=user.org_id,
    )
    return _policy_to_response(policy)


@router.get("/sla/policies")
async def list_sla_policies(
    user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """List all SLA policies for the user's organization."""
    repo = EnterpriseRepository(db)
    policies = await repo.list_sla_policies(user.org_id, active_only=False)
    return [_policy_to_response(p) for p in policies]


@router.get("/sla/status")
async def check_sla_status(
    user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Check SLA status for all active incidents in the organization."""
    tracker = SLATracker(db)
    statuses = await tracker.check_all_active_incidents(user.org_id)
    return [
        {
            "incident_id": str(s.incident_id),
            "incident_title": s.incident_title,
            "severity": s.severity,
            "is_breached": s.is_breached,
            "breach_type": s.breach_type,
            "time_remaining_seconds": round(s.time_remaining_seconds, 1),
            "percentage_elapsed": round(s.percentage_elapsed, 2),
            "policy": s.policy,
        }
        for s in statuses
    ]


@router.get("/sla/compliance")
async def get_sla_compliance(
    start_date: str = Query(..., description="Start date (ISO 8601)"),
    end_date: str = Query(..., description="End date (ISO 8601)"),
    user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Get SLA compliance report for a date range including MTTA and MTTR."""
    start = _parse_datetime(start_date, "start_date")
    end = _parse_datetime(end_date, "end_date")

    tracker = SLATracker(db)
    compliance = await tracker.calculate_compliance_rate(user.org_id, start, end)
    mtta = await tracker.calculate_mtta(user.org_id, start, end)
    mttr = await tracker.calculate_mttr(user.org_id, start, end)

    return {
        **compliance,
        "mtta_seconds": round(mtta, 2),
        "mttr_seconds": round(mttr, 2),
        "mtta_minutes": round(mtta / 60, 2) if mtta > 0 else 0,
        "mttr_minutes": round(mttr / 60, 2) if mttr > 0 else 0,
    }


# ======================================================================
# Alert Rules Endpoints
# ======================================================================

@router.post("/alert-rules", response_model=AlertRuleResponse, status_code=201)
async def create_alert_rule(
    req: CreateAlertRuleRequest,
    user: User = Depends(require_role(UserRole.COMMANDER)),
    db: AsyncSession = Depends(get_db),
):
    """Create a new alert rule. Requires commander or admin role."""
    engine = AlertRuleEngine(db)
    rule = await engine.create_rule(
        name=req.name,
        description=req.description,
        condition_type=req.condition_type,
        condition_config=req.condition_config,
        action_type=req.action_type,
        action_config=req.action_config,
        org_id=user.org_id,
        created_by=user.id,
    )
    return _rule_to_response(rule)


@router.get("/alert-rules")
async def list_alert_rules(
    is_active: Optional[bool] = Query(None, description="Filter by active status"),
    user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """List all alert rules for the user's organization."""
    engine = AlertRuleEngine(db)
    rules = await engine.list_rules(user.org_id, is_active=is_active)
    return [_rule_to_response(r) for r in rules]


@router.put("/alert-rules/{rule_id}", response_model=AlertRuleResponse)
async def update_alert_rule(
    rule_id: str,
    req: UpdateAlertRuleRequest,
    user: User = Depends(require_role(UserRole.COMMANDER)),
    db: AsyncSession = Depends(get_db),
):
    """Update an existing alert rule. Requires commander or admin role."""
    rid = _parse_uuid(rule_id, "rule_id")
    engine = AlertRuleEngine(db)

    updates = req.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No fields to update",
        )

    rule = await engine.update_rule(rid, updates)
    if rule is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Alert rule {rule_id} not found",
        )
    return _rule_to_response(rule)


@router.delete("/alert-rules/{rule_id}", status_code=204)
async def delete_alert_rule(
    rule_id: str,
    user: User = Depends(require_role(UserRole.COMMANDER)),
    db: AsyncSession = Depends(get_db),
):
    """Delete an alert rule. Requires commander or admin role."""
    rid = _parse_uuid(rule_id, "rule_id")
    engine = AlertRuleEngine(db)
    deleted = await engine.delete_rule(rid)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Alert rule {rule_id} not found",
        )
    return None


@router.post("/alert-rules/{rule_id}/toggle", response_model=AlertRuleResponse)
async def toggle_alert_rule(
    rule_id: str,
    user: User = Depends(require_role(UserRole.COMMANDER)),
    db: AsyncSession = Depends(get_db),
):
    """Toggle an alert rule on or off. Requires commander or admin role."""
    rid = _parse_uuid(rule_id, "rule_id")
    engine = AlertRuleEngine(db)
    rule = await engine.toggle_rule(rid)
    if rule is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Alert rule {rule_id} not found",
        )
    return _rule_to_response(rule)


# ======================================================================
# Runbook Automation Endpoints
# ======================================================================

@router.post("/runbooks/execute", status_code=201)
async def start_runbook_execution(
    req: StartRunbookRequest,
    user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Start a runbook execution for an incident."""
    incident_uuid = _parse_uuid(req.incident_id, "incident_id")
    executor = RunbookExecutor(db)

    steps = [
        RunbookStep(
            index=s.index,
            name=s.name,
            type=s.type,
            config=s.config,
            timeout_seconds=s.timeout_seconds,
        )
        for s in req.steps
    ]

    execution = await executor.start_execution(
        incident_id=incident_uuid,
        runbook_name=req.runbook_name,
        runbook_notion_id=req.runbook_notion_id,
        steps=steps,
        executed_by=user.id,
    )

    return {
        "execution_id": str(execution.id),
        "status": execution.status.value,
        "runbook_name": execution.runbook_name,
        "steps_total": execution.steps_total,
    }


@router.get("/runbooks/executions/{execution_id}")
async def get_execution_status(
    execution_id: str,
    user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Get the status of a runbook execution including all step results."""
    eid = _parse_uuid(execution_id, "execution_id")
    executor = RunbookExecutor(db)
    result = await executor.get_execution_status(eid)
    if "error" in result:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=result["error"],
        )
    return result


@router.post("/runbooks/executions/{execution_id}/approve/{step_index}")
async def approve_execution_step(
    execution_id: str,
    step_index: int,
    req: ApproveRejectRequest = ApproveRejectRequest(),
    user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Approve a pending manual or approval step in a runbook execution."""
    eid = _parse_uuid(execution_id, "execution_id")
    executor = RunbookExecutor(db)
    approved = await executor.approve_step(
        execution_id=eid,
        step_index=step_index,
        approved_by=req.user or user.name,
    )
    if not approved:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Step is not pending approval or execution not found",
        )
    return {"status": "approved", "step_index": step_index}


@router.post("/runbooks/executions/{execution_id}/reject/{step_index}")
async def reject_execution_step(
    execution_id: str,
    step_index: int,
    req: ApproveRejectRequest = ApproveRejectRequest(),
    user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Reject a pending manual or approval step, failing the execution."""
    eid = _parse_uuid(execution_id, "execution_id")
    executor = RunbookExecutor(db)
    rejected = await executor.reject_step(
        execution_id=eid,
        step_index=step_index,
        rejected_by=req.user or user.name,
        reason=req.reason,
    )
    if not rejected:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Step is not pending approval or execution not found",
        )
    return {"status": "rejected", "step_index": step_index, "reason": req.reason}


@router.post("/runbooks/executions/{execution_id}/cancel")
async def cancel_execution(
    execution_id: str,
    req: CancelRequest = CancelRequest(),
    user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Cancel a running runbook execution."""
    eid = _parse_uuid(execution_id, "execution_id")
    executor = RunbookExecutor(db)
    execution = await executor.cancel_execution(eid, reason=req.reason)
    if execution is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Execution {execution_id} not found",
        )
    return {
        "execution_id": str(execution.id),
        "status": execution.status.value,
        "reason": req.reason,
    }


# ======================================================================
# Reporting Endpoints
# ======================================================================

@router.post("/reports/generate", status_code=201)
async def generate_report(
    req: GenerateReportRequest,
    user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate an incident report of the specified type and date range."""
    generator = ReportGenerator(db)

    if req.report_type == "daily":
        if not req.date:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="'date' is required for daily reports",
            )
        dt = _parse_datetime(req.date, "date")
        report = await generator.generate_daily_report(user.org_id, dt)

    elif req.report_type == "weekly":
        if not req.week_start:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="'week_start' is required for weekly reports",
            )
        dt = _parse_datetime(req.week_start, "week_start")
        report = await generator.generate_weekly_report(user.org_id, dt)

    elif req.report_type == "monthly":
        if req.month is None or req.year is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="'month' and 'year' are required for monthly reports",
            )
        report = await generator.generate_monthly_report(
            user.org_id, req.month, req.year
        )

    elif req.report_type == "custom":
        if not req.start_date or not req.end_date:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="'start_date' and 'end_date' are required for custom reports",
            )
        start = _parse_datetime(req.start_date, "start_date")
        end = _parse_datetime(req.end_date, "end_date")
        report = await generator.generate_custom_report(
            user.org_id, start, end, req.filters
        )

    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid report type: {req.report_type}. Use: daily, weekly, monthly, custom",
        )

    return _report_to_response(report)


@router.get("/reports")
async def list_reports(
    report_type: Optional[str] = Query(None, description="Filter by report type"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """List generated reports for the user's organization."""
    repo = EnterpriseRepository(db)
    reports = await repo.list_reports(
        user.org_id,
        report_type=report_type,
        limit=limit,
        offset=offset,
    )
    return [_report_to_response(r) for r in reports]


@router.get("/reports/{report_id}")
async def get_report(
    report_id: str,
    user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a specific report by ID."""
    rid = _parse_uuid(report_id, "report_id")
    repo = EnterpriseRepository(db)
    report = await repo.get_report(rid)
    if report is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Report {report_id} not found",
        )
    return _report_to_response(report)


@router.get("/reports/{report_id}/csv")
async def download_report_csv(
    report_id: str,
    user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Download a report as CSV."""
    from fastapi.responses import Response

    rid = _parse_uuid(report_id, "report_id")
    repo = EnterpriseRepository(db)
    report = await repo.get_report(rid)
    if report is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Report {report_id} not found",
        )

    if not report.data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Report has no data to export",
        )

    csv_content = ReportGenerator.export_csv(report.data)
    filename = f"opslens-report-{report.report_type.value}-{report_id[:8]}.csv"

    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
