"""Runbook execution engine with sandboxed command support."""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import (
    RunbookExecution,
    RunbookStatusEnum,
    TimelineEvent,
    TimelineEventTypeEnum,
)

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ALLOWED_COMMANDS: list[str] = [
    "kubectl rollout",
    "kubectl scale",
    "kubectl restart",
    "kubectl get",
    "kubectl describe",
    "kubectl logs",
    "kubectl top",
    "docker restart",
    "docker stop",
    "docker start",
    "docker ps",
    "docker logs",
    "docker inspect",
    "systemctl restart",
    "systemctl stop",
    "systemctl start",
    "systemctl status",
    "systemctl reload",
    "curl -s",
    "curl --silent",
    "curl -o /dev/null",
    "wget -q",
    "ping -c",
    "dig",
    "nslookup",
    "traceroute",
    "netstat -tlnp",
    "ss -tlnp",
    "df -h",
    "free -m",
    "top -bn1",
    "uptime",
    "redis-cli ping",
    "redis-cli info",
    "pg_isready",
    "mysql --execute",
]

# Commands that are never allowed
BLOCKED_PATTERNS: list[str] = [
    "rm -rf",
    "rm -r /",
    "mkfs",
    "dd if=",
    ":(){ :|:& };:",
    "> /dev/sd",
    "chmod 777 /",
    "wget -O- | sh",
    "curl | sh",
    "eval(",
    "exec(",
    "sudo su",
    "sudo -i",
    "passwd",
    "userdel",
    "groupdel",
]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class RunbookStep:
    """Definition of a single runbook step."""

    index: int
    name: str
    type: str  # manual, command, api_call, k8s, approval
    config: dict[str, Any] = field(default_factory=dict)
    timeout_seconds: int = 300


@dataclass
class StepResult:
    """Outcome of executing a single step."""

    success: bool
    output: str
    duration_ms: int
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# RunbookExecutor
# ---------------------------------------------------------------------------

