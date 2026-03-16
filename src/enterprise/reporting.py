"""Incident reporting and analytics engine."""

from __future__ import annotations

import csv
import io
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import (
    AgentResult,
    Incident,
    IncidentReport,
    IncidentStatusEnum,
    ReportTypeEnum,
    SLABreach,
    TimelineEvent,
    TimelineEventTypeEnum,
)

logger = structlog.get_logger()


class ReportGenerator:
    """Generates incident analytics reports over configurable time ranges."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Public report generators
    # ------------------------------------------------------------------

    async def generate_daily_report(
        self, org_id: uuid.UUID, date: datetime
    ) -> IncidentReport:
        """Generate a report for a single day.

        Args:
            org_id: Organization UUID.
            date: The date to report on (time part is ignored).

        Returns:
            Persisted ``IncidentReport``.
        """
        start = date.replace(hour=0, minute=0, second=0, microsecond=0)
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        end = start + timedelta(days=1)

        data = await self._build_report_data(org_id, start, end)

        title = f"Daily Incident Report - {start.strftime('%Y-%m-%d')}"
        content = self.format_report_text(data)

        return await self._persist_report(
            org_id=org_id,
            report_type=ReportTypeEnum.DAILY,
            title=title,
            content=content,
            data=data,
            period_start=start,
            period_end=end,
        )

    async def generate_weekly_report(
        self, org_id: uuid.UUID, week_start: datetime
    ) -> IncidentReport:
        """Generate a report for a full week (7 days starting from ``week_start``).

        Args:
            org_id: Organization UUID.
            week_start: First day of the week.

        Returns:
            Persisted ``IncidentReport``.
        """
        start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        end = start + timedelta(weeks=1)

        data = await self._build_report_data(org_id, start, end)

        title = (
            f"Weekly Incident Report - "
            f"{start.strftime('%Y-%m-%d')} to {(end - timedelta(days=1)).strftime('%Y-%m-%d')}"
        )
        content = self.format_report_text(data)

        return await self._persist_report(
            org_id=org_id,
            report_type=ReportTypeEnum.WEEKLY,
            title=title,
            content=content,
            data=data,
            period_start=start,
            period_end=end,
        )

    async def generate_monthly_report(
        self, org_id: uuid.UUID, month: int, year: int
    ) -> IncidentReport:
        """Generate a report for a calendar month.

        Args:
            org_id: Organization UUID.
            month: Month number (1-12).
            year: Four-digit year.

        Returns:
            Persisted ``IncidentReport``.
        """
        start = datetime(year, month, 1, tzinfo=timezone.utc)
        if month == 12:
            end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
        else:
            end = datetime(year, month + 1, 1, tzinfo=timezone.utc)

        data = await self._build_report_data(org_id, start, end)

        title = f"Monthly Incident Report - {start.strftime('%B %Y')}"
        content = self.format_report_text(data)

        return await self._persist_report(
            org_id=org_id,
            report_type=ReportTypeEnum.MONTHLY,
            title=title,
            content=content,
            data=data,
            period_start=start,
            period_end=end,
        )

    async def generate_custom_report(
        self,
        org_id: uuid.UUID,
        start_date: datetime,
        end_date: datetime,
        filters: dict[str, Any] | None = None,
    ) -> IncidentReport:
        """Generate a report for a custom date range with optional filters.

        Args:
            org_id: Organization UUID.
            start_date: Range start.
            end_date: Range end.
            filters: Optional filters (``severity``, ``service``, ``status``).

        Returns:
            Persisted ``IncidentReport``.
        """
        if start_date.tzinfo is None:
            start_date = start_date.replace(tzinfo=timezone.utc)
        if end_date.tzinfo is None:
            end_date = end_date.replace(tzinfo=timezone.utc)

        data = await self._build_report_data(org_id, start_date, end_date, filters)
        if filters:
            data["filters"] = filters

        title = (
            f"Custom Incident Report - "
            f"{start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}"
        )
        content = self.format_report_text(data)

        return await self._persist_report(
            org_id=org_id,
            report_type=ReportTypeEnum.CUSTOM,
            title=title,
            content=content,
            data=data,
            period_start=start_date,
            period_end=end_date,
        )

    # ------------------------------------------------------------------
    # Formatting
    # ------------------------------------------------------------------

    @staticmethod
    def format_report_text(report_data: dict[str, Any]) -> str:
        """Render report data as human-readable markdown text.

        Args:
            report_data: The JSON report structure.

        Returns:
            Formatted markdown string.
        """
        lines: list[str] = []
        summary = report_data.get("summary", {})
        period = report_data.get("period", {})

        lines.append("# OpsLens Incident Report")
        if period:
            lines.append(
                f"**Period:** {period.get('start', 'N/A')} to {period.get('end', 'N/A')}"
            )
        lines.append("")

        # Summary
        lines.append("## Summary")
        lines.append(f"- **Total incidents:** {summary.get('total', 0)}")
        lines.append(f"- **New incidents:** {summary.get('new', 0)}")
        lines.append(f"- **Resolved:** {summary.get('resolved', 0)}")
        lines.append(f"- **Escalated:** {summary.get('escalated', 0)}")
        avg_res = summary.get("avg_resolution_minutes")
        if avg_res is not None:
            lines.append(f"- **Avg resolution time:** {avg_res:.1f} minutes")
        lines.append("")

        # By severity
        by_severity = report_data.get("by_severity", {})
        if by_severity:
            lines.append("## By Severity")
            for sev, sev_data in sorted(by_severity.items()):
                count = sev_data.get("count", 0)
                mttr = sev_data.get("avg_mttr_minutes")
                mttr_str = f", avg MTTR {mttr:.1f}m" if mttr is not None else ""
                lines.append(f"- **{sev}:** {count} incidents{mttr_str}")
            lines.append("")

        # By service
        by_service = report_data.get("by_service", {})
        if by_service:
            lines.append("## By Service")
            for svc, svc_data in sorted(
                by_service.items(), key=lambda x: x[1].get("count", 0), reverse=True
            ):
                count = svc_data.get("count", 0)
                mttr = svc_data.get("avg_mttr_minutes")
                mttr_str = f", avg MTTR {mttr:.1f}m" if mttr is not None else ""
                lines.append(f"- **{svc}:** {count} incidents{mttr_str}")
            lines.append("")

        # SLA compliance
        sla = report_data.get("sla_compliance", {})
        if sla:
            lines.append("## SLA Compliance")
            lines.append(f"- Total tracked: {sla.get('total', 0)}")
            lines.append(f"- Within SLA: {sla.get('within_sla', 0)}")
            lines.append(f"- Breached: {sla.get('breached', 0)}")
            lines.append(f"- Compliance rate: {sla.get('rate', 0):.1f}%")
            lines.append("")

        # Agent performance
        agent = report_data.get("agent_performance", {})
        if agent:
            lines.append("## AI Agent Performance")
            lines.append(f"- Avg confidence: {agent.get('avg_confidence', 0):.2f}")
            lines.append(
                f"- Avg processing time: {agent.get('avg_agent_duration_ms', 0):.0f}ms"
            )
            lines.append(f"- Total agent actions: {agent.get('total_actions', 0)}")
            lines.append("")

        # Top recurring
        recurring = report_data.get("top_recurring", [])
        if recurring:
            lines.append("## Top Recurring Incidents")
            for item in recurring[:10]:
                pattern = item.get("title_pattern", "Unknown")
                count = item.get("count", 0)
                services = ", ".join(item.get("services", []))
                lines.append(f"- **{pattern}** ({count}x) - Services: {services}")
            lines.append("")

        # Notable incidents
        notable = report_data.get("notable_incidents", [])
        if notable:
            lines.append("## Notable Incidents")
            for inc in notable[:10]:
                title = inc.get("title", "Untitled")
                sev = inc.get("severity", "")
                res_time = inc.get("resolution_time_minutes")
                res_str = f" - Resolved in {res_time:.0f}m" if res_time else " - Unresolved"
                lines.append(f"- [{sev}] {title}{res_str}")
            lines.append("")

        return "\n".join(lines)

    @staticmethod
    def export_csv(report_data: dict[str, Any]) -> str:
        """Export report data as a CSV string.

        Args:
            report_data: The JSON report structure.

        Returns:
            CSV-formatted string.
        """
        output = io.StringIO()
        writer = csv.writer(output)

        # Summary section
        writer.writerow(["Section", "Metric", "Value"])
        summary = report_data.get("summary", {})
        for key, val in summary.items():
            writer.writerow(["Summary", key, val])

        # By severity
        by_severity = report_data.get("by_severity", {})
        for sev, sev_data in sorted(by_severity.items()):
            for key, val in sev_data.items():
                writer.writerow([f"Severity - {sev}", key, val])

        # By service
        by_service = report_data.get("by_service", {})
        for svc, svc_data in sorted(by_service.items()):
            for key, val in svc_data.items():
                writer.writerow([f"Service - {svc}", key, val])

        # SLA compliance
        sla = report_data.get("sla_compliance", {})
        for key, val in sla.items():
            writer.writerow(["SLA Compliance", key, val])

        # Agent performance
        agent = report_data.get("agent_performance", {})
        for key, val in agent.items():
            writer.writerow(["Agent Performance", key, val])

        # Trends (incident count by day)
        trends = report_data.get("trends", {})
        for day_entry in trends.get("incident_count_by_day", []):
            writer.writerow([
                "Trend - Daily Count",
                day_entry.get("date", ""),
                day_entry.get("count", 0),
            ])
        for day_entry in trends.get("mttr_by_day", []):
            writer.writerow([
                "Trend - MTTR by Day",
                day_entry.get("date", ""),
                day_entry.get("avg_mttr_minutes", 0),
            ])

        # Notable incidents
        notable = report_data.get("notable_incidents", [])
        if notable:
            writer.writerow([])
            writer.writerow(["Incident ID", "Title", "Severity", "Resolution Minutes"])
            for inc in notable:
                writer.writerow([
                    inc.get("incident_id", ""),
                    inc.get("title", ""),
                    inc.get("severity", ""),
                    inc.get("resolution_time_minutes", ""),
                ])

        return output.getvalue()

    # ------------------------------------------------------------------
    # Internal report builder
    # ------------------------------------------------------------------

    async def _build_report_data(
        self,
        org_id: uuid.UUID,
        start: datetime,
        end: datetime,
        filters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build the full report data structure for a given period.

        Args:
            org_id: Organization UUID.
            start: Period start (inclusive).
            end: Period end (exclusive).
            filters: Optional dict with ``severity``, ``service``, ``status`` keys.

        Returns:
            Comprehensive report data dict.
        """
        # Fetch incidents
        stmt = select(Incident).where(
            Incident.org_id == org_id,
            Incident.created_at >= start,
            Incident.created_at < end,
        )
        if filters:
            if "severity" in filters:
                stmt = stmt.where(Incident.severity == filters["severity"])
            if "service" in filters:
                stmt = stmt.where(Incident.service == filters["service"])
            if "status" in filters:
                try:
                    status_enum = IncidentStatusEnum(filters["status"])
                    stmt = stmt.where(Incident.status == status_enum)
                except ValueError:
                    pass

        result = await self._session.execute(stmt)
        incidents = list(result.scalars().all())

        # Summary
        total = len(incidents)
        resolved = [
            i for i in incidents
            if i.status in (IncidentStatusEnum.RESOLVED, IncidentStatusEnum.POSTMORTEM)
        ]
        escalated = await self._count_escalated(incidents)

        resolution_times: list[float] = []
        for inc in resolved:
            if inc.resolved_at is not None:
                created = inc.created_at
                resolved_at = inc.resolved_at
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                if resolved_at.tzinfo is None:
                    resolved_at = resolved_at.replace(tzinfo=timezone.utc)
                delta = (resolved_at - created).total_seconds() / 60.0
                if delta >= 0:
                    resolution_times.append(delta)

        avg_resolution = (
            sum(resolution_times) / len(resolution_times)
            if resolution_times
            else None
        )

        summary = {
            "total": total,
            "new": total,
            "resolved": len(resolved),
            "escalated": escalated,
            "avg_resolution_minutes": round(avg_resolution, 2) if avg_resolution is not None else None,
        }

        # By severity
        by_severity = self._group_by_severity(incidents, resolution_times, resolved)

        # By service
        by_service = self._group_by_service(incidents, resolved)

        # Trends
        trends = self._build_trends(incidents, resolved, start, end)

        # Top recurring
        top_recurring = self._find_recurring(incidents)

        # SLA compliance
        sla_compliance = await self._get_sla_compliance(org_id, incidents)

        # Agent performance
        agent_performance = await self._get_agent_performance(incidents)

        # Notable incidents (P0/P1 or longest resolution)
        notable = self._find_notable(incidents, resolved)

        return {
            "period": {
                "start": start.isoformat(),
                "end": end.isoformat(),
            },
            "summary": summary,
            "by_severity": by_severity,
            "by_service": by_service,
            "trends": trends,
            "top_recurring": top_recurring,
            "sla_compliance": sla_compliance,
            "agent_performance": agent_performance,
            "notable_incidents": notable,
        }

    # ------------------------------------------------------------------
    # Helper methods for report data
    # ------------------------------------------------------------------

    async def _count_escalated(self, incidents: list[Incident]) -> int:
        """Count incidents that had escalation events."""
        if not incidents:
            return 0
        incident_ids = [i.id for i in incidents]
        stmt = (
            select(func.count(func.distinct(TimelineEvent.incident_id)))
            .where(
                TimelineEvent.incident_id.in_(incident_ids),
                TimelineEvent.event_type == TimelineEventTypeEnum.ESCALATION,
            )
        )
        result = await self._session.execute(stmt)
        return result.scalar() or 0

    @staticmethod
    def _group_by_severity(
        incidents: list[Incident],
        resolution_times: list[float],
        resolved: list[Incident],
    ) -> dict[str, Any]:
        """Group incidents by severity with MTTR."""
        groups: dict[str, list[Incident]] = defaultdict(list)
        for inc in incidents:
            groups[inc.severity].append(inc)

        result: dict[str, Any] = {}
        for sev, incs in sorted(groups.items()):
            sev_resolved = [
                i for i in incs
                if i.status in (IncidentStatusEnum.RESOLVED, IncidentStatusEnum.POSTMORTEM)
                and i.resolved_at is not None
            ]
            sev_times: list[float] = []
            for inc in sev_resolved:
                created = inc.created_at
                resolved_at = inc.resolved_at
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                if resolved_at is not None:
                    if resolved_at.tzinfo is None:
                        resolved_at = resolved_at.replace(tzinfo=timezone.utc)
                    delta = (resolved_at - created).total_seconds() / 60.0
                    if delta >= 0:
                        sev_times.append(delta)

            avg_mttr = (
                round(sum(sev_times) / len(sev_times), 2) if sev_times else None
            )
            result[sev] = {
                "count": len(incs),
                "resolved": len(sev_resolved),
                "avg_mttr_minutes": avg_mttr,
            }

        return result

    @staticmethod
    def _group_by_service(
        incidents: list[Incident], resolved: list[Incident]
    ) -> dict[str, Any]:
        """Group incidents by service with MTTR."""
        groups: dict[str, list[Incident]] = defaultdict(list)
        for inc in incidents:
            svc = inc.service or "unknown"
            groups[svc].append(inc)

        result: dict[str, Any] = {}
        for svc, incs in sorted(groups.items()):
            svc_resolved = [
                i for i in incs
                if i.status in (IncidentStatusEnum.RESOLVED, IncidentStatusEnum.POSTMORTEM)
                and i.resolved_at is not None
            ]
            svc_times: list[float] = []
            for inc in svc_resolved:
                created = inc.created_at
                resolved_at = inc.resolved_at
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                if resolved_at is not None:
                    if resolved_at.tzinfo is None:
                        resolved_at = resolved_at.replace(tzinfo=timezone.utc)
                    delta = (resolved_at - created).total_seconds() / 60.0
                    if delta >= 0:
                        svc_times.append(delta)

            avg_mttr = (
                round(sum(svc_times) / len(svc_times), 2) if svc_times else None
            )
            result[svc] = {
                "count": len(incs),
                "resolved": len(svc_resolved),
                "avg_mttr_minutes": avg_mttr,
            }

        return result

    @staticmethod
    def _build_trends(
        incidents: list[Incident],
        resolved: list[Incident],
        start: datetime,
        end: datetime,
    ) -> dict[str, Any]:
        """Build daily trend data for incident counts and MTTR."""
        # Group by day
        daily_counts: Counter[str] = Counter()
        daily_resolution: dict[str, list[float]] = defaultdict(list)

        for inc in incidents:
            day = inc.created_at.strftime("%Y-%m-%d")
            daily_counts[day] += 1

        for inc in resolved:
            if inc.resolved_at is not None:
                created = inc.created_at
                resolved_at = inc.resolved_at
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                if resolved_at.tzinfo is None:
                    resolved_at = resolved_at.replace(tzinfo=timezone.utc)
                day = inc.created_at.strftime("%Y-%m-%d")
                delta = (resolved_at - created).total_seconds() / 60.0
                if delta >= 0:
                    daily_resolution[day].append(delta)

        # Fill in all days in range
        count_by_day: list[dict[str, Any]] = []
        mttr_by_day: list[dict[str, Any]] = []

        current = start
        while current < end:
            day_str = current.strftime("%Y-%m-%d")
            count_by_day.append({
                "date": day_str,
                "count": daily_counts.get(day_str, 0),
            })
            day_times = daily_resolution.get(day_str, [])
            avg_mttr = (
                round(sum(day_times) / len(day_times), 2) if day_times else 0
            )
            mttr_by_day.append({
                "date": day_str,
                "avg_mttr_minutes": avg_mttr,
                "resolved_count": len(day_times),
            })
            current += timedelta(days=1)

        return {
            "incident_count_by_day": count_by_day,
            "mttr_by_day": mttr_by_day,
        }

    @staticmethod
    def _find_recurring(incidents: list[Incident]) -> list[dict[str, Any]]:
        """Find recurring incident patterns by normalized title."""
        import re

        pattern_map: dict[str, dict[str, Any]] = {}
        for inc in incidents:
            # Normalize: remove numbers, UUIDs, timestamps
            normalized = re.sub(r"\b[0-9a-f-]{36}\b", "<UUID>", inc.title)
            normalized = re.sub(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}", "<TIMESTAMP>", normalized)
            normalized = re.sub(r"\b\d+\b", "<N>", normalized)
            normalized = normalized.strip()

            if normalized not in pattern_map:
                pattern_map[normalized] = {
                    "title_pattern": normalized,
                    "count": 0,
                    "services": set(),
                }
            pattern_map[normalized]["count"] += 1
            if inc.service:
                pattern_map[normalized]["services"].add(inc.service)

        recurring = [
            {
                "title_pattern": v["title_pattern"],
                "count": v["count"],
                "services": sorted(v["services"]),
            }
            for v in pattern_map.values()
            if v["count"] >= 2
        ]
        recurring.sort(key=lambda x: x["count"], reverse=True)
        return recurring[:20]

    async def _get_sla_compliance(
        self, org_id: uuid.UUID, incidents: list[Incident]
    ) -> dict[str, Any]:
        """Calculate SLA compliance for the given incidents."""
        if not incidents:
            return {"total": 0, "within_sla": 0, "breached": 0, "rate": 100.0}

        incident_ids = [i.id for i in incidents]
        stmt = select(func.count(func.distinct(SLABreach.incident_id))).where(
            SLABreach.incident_id.in_(incident_ids)
        )
        result = await self._session.execute(stmt)
        breached_count = result.scalar() or 0

        total = len(incidents)
        within = total - breached_count

        return {
            "total": total,
            "within_sla": within,
            "breached": breached_count,
            "rate": round((within / total * 100) if total > 0 else 100.0, 2),
        }

    async def _get_agent_performance(
        self, incidents: list[Incident]
    ) -> dict[str, Any]:
        """Calculate AI agent performance metrics."""
        if not incidents:
            return {
                "avg_confidence": 0.0,
                "avg_agent_duration_ms": 0,
                "total_actions": 0,
            }

        incident_ids = [i.id for i in incidents]
        stmt = select(AgentResult).where(
            AgentResult.incident_id.in_(incident_ids)
        )
        result = await self._session.execute(stmt)
        agent_results = list(result.scalars().all())

        if not agent_results:
            return {
                "avg_confidence": 0.0,
                "avg_agent_duration_ms": 0,
                "total_actions": 0,
            }

        confidences = [r.confidence for r in agent_results if r.confidence is not None]
        durations = [r.duration_ms for r in agent_results if r.duration_ms is not None]

        avg_confidence = (
            sum(confidences) / len(confidences) if confidences else 0.0
        )
        avg_duration = (
            sum(durations) / len(durations) if durations else 0
        )

        return {
            "avg_confidence": round(avg_confidence, 3),
            "avg_agent_duration_ms": round(avg_duration, 0),
            "total_actions": len(agent_results),
        }

    @staticmethod
    def _find_notable(
        incidents: list[Incident], resolved: list[Incident]
    ) -> list[dict[str, Any]]:
        """Identify notable incidents (high severity or long resolution)."""
        notable: list[dict[str, Any]] = []

        for inc in incidents:
            is_notable = False
            resolution_minutes: Optional[float] = None

            # High severity is always notable
            if inc.severity in ("P0-Critical", "P1-High"):
                is_notable = True

            # Calculate resolution time if resolved
            if inc.resolved_at is not None:
                created = inc.created_at
                resolved_at = inc.resolved_at
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                if resolved_at.tzinfo is None:
                    resolved_at = resolved_at.replace(tzinfo=timezone.utc)
                resolution_minutes = (resolved_at - created).total_seconds() / 60.0

                # Long resolution is notable (over 4 hours)
                if resolution_minutes > 240:
                    is_notable = True

            if is_notable:
                notable.append({
                    "incident_id": inc.incident_id,
                    "title": inc.title,
                    "severity": inc.severity,
                    "status": inc.status.value,
                    "service": inc.service,
                    "resolution_time_minutes": (
                        round(resolution_minutes, 1) if resolution_minutes else None
                    ),
                })

        # Sort by severity then resolution time
        severity_order = {"P0-Critical": 0, "P1-High": 1, "P2-Medium": 2, "P3-Low": 3}
        notable.sort(
            key=lambda x: (
                severity_order.get(x.get("severity", ""), 99),
                -(x.get("resolution_time_minutes") or 0),
            )
        )
        return notable[:20]

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    async def _persist_report(
        self,
        org_id: uuid.UUID,
        report_type: ReportTypeEnum,
        title: str,
        content: str,
        data: dict[str, Any],
        period_start: datetime,
        period_end: datetime,
    ) -> IncidentReport:
        """Save a report to the database."""
        report = IncidentReport(
            report_type=report_type,
            title=title,
            content=content,
            data=data,
            org_id=org_id,
            generated_by="system",
            period_start=period_start,
            period_end=period_end,
        )
        self._session.add(report)
        await self._session.flush()
        await self._session.refresh(report)
        logger.info(
            "reporting.report_generated",
            report_id=str(report.id),
            type=report_type.value,
            title=title,
        )
        return report
