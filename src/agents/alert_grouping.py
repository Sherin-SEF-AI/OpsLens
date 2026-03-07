"""Smart Alert Grouping: correlate related alerts into existing incidents."""

import time
from typing import Any

import structlog

from src.incidents.models import Incident
from src.webhooks.schemas import UnifiedAlert

logger = structlog.get_logger()


class AlertGrouper:
    """Groups related alerts into existing incidents instead of creating duplicates.

    Grouping rules (checked in order):
    1. Exact fingerprint match (existing dedup) - handled by IncidentManager
    2. Same service + similar title within time window
    3. Same service + same severity within short time window
    """

    def __init__(self, group_window_seconds: int = 600):
        self.group_window = group_window_seconds

    def find_group(
        self,
        alert: UnifiedAlert,
        active_incidents: list[Incident],
    ) -> Incident | None:
        """Find an existing active incident this alert should be grouped into.

        Returns the incident to group into, or None if a new incident should be created.
        """
        if not active_incidents:
            return None

        now = time.time()
        candidates: list[tuple[Incident, float]] = []

        for incident in active_incidents:
            # Skip resolved/postmortem incidents
            age = now - incident.triggered_at.timestamp()
            if age > self.group_window:
                continue

            score = self._similarity_score(alert, incident)
            if score > 0:
                candidates.append((incident, score))

        if not candidates:
            return None

        # Return highest-scoring match
        candidates.sort(key=lambda x: x[1], reverse=True)
        best_incident, best_score = candidates[0]

        if best_score >= 2.0:
            logger.info(
                "alert_grouped",
                alert_id=alert.alert_id,
                grouped_into=best_incident.incident_id,
                score=best_score,
            )
            return best_incident

        return None

    def _similarity_score(self, alert: UnifiedAlert, incident: Incident) -> float:
        """Calculate similarity score between an alert and an existing incident.

        Returns a score where >= 2.0 means "should group".
        """
        score = 0.0

        # Same service is the primary signal
        if alert.service and alert.service.lower() == incident.service.lower():
            score += 1.5

        # Title similarity (simple word overlap)
        alert_words = set(alert.title.lower().split())
        incident_words = set(incident.title.lower().split())
        if alert_words and incident_words:
            overlap = len(alert_words & incident_words) / max(
                len(alert_words), len(incident_words)
            )
            score += overlap * 1.5

        # Same severity is a weak signal
        if alert.severity.value == incident.severity:
            score += 0.3

        # Same source
        if alert.source.value == incident.source:
            score += 0.2

        # Shared labels
        if alert.labels and incident.labels:
            shared = set(alert.labels.keys()) & set(incident.labels.keys())
            if shared:
                matching_values = sum(
                    1 for k in shared if alert.labels[k] == incident.labels.get(k)
                )
                score += matching_values * 0.3

        return score

    def format_grouped_alert_comment(self, alert: UnifiedAlert) -> str:
        """Format a comment for an alert that was grouped into an existing incident."""
        return (
            f"**Related Alert Grouped**\n"
            f"- **Title:** {alert.title}\n"
            f"- **Severity:** {alert.severity.value}\n"
            f"- **Source:** {alert.source.value}\n"
            f"- **Description:** {alert.description[:500]}\n"
            f"- **Triggered At:** {alert.triggered_at.isoformat()}"
        )