class RunbookExecutor:
    """Executes runbook steps with safety controls and audit trail."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        # In-memory tracking of step results per execution
        self._step_results: dict[uuid.UUID, dict[int, StepResult]] = {}
        # In-memory tracking of step definitions per execution
        self._step_definitions: dict[uuid.UUID, list[RunbookStep]] = {}
        # Pending approval requests
        self._pending_approvals: dict[tuple[uuid.UUID, int], dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Execution lifecycle
    # ------------------------------------------------------------------

    async def start_execution(
        self,
        incident_id: uuid.UUID,
        runbook_name: str,
        runbook_notion_id: str | None,
        steps: list[RunbookStep],
        executed_by: uuid.UUID | None = None,
    ) -> RunbookExecution:
        """Start a new runbook execution.

        Args:
            incident_id: The incident this runbook addresses.
            runbook_name: Human-readable runbook name.
            runbook_notion_id: Optional Notion page ID for the source runbook.
            steps: Ordered list of ``RunbookStep`` to execute.
            executed_by: The user who triggered execution.

        Returns:
            The persisted ``RunbookExecution`` in ``RUNNING`` state.
        """
        execution = RunbookExecution(
            incident_id=incident_id,
            runbook_name=runbook_name,
            runbook_notion_id=runbook_notion_id,
            status=RunbookStatusEnum.RUNNING,
            steps_total=len(steps),
            steps_completed=0,
            executed_by=executed_by,
            started_at=datetime.now(timezone.utc),
            output={
                "steps": [
                    {
                        "index": s.index,
                        "name": s.name,
                        "type": s.type,
                        "status": "pending",
                        "result": None,
                    }
                    for s in steps
                ],
            },
        )
        self._session.add(execution)
        await self._session.flush()
        await self._session.refresh(execution)

        # Store step definitions
        self._step_definitions[execution.id] = steps
        self._step_results[execution.id] = {}

        # Timeline event
        event = TimelineEvent(
            incident_id=incident_id,
            event_type=TimelineEventTypeEnum.MANUAL_ACTION,
            message=f"Runbook '{runbook_name}' execution started ({len(steps)} steps).",
            actor="runbook-executor",
            metadata_={
                "execution_id": str(execution.id),
                "runbook_name": runbook_name,
                "steps_total": len(steps),
            },
        )
        self._session.add(event)
        await self._session.flush()

        logger.info(
            "runbook.execution_started",
            execution_id=str(execution.id),
            runbook=runbook_name,
            steps=len(steps),
        )
        return execution

    async def execute_step(
        self, execution_id: uuid.UUID, step_index: int
    ) -> StepResult:
        """Execute a single step of a runbook.

        Args:
            execution_id: The running execution UUID.
            step_index: Zero-based index of the step to execute.

        Returns:
            A ``StepResult`` with success status, output, and timing.
        """
        execution = await self._get_execution(execution_id)
        if execution is None:
            return StepResult(
                success=False, output="", duration_ms=0,
                error="Execution not found",
            )

        if execution.status != RunbookStatusEnum.RUNNING:
            return StepResult(
                success=False, output="", duration_ms=0,
                error=f"Execution is not running (status={execution.status.value})",
            )

        steps = self._step_definitions.get(execution_id, [])
        step = next((s for s in steps if s.index == step_index), None)
        if step is None:
            return StepResult(
                success=False, output="", duration_ms=0,
                error=f"Step {step_index} not found",
            )

        # Update step status to running
        await self._update_step_output(execution, step_index, "running", None)

        start = time.monotonic()
        result: StepResult

        try:
            if step.type == "command":
                result = await self._execute_command(step)
            elif step.type == "api_call":
                result = await self._execute_api_call(step)
            elif step.type == "k8s":
                result = await self._execute_k8s(step)
            elif step.type == "approval":
                result = await self._handle_approval(execution_id, step)
            elif step.type == "manual":
                result = StepResult(
                    success=True,
                    output="Manual step — requires human completion. Mark as done via approve endpoint.",
                    duration_ms=0,
                    error=None,
                )
                # Create pending approval for manual steps too
                self._pending_approvals[(execution_id, step_index)] = {
                    "step_name": step.name,
                    "requested_at": datetime.now(timezone.utc).isoformat(),
                    "type": "manual",
                }
            else:
                result = StepResult(
                    success=False,
                    output="",
                    duration_ms=0,
                    error=f"Unknown step type: {step.type}",
                )
        except Exception as exc:
            elapsed = int((time.monotonic() - start) * 1000)
            result = StepResult(
                success=False,
                output="",
                duration_ms=elapsed,
                error=str(exc),
            )
            logger.error(
                "runbook.step_error",
                execution_id=str(execution_id),
                step=step_index,
                error=str(exc),
            )

        if result.duration_ms == 0 and step.type not in ("manual", "approval"):
            result = StepResult(
                success=result.success,
                output=result.output,
                duration_ms=int((time.monotonic() - start) * 1000),
                error=result.error,
            )

        # Store result
        self._step_results.setdefault(execution_id, {})[step_index] = result

        # Update execution output
        status_str = "completed" if result.success else "failed"
        if step.type in ("manual", "approval") and result.success:
            status_str = "awaiting_approval"
        await self._update_step_output(execution, step_index, status_str, result)

        # Update steps_completed count
        if result.success and step.type not in ("manual", "approval"):
            execution.steps_completed = min(
                execution.steps_completed + 1, execution.steps_total
            )
            await self._session.flush()

        logger.info(
            "runbook.step_executed",
            execution_id=str(execution_id),
            step=step_index,
            step_name=step.name,
            success=result.success,
            duration_ms=result.duration_ms,
        )
        return result

    async def advance_execution(
        self, execution_id: uuid.UUID
    ) -> dict[str, Any]:
        """Advance to the next pending step or mark execution as complete.

        Returns:
            Dict with ``status``, ``next_step``, or ``completed`` info.
        """
        execution = await self._get_execution(execution_id)
        if execution is None:
            return {"error": "Execution not found"}

        if execution.status != RunbookStatusEnum.RUNNING:
            return {
                "status": execution.status.value,
                "message": "Execution is not running",
            }

        steps = self._step_definitions.get(execution_id, [])
        results = self._step_results.get(execution_id, {})

        # Find next step to execute
        for step in sorted(steps, key=lambda s: s.index):
            if step.index not in results:
                # Execute next step
                result = await self.execute_step(execution_id, step.index)
                return {
                    "status": "running",
                    "current_step": step.index,
                    "step_name": step.name,
                    "step_type": step.type,
                    "result": {
                        "success": result.success,
                        "output": result.output,
                        "duration_ms": result.duration_ms,
                        "error": result.error,
                    },
                }
            else:
                prev_result = results[step.index]
                if not prev_result.success and step.type not in ("manual", "approval"):
                    # Previous step failed — stop execution
                    execution.status = RunbookStatusEnum.FAILED
                    execution.completed_at = datetime.now(timezone.utc)
                    await self._session.flush()
                    return {
                        "status": "failed",
                        "failed_step": step.index,
                        "step_name": step.name,
                        "error": prev_result.error,
                    }

        # All steps done
        execution.status = RunbookStatusEnum.COMPLETED
        execution.completed_at = datetime.now(timezone.utc)
        execution.steps_completed = execution.steps_total
        await self._session.flush()

        # Timeline event
        event = TimelineEvent(
            incident_id=execution.incident_id,
            event_type=TimelineEventTypeEnum.MANUAL_ACTION,
            message=f"Runbook '{execution.runbook_name}' execution completed successfully.",
            actor="runbook-executor",
            metadata_={"execution_id": str(execution_id)},
        )
        self._session.add(event)
        await self._session.flush()

        logger.info(
            "runbook.execution_completed",
            execution_id=str(execution_id),
        )
        return {
            "status": "completed",
            "steps_completed": execution.steps_completed,
            "steps_total": execution.steps_total,
        }

    async def cancel_execution(
        self, execution_id: uuid.UUID, reason: str = ""
    ) -> RunbookExecution | None:
        """Cancel a running execution.

        Args:
            execution_id: The execution to cancel.
            reason: Free-text explanation for cancellation.

        Returns:
            Updated ``RunbookExecution`` or ``None`` if not found.
        """
        execution = await self._get_execution(execution_id)
        if execution is None:
            return None

        if execution.status not in (RunbookStatusEnum.RUNNING, RunbookStatusEnum.PENDING):
            return execution

        execution.status = RunbookStatusEnum.CANCELLED
        execution.completed_at = datetime.now(timezone.utc)

        # Update output with cancellation info
        output = dict(execution.output) if execution.output else {}
        output["cancelled_reason"] = reason
        output["cancelled_at"] = datetime.now(timezone.utc).isoformat()
        execution.output = output
        await self._session.flush()
        await self._session.refresh(execution)

        # Clean up
        self._step_definitions.pop(execution_id, None)
        self._step_results.pop(execution_id, None)
        # Remove pending approvals for this execution
        keys_to_remove = [k for k in self._pending_approvals if k[0] == execution_id]
        for k in keys_to_remove:
            del self._pending_approvals[k]

        # Timeline
        event = TimelineEvent(
            incident_id=execution.incident_id,
            event_type=TimelineEventTypeEnum.MANUAL_ACTION,
            message=f"Runbook '{execution.runbook_name}' execution cancelled. Reason: {reason or 'N/A'}",
            actor="runbook-executor",
            metadata_={"execution_id": str(execution_id), "reason": reason},
        )
        self._session.add(event)
        await self._session.flush()

        logger.info(
            "runbook.execution_cancelled",
            execution_id=str(execution_id),
            reason=reason,
        )
        return execution

    async def get_execution_status(
        self, execution_id: uuid.UUID
    ) -> dict[str, Any]:
        """Get full execution status including all step results.

        Returns:
            Dict with execution metadata and per-step results.
        """
        execution = await self._get_execution(execution_id)
        if execution is None:
            return {"error": "Execution not found"}

        output = execution.output or {}
        steps_info = output.get("steps", [])

        return {
            "execution_id": str(execution.id),
            "incident_id": str(execution.incident_id),
            "runbook_name": execution.runbook_name,
            "runbook_notion_id": execution.runbook_notion_id,
            "status": execution.status.value,
            "steps_total": execution.steps_total,
            "steps_completed": execution.steps_completed,
            "started_at": execution.started_at.isoformat() if execution.started_at else None,
            "completed_at": execution.completed_at.isoformat() if execution.completed_at else None,
            "executed_by": str(execution.executed_by) if execution.executed_by else None,
            "steps": steps_info,
        }

    # ------------------------------------------------------------------
    # Approval workflow
    # ------------------------------------------------------------------

    async def approve_step(
        self,
        execution_id: uuid.UUID,
        step_index: int,
        approved_by: str,
    ) -> bool:
        """Approve a pending manual or approval step.

        Args:
            execution_id: The execution UUID.
            step_index: The step requiring approval.
            approved_by: Name or email of the approver.

        Returns:
            ``True`` if approved successfully, ``False`` otherwise.
        """
        key = (execution_id, step_index)
        if key not in self._pending_approvals:
            # Check if the step is actually waiting
            execution = await self._get_execution(execution_id)
            if execution is None:
                return False
            output = execution.output or {}
            steps_out = output.get("steps", [])
            step_out = next((s for s in steps_out if s.get("index") == step_index), None)
            if step_out is None or step_out.get("status") != "awaiting_approval":
                return False

        # Record approval
        result = StepResult(
            success=True,
            output=f"Approved by {approved_by}",
            duration_ms=0,
            error=None,
        )
        self._step_results.setdefault(execution_id, {})[step_index] = result
        self._pending_approvals.pop(key, None)

        # Update execution
        execution = await self._get_execution(execution_id)
        if execution is not None:
            await self._update_step_output(execution, step_index, "completed", result)
            execution.steps_completed = min(
                execution.steps_completed + 1, execution.steps_total
            )
            await self._session.flush()

        logger.info(
            "runbook.step_approved",
            execution_id=str(execution_id),
            step=step_index,
            approved_by=approved_by,
        )
        return True

    async def reject_step(
        self,
        execution_id: uuid.UUID,
        step_index: int,
        rejected_by: str,
        reason: str = "",
    ) -> bool:
        """Reject a pending manual or approval step, failing the execution.

        Args:
            execution_id: The execution UUID.
            step_index: The step being rejected.
            rejected_by: Name or email of the rejector.
            reason: Free-text explanation.

        Returns:
            ``True`` if rejected successfully, ``False`` otherwise.
        """
        key = (execution_id, step_index)
        self._pending_approvals.pop(key, None)

        result = StepResult(
            success=False,
            output=f"Rejected by {rejected_by}: {reason}",
            duration_ms=0,
            error=f"Step rejected: {reason}",
        )
        self._step_results.setdefault(execution_id, {})[step_index] = result

        execution = await self._get_execution(execution_id)
        if execution is not None:
            await self._update_step_output(execution, step_index, "rejected", result)
            execution.status = RunbookStatusEnum.FAILED
            execution.completed_at = datetime.now(timezone.utc)
            await self._session.flush()

        logger.info(
            "runbook.step_rejected",
            execution_id=str(execution_id),
            step=step_index,
            rejected_by=rejected_by,
            reason=reason,
        )
        return True

    # ------------------------------------------------------------------
    # Command validation
    # ------------------------------------------------------------------

    @staticmethod
    def validate_command(cmd: str) -> bool:
        """Check if a command is in the allowlist and not in the blocklist.

        Args:
            cmd: The shell command string.

        Returns:
            ``True`` if the command is allowed.
        """
        cmd_stripped = cmd.strip()

        # Block dangerous patterns
        for pattern in BLOCKED_PATTERNS:
            if pattern in cmd_stripped:
                return False

        # Check allowlist
        for allowed in ALLOWED_COMMANDS:
            if cmd_stripped.startswith(allowed):
                return True

        return False

    # ------------------------------------------------------------------
    # Step type handlers
    # ------------------------------------------------------------------

    async def _execute_command(self, step: RunbookStep) -> StepResult:
        """Execute a sandboxed shell command."""
        cmd = step.config.get("command", "")
        if not cmd:
            return StepResult(
                success=False, output="", duration_ms=0,
                error="No command specified",
            )

        if not self.validate_command(cmd):
            return StepResult(
                success=False,
                output="",
                duration_ms=0,
                error=f"Command not allowed: {cmd}. Must match allowlist.",
            )

        timeout = step.timeout_seconds or 300
        start = time.monotonic()

        try:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
            elapsed = int((time.monotonic() - start) * 1000)

            output = stdout.decode("utf-8", errors="replace")
            err_output = stderr.decode("utf-8", errors="replace")

            if proc.returncode == 0:
                return StepResult(
                    success=True,
                    output=output,
                    duration_ms=elapsed,
                )
            else:
                return StepResult(
                    success=False,
                    output=output,
                    duration_ms=elapsed,
                    error=f"Exit code {proc.returncode}: {err_output}",
                )
        except asyncio.TimeoutError:
            elapsed = int((time.monotonic() - start) * 1000)
            return StepResult(
                success=False,
                output="",
                duration_ms=elapsed,
                error=f"Command timed out after {timeout}s",
            )

    async def _execute_api_call(self, step: RunbookStep) -> StepResult:
        """Execute an HTTP API call."""
        import aiohttp

        method = step.config.get("method", "GET").upper()
        url = step.config.get("url", "")
        headers = step.config.get("headers", {})
        body = step.config.get("body")
        timeout_sec = step.timeout_seconds or 60

        if not url:
            return StepResult(
                success=False, output="", duration_ms=0,
                error="No URL specified",
            )

        start = time.monotonic()
        try:
            timeout = aiohttp.ClientTimeout(total=timeout_sec)
            async with aiohttp.ClientSession(timeout=timeout) as client:
                kwargs: dict[str, Any] = {"headers": headers}
                if body is not None:
                    if isinstance(body, dict):
                        kwargs["json"] = body
                    else:
                        kwargs["data"] = str(body)

                async with client.request(method, url, **kwargs) as resp:
                    resp_text = await resp.text()
                    elapsed = int((time.monotonic() - start) * 1000)

                    if 200 <= resp.status < 300:
                        return StepResult(
                            success=True,
                            output=f"HTTP {resp.status}: {resp_text[:2000]}",
                            duration_ms=elapsed,
                        )
                    else:
                        return StepResult(
                            success=False,
                            output=resp_text[:2000],
                            duration_ms=elapsed,
                            error=f"HTTP {resp.status}",
                        )
        except Exception as exc:
            elapsed = int((time.monotonic() - start) * 1000)
            return StepResult(
                success=False,
                output="",
                duration_ms=elapsed,
                error=str(exc),
            )

    async def _execute_k8s(self, step: RunbookStep) -> StepResult:
        """Execute a kubectl command (rollback, scale, restart)."""
        action = step.config.get("action", "")
        namespace = step.config.get("namespace", "default")
        resource = step.config.get("resource", "")
        extra_args = step.config.get("args", "")

        if not action or not resource:
            return StepResult(
                success=False, output="", duration_ms=0,
                error="k8s step requires 'action' and 'resource' in config",
            )

        # Map actions to kubectl commands
        action_map = {
            "rollback": f"kubectl rollout undo {resource} -n {namespace}",
            "restart": f"kubectl rollout restart {resource} -n {namespace}",
            "scale": f"kubectl scale {resource} -n {namespace} {extra_args}",
            "status": f"kubectl rollout status {resource} -n {namespace}",
            "get": f"kubectl get {resource} -n {namespace} {extra_args}",
            "describe": f"kubectl describe {resource} -n {namespace}",
            "logs": f"kubectl logs {resource} -n {namespace} --tail=100",
        }

        cmd = action_map.get(action)
        if cmd is None:
            return StepResult(
                success=False, output="", duration_ms=0,
                error=f"Unknown k8s action: {action}. Supported: {list(action_map.keys())}",
            )

        if extra_args and action not in ("scale", "get"):
            cmd = f"{cmd} {extra_args}"

        if not self.validate_command(cmd):
            return StepResult(
                success=False, output="", duration_ms=0,
                error=f"k8s command not allowed: {cmd}",
            )

        # Reuse command executor
        temp_step = RunbookStep(
            index=step.index,
            name=step.name,
            type="command",
            config={"command": cmd},
            timeout_seconds=step.timeout_seconds,
        )
        return await self._execute_command(temp_step)

    async def _handle_approval(
        self, execution_id: uuid.UUID, step: RunbookStep
    ) -> StepResult:
        """Create an approval request and wait for human confirmation."""
        self._pending_approvals[(execution_id, step.index)] = {
            "step_name": step.name,
            "requested_at": datetime.now(timezone.utc).isoformat(),
            "type": "approval",
            "message": step.config.get("message", f"Approval required for step: {step.name}"),
            "required_approvers": step.config.get("required_approvers", []),
        }

        return StepResult(
            success=True,
            output=f"Approval requested for step '{step.name}'. Waiting for human confirmation.",
            duration_ms=0,
            error=None,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _get_execution(
        self, execution_id: uuid.UUID
    ) -> RunbookExecution | None:
        """Fetch an execution by UUID."""
        stmt = select(RunbookExecution).where(RunbookExecution.id == execution_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def _update_step_output(
        self,
        execution: RunbookExecution,
        step_index: int,
        status: str,
        result: StepResult | None,
    ) -> None:
        """Update the step output in the execution's JSON output field."""
        output = dict(execution.output) if execution.output else {"steps": []}
        steps_list: list[dict[str, Any]] = output.get("steps", [])

        for step_out in steps_list:
            if step_out.get("index") == step_index:
                step_out["status"] = status
                if result is not None:
                    step_out["result"] = {
                        "success": result.success,
                        "output": result.output[:5000],  # Truncate large outputs
                        "duration_ms": result.duration_ms,
                        "error": result.error,
                    }
                break

        output["steps"] = steps_list
        execution.output = output
        await self._session.flush()
