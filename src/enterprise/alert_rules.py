"""Custom alert rule engine with condition evaluation and action dispatch."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import (
    ActionTypeEnum,
    AlertRule,
    ConditionTypeEnum,
)
from src.webhooks.schemas import UnifiedAlert

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class RuleAction:
    """An action to take when a rule matches an alert."""

    rule_id: uuid.UUID
    rule_name: str
    action_type: str
    action_config: dict[str, Any]
    matched_conditions: list[str]


# ---------------------------------------------------------------------------
# In-memory state for time-window tracking
# ---------------------------------------------------------------------------

# Keyed by (org_id, rule_id, group_key) -> list of alert timestamps
_time_window_tracker: dict[tuple[str, str, str], list[datetime]] = {}


# ---------------------------------------------------------------------------
# AlertRuleEngine
# ---------------------------------------------------------------------------

class AlertRuleEngine:
    """Evaluates incoming alerts against user-defined rules and produces actions."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Rule CRUD
    # ------------------------------------------------------------------

    async def create_rule(
        self,
        name: str,
        description: str,
        condition_type: str,
        condition_config: dict[str, Any],
        action_type: str,
        action_config: dict[str, Any],
        org_id: uuid.UUID,
        created_by: uuid.UUID | None = None,
    ) -> AlertRule:
        """Create a new alert rule.

        Args:
            name: Human-readable rule name.
            description: Explanation of what this rule does.
            condition_type: One of ``threshold``, ``pattern``, ``composite``, ``time_window``.
            condition_config: JSON condition configuration.
            action_type: One of ``create_incident``, ``escalate``, ``notify``, ``suppress``.
            action_config: JSON action configuration.
            org_id: Owning organization.
            created_by: User UUID who created the rule.

        Returns:
            The persisted ``AlertRule``.
        """
        try:
            ct = ConditionTypeEnum(condition_type)
        except ValueError:
            ct = ConditionTypeEnum.THRESHOLD

        try:
            at = ActionTypeEnum(action_type)
        except ValueError:
            at = ActionTypeEnum.CREATE_INCIDENT

        rule = AlertRule(
            name=name,
            description=description,
            condition_type=ct,
            condition_config=condition_config,
            action_type=at,
            action_config=action_config,
            org_id=org_id,
            created_by=created_by,
        )
        self._session.add(rule)
        await self._session.flush()
        await self._session.refresh(rule)
        logger.info(
            "alert_rules.created",
            name=name,
            condition_type=condition_type,
            action_type=action_type,
        )
        return rule

    async def list_rules(
        self, org_id: uuid.UUID, is_active: bool | None = None
    ) -> list[AlertRule]:
        """List alert rules for an organization.

        Args:
            org_id: Organization UUID.
            is_active: If set, filter by active status.

        Returns:
            List of ``AlertRule`` rows.
        """
        stmt = select(AlertRule).where(AlertRule.org_id == org_id)
        if is_active is not None:
            stmt = stmt.where(AlertRule.is_active == is_active)
        stmt = stmt.order_by(AlertRule.created_at.desc())
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def update_rule(
        self,
        rule_id: uuid.UUID,
        updates: dict[str, Any],
    ) -> AlertRule | None:
        """Update an existing alert rule.

        Args:
            rule_id: The rule UUID.
            updates: Dict of column names to new values.

        Returns:
            Updated ``AlertRule`` or ``None`` if not found.
        """
        stmt = select(AlertRule).where(AlertRule.id == rule_id)
        result = await self._session.execute(stmt)
        rule = result.scalar_one_or_none()
        if rule is None:
            return None

        for key, value in updates.items():
            if key == "condition_type":
                try:
                    value = ConditionTypeEnum(value)
                except ValueError:
                    continue
            elif key == "action_type":
                try:
                    value = ActionTypeEnum(value)
                except ValueError:
                    continue
            if hasattr(rule, key):
                setattr(rule, key, value)

        rule.updated_at = datetime.now(timezone.utc)
        await self._session.flush()
        await self._session.refresh(rule)
        logger.info("alert_rules.updated", rule_id=str(rule_id))
        return rule

    async def delete_rule(self, rule_id: uuid.UUID) -> bool:
        """Delete an alert rule by UUID.

        Returns:
            ``True`` if deleted, ``False`` if not found.
        """
        stmt = select(AlertRule).where(AlertRule.id == rule_id)
        result = await self._session.execute(stmt)
        rule = result.scalar_one_or_none()
        if rule is None:
            return False
        await self._session.delete(rule)
        await self._session.flush()
        logger.info("alert_rules.deleted", rule_id=str(rule_id))
        return True

    async def toggle_rule(self, rule_id: uuid.UUID) -> AlertRule | None:
        """Toggle a rule's active state.

        Returns:
            Updated ``AlertRule`` or ``None`` if not found.
        """
        stmt = select(AlertRule).where(AlertRule.id == rule_id)
        result = await self._session.execute(stmt)
        rule = result.scalar_one_or_none()
        if rule is None:
            return None
        rule.is_active = not rule.is_active
        rule.updated_at = datetime.now(timezone.utc)
        await self._session.flush()
        await self._session.refresh(rule)
        logger.info(
            "alert_rules.toggled",
            rule_id=str(rule_id),
            is_active=rule.is_active,
        )
        return rule

    # ------------------------------------------------------------------
    # Alert evaluation
    # ------------------------------------------------------------------

    async def evaluate_alert(
        self, alert: UnifiedAlert, org_id: uuid.UUID
    ) -> list[RuleAction]:
        """Evaluate an incoming alert against all active rules.

        Args:
            alert: The canonical ``UnifiedAlert``.
            org_id: Organization UUID to scope rules.

        Returns:
            List of ``RuleAction`` for rules that matched.
        """
        rules = await self.list_rules(org_id, is_active=True)
        actions: list[RuleAction] = []

        for rule in rules:
            matched, descriptions = self._evaluate_condition(
                alert, rule.condition_type.value, rule.condition_config, rule, org_id
            )
            if matched:
                actions.append(
                    RuleAction(
                        rule_id=rule.id,
                        rule_name=rule.name,
                        action_type=rule.action_type.value,
                        action_config=rule.action_config,
                        matched_conditions=descriptions,
                    )
                )
                logger.info(
                    "alert_rules.matched",
                    rule_name=rule.name,
                    alert_title=alert.title,
                    action=rule.action_type.value,
                )

        return actions

    async def evaluate_suppression_rules(
        self, alert: UnifiedAlert, org_id: uuid.UUID
    ) -> bool:
        """Check if any active suppression rules match this alert.

        Returns:
            ``True`` if the alert should be suppressed, ``False`` otherwise.
        """
        rules = await self.list_rules(org_id, is_active=True)
        for rule in rules:
            if rule.action_type != ActionTypeEnum.SUPPRESS:
                continue
            matched, _ = self._evaluate_condition(
                alert, rule.condition_type.value, rule.condition_config, rule, org_id
            )
            if matched:
                # Check suppression duration
                config = rule.action_config or {}
                duration_minutes = config.get("duration_minutes", 60)
                rule_created = rule.created_at
                if rule_created.tzinfo is None:
                    rule_created = rule_created.replace(tzinfo=timezone.utc)
                now = datetime.now(timezone.utc)
                if (now - rule_created).total_seconds() <= duration_minutes * 60:
                    logger.info(
                        "alert_rules.suppressed",
                        rule_name=rule.name,
                        alert_title=alert.title,
                        reason=config.get("reason", "Suppression rule matched"),
                    )
                    return True
        return False

    async def get_rule_match_history(
        self, rule_id: uuid.UUID, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Get recent match history for a rule.

        This reads from the rule's action_config.match_history field,
        which is updated on each match. Returns last ``limit`` entries.

        Returns:
            List of match dicts with timestamp, alert info, and action taken.
        """
        stmt = select(AlertRule).where(AlertRule.id == rule_id)
        result = await self._session.execute(stmt)
        rule = result.scalar_one_or_none()
        if rule is None:
            return []

        config = rule.action_config or {}
        history: list[dict[str, Any]] = config.get("match_history", [])
        return history[-limit:]

    async def record_match(
        self, rule_id: uuid.UUID, alert: UnifiedAlert
    ) -> None:
        """Record a rule match in the rule's action_config.match_history."""
        stmt = select(AlertRule).where(AlertRule.id == rule_id)
        result = await self._session.execute(stmt)
        rule = result.scalar_one_or_none()
        if rule is None:
            return

        config = dict(rule.action_config) if rule.action_config else {}
        history: list[dict[str, Any]] = config.get("match_history", [])
        history.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "alert_id": alert.alert_id,
            "alert_title": alert.title,
            "severity": alert.severity.value if hasattr(alert.severity, "value") else str(alert.severity),
            "service": alert.service,
        })
        # Keep last 200 entries
        config["match_history"] = history[-200:]
        rule.action_config = config
        rule.updated_at = datetime.now(timezone.utc)
        await self._session.flush()

    # ------------------------------------------------------------------
    # Condition evaluation (stateless)
    # ------------------------------------------------------------------

    def _evaluate_condition(
        self,
        alert: UnifiedAlert,
        condition_type: str,
        condition_config: dict[str, Any],
        rule: AlertRule | None = None,
        org_id: uuid.UUID | None = None,
    ) -> tuple[bool, list[str]]:
        """Evaluate a single condition against an alert.

        Returns:
            Tuple of (matched: bool, description_list).
        """
        if condition_type == "threshold":
            return self._eval_threshold(alert, condition_config)
        elif condition_type == "pattern":
            return self._eval_pattern(alert, condition_config)
        elif condition_type == "composite":
            return self._eval_composite(alert, condition_config, rule, org_id)
        elif condition_type == "time_window":
            return self._eval_time_window(alert, condition_config, rule, org_id)
        else:
            return False, []

    def _eval_threshold(
        self, alert: UnifiedAlert, config: dict[str, Any]
    ) -> tuple[bool, list[str]]:
        """Evaluate a threshold condition.

        Config:
            field: attribute name on the alert
            operator: eq, neq, gt, lt, gte, lte, in, not_in, contains
            value: comparison value
        """
        field_name = config.get("field", "")
        operator = config.get("operator", "eq")
        value = config.get("value")

        alert_value = self._get_alert_field(alert, field_name)
        if alert_value is None:
            return False, []

        matched = False
        # Normalize for comparison
        alert_str = str(alert_value).lower() if isinstance(alert_value, str) else alert_value
        value_str = str(value).lower() if isinstance(value, str) else value

        if operator == "eq":
            matched = alert_str == value_str
        elif operator == "neq":
            matched = alert_str != value_str
        elif operator in ("gt", "lt", "gte", "lte"):
            try:
                a_num = float(alert_value) if not isinstance(alert_value, (int, float)) else alert_value
                v_num = float(value) if not isinstance(value, (int, float)) else value
                if operator == "gt":
                    matched = a_num > v_num
                elif operator == "lt":
                    matched = a_num < v_num
                elif operator == "gte":
                    matched = a_num >= v_num
                elif operator == "lte":
                    matched = a_num <= v_num
            except (ValueError, TypeError):
                matched = False
        elif operator == "in":
            if isinstance(value, list):
                matched = alert_str in [str(v).lower() for v in value]
            else:
                matched = alert_str in str(value).lower()
        elif operator == "not_in":
            if isinstance(value, list):
                matched = alert_str not in [str(v).lower() for v in value]
            else:
                matched = alert_str not in str(value).lower()
        elif operator == "contains":
            matched = str(value).lower() in str(alert_value).lower()

        desc = f"threshold({field_name} {operator} {value})" if matched else ""
        return matched, [desc] if matched else []

    def _eval_pattern(
        self, alert: UnifiedAlert, config: dict[str, Any]
    ) -> tuple[bool, list[str]]:
        """Evaluate a regex pattern condition.

        Config:
            field: attribute name on the alert
            regex: regular expression pattern
        """
        field_name = config.get("field", "")
        pattern = config.get("regex", "")

        alert_value = self._get_alert_field(alert, field_name)
        if alert_value is None or not pattern:
            return False, []

        try:
            matched = bool(re.search(pattern, str(alert_value), re.IGNORECASE))
        except re.error:
            logger.warning("alert_rules.invalid_regex", pattern=pattern)
            matched = False

        desc = f"pattern({field_name} ~ /{pattern}/)" if matched else ""
        return matched, [desc] if matched else []

    def _eval_composite(
        self,
        alert: UnifiedAlert,
        config: dict[str, Any],
        rule: AlertRule | None = None,
        org_id: uuid.UUID | None = None,
    ) -> tuple[bool, list[str]]:
        """Evaluate a composite condition with AND/OR logic.

        Config:
            operator: "and" or "or"
            conditions: list of {condition_type, ...config} dicts
        """
        logic_op = config.get("operator", "and").lower()
        conditions = config.get("conditions", [])
        if not conditions:
            return False, []

        all_descriptions: list[str] = []
        results: list[bool] = []

        for sub in conditions:
            sub_type = sub.get("condition_type", sub.get("type", "threshold"))
            sub_config = {k: v for k, v in sub.items() if k not in ("condition_type", "type")}
            matched, descs = self._evaluate_condition(alert, sub_type, sub_config, rule, org_id)
            results.append(matched)
            all_descriptions.extend(descs)

        if logic_op == "and":
            final = all(results)
        elif logic_op == "or":
            final = any(results)
        else:
            final = all(results)

        if final:
            return True, all_descriptions
        return False, []

    def _eval_time_window(
        self,
        alert: UnifiedAlert,
        config: dict[str, Any],
        rule: AlertRule | None = None,
        org_id: uuid.UUID | None = None,
    ) -> tuple[bool, list[str]]:
        """Evaluate a time-window condition (N alerts in M minutes).

        Config:
            count: minimum number of alerts to trigger
            window_minutes: time window in minutes
            group_by: field to group alerts by (e.g. "service")
        """
        threshold_count = config.get("count", 3)
        window_minutes = config.get("window_minutes", 5)
        group_by = config.get("group_by", "service")

        rule_id_str = str(rule.id) if rule else "unknown"
        org_id_str = str(org_id) if org_id else "unknown"
        group_value = str(self._get_alert_field(alert, group_by) or "default")

        key = (org_id_str, rule_id_str, group_value)
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(minutes=window_minutes)

        # Get or init tracker
        timestamps = _time_window_tracker.get(key, [])
        # Prune old entries
        timestamps = [t for t in timestamps if t >= cutoff]
        timestamps.append(now)
        _time_window_tracker[key] = timestamps

        matched = len(timestamps) >= threshold_count
        desc = (
            f"time_window({len(timestamps)} alerts in {window_minutes}m for {group_by}={group_value})"
            if matched
            else ""
        )
        return matched, [desc] if matched else []

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _get_alert_field(alert: UnifiedAlert, field_name: str) -> Any:
        """Extract a field value from a ``UnifiedAlert``.

        Supports dotted paths for nested access (e.g. ``labels.team``).
        """
        if not field_name:
            return None

        parts = field_name.split(".")
        obj: Any = alert

        for part in parts:
            if isinstance(obj, dict):
                obj = obj.get(part)
            elif hasattr(obj, part):
                obj = getattr(obj, part)
            else:
                return None

            # Resolve enum values
            if hasattr(obj, "value") and not isinstance(obj, (dict, list)):
                obj = obj.value

        return obj
