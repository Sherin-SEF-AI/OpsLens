"""Tests for webhook normalization across all alert sources."""

from datetime import datetime, timezone

import pytest

from src.webhooks.normalizer import (
    SEVERITY_MAP,
    _make_fingerprint,
    _map_severity,
    normalize_alertmanager,
    normalize_generic,
    normalize_grafana,
    normalize_manual,
    normalize_pagerduty,
)
from src.webhooks.schemas import (
    AlertManagerAlert,
    AlertManagerWebhook,
    AlertSource,
    AlertStatus,
    GenericAlert,
    GrafanaAlert,
    GrafanaWebhook,
    ManualIncident,
    PagerDutyEvent,
    PagerDutyWebhook,
    Severity,
    UnifiedAlert,
)


# ---------------------------------------------------------------------------
# Severity mapping
# ---------------------------------------------------------------------------

class TestSeverityMapping:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("critical", Severity.P0),
            ("CRITICAL", Severity.P0),
            ("high", Severity.P1),
            ("warning", Severity.P1),
            ("medium", Severity.P2),
            ("info", Severity.P3),
            ("low", Severity.P3),
            ("none", Severity.P3),
            ("p0", Severity.P0),
            ("p1", Severity.P1),
            ("p2", Severity.P2),
            ("p3", Severity.P3),
            ("  Critical  ", Severity.P0),  # whitespace
        ],
    )
    def test_severity_mapping(self, raw, expected):
        assert _map_severity(raw) == expected

    def test_unknown_severity_defaults_to_p2(self):
        assert _map_severity("banana") == Severity.P2
        assert _map_severity("") == Severity.P2


# ---------------------------------------------------------------------------
# Fingerprint generation
# ---------------------------------------------------------------------------

class TestFingerprint:
    def test_deterministic(self):
        fp1 = _make_fingerprint("svc", "title", "src")
        fp2 = _make_fingerprint("svc", "title", "src")
        assert fp1 == fp2

    def test_different_inputs_differ(self):
        fp1 = _make_fingerprint("svc-a", "title", "src")
        fp2 = _make_fingerprint("svc-b", "title", "src")
        assert fp1 != fp2

    def test_length_is_16(self):
        fp = _make_fingerprint("x", "y", "z")
        assert len(fp) == 16


# ---------------------------------------------------------------------------
# AlertManager normalization
# ---------------------------------------------------------------------------

class TestNormalizeAlertManager:
    def test_firing_alert(self, sample_alertmanager_webhook):
        alerts = normalize_alertmanager(sample_alertmanager_webhook)
        assert len(alerts) == 1
        alert = alerts[0]
        assert isinstance(alert, UnifiedAlert)
        assert alert.source == AlertSource.PROMETHEUS
        assert alert.status == AlertStatus.FIRING
        assert alert.title == "CPU usage is above 95%"
        assert alert.service == "api-server"
        assert alert.severity == Severity.P0  # critical -> P0
        assert alert.source_url == "http://prometheus:9090/graph?g0.expr=cpu"
        assert alert.dashboard_url == "https://grafana.example.com/d/cpu"
        assert alert.runbook_url == "https://wiki.example.com/runbooks/cpu"
        assert alert.fingerprint == "prom-fp-001"

    def test_resolved_alert(self):
        webhook = AlertManagerWebhook(
            status="resolved",
            alerts=[
                AlertManagerAlert(
                    status="resolved",
                    labels={"alertname": "HighCPU", "severity": "critical", "service": "api"},
                    annotations={"summary": "CPU OK now"},
                    startsAt=datetime(2026, 3, 15, 10, 0, 0, tzinfo=timezone.utc),
                    endsAt=datetime(2026, 3, 15, 10, 30, 0, tzinfo=timezone.utc),
                    fingerprint="resolved-fp",
                )
            ],
        )
        alerts = normalize_alertmanager(webhook)
        assert len(alerts) == 1
        assert alerts[0].status == AlertStatus.RESOLVED
        assert alerts[0].resolved_at == datetime(2026, 3, 15, 10, 30, 0, tzinfo=timezone.utc)

    def test_missing_annotations_uses_alertname(self):
        webhook = AlertManagerWebhook(
            status="firing",
            alerts=[
                AlertManagerAlert(
                    status="firing",
                    labels={"alertname": "DiskFull"},
                    annotations={},
                    startsAt=datetime(2026, 3, 15, 10, 0, 0, tzinfo=timezone.utc),
                )
            ],
        )
        alerts = normalize_alertmanager(webhook)
        assert alerts[0].title == "DiskFull"

    def test_empty_labels_defaults(self):
        webhook = AlertManagerWebhook(
            status="firing",
            alerts=[
                AlertManagerAlert(
                    status="firing",
                    labels={},
                    annotations={},
                    startsAt=datetime(2026, 3, 15, 10, 0, 0, tzinfo=timezone.utc),
                )
            ],
        )
        alerts = normalize_alertmanager(webhook)
        assert alerts[0].title == "Unknown Alert"
        assert alerts[0].service == "unknown"
        assert alerts[0].severity == Severity.P2  # medium default

    def test_multiple_alerts(self):
        webhook = AlertManagerWebhook(
            status="firing",
            alerts=[
                AlertManagerAlert(
                    status="firing",
                    labels={"alertname": "A", "service": "svc1"},
                    annotations={},
                    startsAt=datetime(2026, 3, 15, 10, 0, 0, tzinfo=timezone.utc),
                ),
                AlertManagerAlert(
                    status="resolved",
                    labels={"alertname": "B", "service": "svc2"},
                    annotations={},
                    startsAt=datetime(2026, 3, 15, 10, 0, 0, tzinfo=timezone.utc),
                    endsAt=datetime(2026, 3, 15, 11, 0, 0, tzinfo=timezone.utc),
                ),
            ],
        )
        alerts = normalize_alertmanager(webhook)
        assert len(alerts) == 2
        assert alerts[0].status == AlertStatus.FIRING
        assert alerts[1].status == AlertStatus.RESOLVED


# ---------------------------------------------------------------------------
# Grafana normalization
# ---------------------------------------------------------------------------

class TestNormalizeGrafana:
    def test_firing_alert(self, sample_grafana_webhook):
        alerts = normalize_grafana(sample_grafana_webhook)
        assert len(alerts) == 1
        alert = alerts[0]
        assert alert.source == AlertSource.GRAFANA
        assert alert.status == AlertStatus.FIRING
        assert alert.title == "Memory above 90%"
        assert alert.service == "web-frontend"
        assert alert.severity == Severity.P1  # warning -> P1
        assert alert.dashboard_url == "https://grafana.example.com/d/mem"

    def test_resolved_grafana(self):
        webhook = GrafanaWebhook(
            status="resolved",
            alerts=[
                GrafanaAlert(
                    status="resolved",
                    labels={"alertname": "HighMem", "service": "web"},
                    annotations={"summary": "Memory recovered"},
                    startsAt=datetime(2026, 3, 15, 11, 0, 0, tzinfo=timezone.utc),
                    endsAt=datetime(2026, 3, 15, 11, 30, 0, tzinfo=timezone.utc),
                    fingerprint="graf-resolved",
                )
            ],
        )
        alerts = normalize_grafana(webhook)
        assert alerts[0].status == AlertStatus.RESOLVED
        assert alerts[0].resolved_at is not None


# ---------------------------------------------------------------------------
# PagerDuty normalization
# ---------------------------------------------------------------------------

class TestNormalizePagerDuty:
    def test_triggered(self, sample_pagerduty_webhook):
        alerts = normalize_pagerduty(sample_pagerduty_webhook)
        assert len(alerts) == 1
        alert = alerts[0]
        assert alert.source == AlertSource.PAGERDUTY
        assert alert.status == AlertStatus.FIRING
        assert alert.title == "Database connection pool exhausted"
        assert alert.service == "database"
        assert alert.severity == Severity.P1  # high urgency
        assert alert.source_url == "https://pagerduty.com/incidents/PDB123"
        assert alert.resolved_at is None

    def test_resolved(self):
        webhook = PagerDutyWebhook(
            event=PagerDutyEvent(
                id="pd-evt-002",
                event_type="incident.resolved",
                occurred_at=datetime(2026, 3, 15, 13, 0, 0, tzinfo=timezone.utc),
                data={
                    "title": "DB pool recovered",
                    "urgency": "low",
                    "service": {"name": "database"},
                },
            )
        )
        alerts = normalize_pagerduty(webhook)
        assert alerts[0].status == AlertStatus.RESOLVED
        assert alerts[0].resolved_at is not None
        assert alerts[0].severity == Severity.P2  # low urgency

    def test_missing_service_defaults(self):
        webhook = PagerDutyWebhook(
            event=PagerDutyEvent(
                id="pd-evt-003",
                event_type="incident.triggered",
                occurred_at=datetime(2026, 3, 15, 14, 0, 0, tzinfo=timezone.utc),
                data={"title": "Something broke"},
            )
        )
        alerts = normalize_pagerduty(webhook)
        assert alerts[0].service == "unknown"


# ---------------------------------------------------------------------------
# Generic normalization
# ---------------------------------------------------------------------------

class TestNormalizeGeneric:
    def test_full_fields(self, sample_generic_alert):
        alerts = normalize_generic(sample_generic_alert)
        assert len(alerts) == 1
        alert = alerts[0]
        assert alert.source == AlertSource.GENERIC
        assert alert.status == AlertStatus.FIRING
        assert alert.title == "Disk space low"
        assert alert.service == "storage"
        assert alert.severity == Severity.P1  # high -> P1
        assert alert.source_url == "https://monitor.example.com/disk"
        assert alert.labels == {"host": "storage-01", "mount": "/"}

    def test_minimal_fields(self):
        alert = GenericAlert(title="Minimal alert")
        alerts = normalize_generic(alert)
        assert len(alerts) == 1
        assert alerts[0].title == "Minimal alert"
        assert alerts[0].service == "unknown"
        assert alerts[0].description == "Minimal alert"  # falls back to title

    def test_no_timestamp_uses_now(self):
        alert = GenericAlert(title="No timestamp")
        alerts = normalize_generic(alert)
        assert alerts[0].triggered_at is not None


# ---------------------------------------------------------------------------
# Manual normalization
# ---------------------------------------------------------------------------

class TestNormalizeManual:
    def test_full_fields(self, sample_manual_incident):
        alerts = normalize_manual(sample_manual_incident)
        assert len(alerts) == 1
        alert = alerts[0]
        assert alert.source == AlertSource.MANUAL
        assert alert.status == AlertStatus.FIRING
        assert alert.title == "Customer reports slow checkout"
        assert alert.service == "checkout-service"
        assert alert.severity == Severity.P1  # P1 -> P1

    def test_minimal_manual(self):
        incident = ManualIncident(title="Quick incident")
        alerts = normalize_manual(incident)
        assert len(alerts) == 1
        assert alerts[0].title == "Quick incident"
        assert alerts[0].description == "Quick incident"  # falls back
        assert alerts[0].service == "unknown"


# ---------------------------------------------------------------------------
# All normalizers return proper UnifiedAlert
# ---------------------------------------------------------------------------

class TestUnifiedAlertContract:
    """Ensure all normalizers return UnifiedAlert with required fields."""

    def _check_alert(self, alert: UnifiedAlert):
        assert alert.alert_id
        assert alert.title
        assert alert.description
        assert isinstance(alert.severity, Severity)
        assert isinstance(alert.status, AlertStatus)
        assert isinstance(alert.source, AlertSource)
        assert alert.service
        assert alert.triggered_at is not None
        assert alert.fingerprint

    def test_alertmanager_contract(self, sample_alertmanager_webhook):
        for alert in normalize_alertmanager(sample_alertmanager_webhook):
            self._check_alert(alert)

    def test_grafana_contract(self, sample_grafana_webhook):
        for alert in normalize_grafana(sample_grafana_webhook):
            self._check_alert(alert)

    def test_pagerduty_contract(self, sample_pagerduty_webhook):
        for alert in normalize_pagerduty(sample_pagerduty_webhook):
            self._check_alert(alert)

    def test_generic_contract(self, sample_generic_alert):
        for alert in normalize_generic(sample_generic_alert):
            self._check_alert(alert)

    def test_manual_contract(self, sample_manual_incident):
        for alert in normalize_manual(sample_manual_incident):
            self._check_alert(alert)


# ---------------------------------------------------------------------------
# Fingerprint consistency
# ---------------------------------------------------------------------------

class TestFingerprintConsistency:
    def test_same_alertmanager_same_fingerprint(self, sample_alertmanager_webhook):
        alerts1 = normalize_alertmanager(sample_alertmanager_webhook)
        alerts2 = normalize_alertmanager(sample_alertmanager_webhook)
        assert alerts1[0].fingerprint == alerts2[0].fingerprint

    def test_same_manual_same_fingerprint(self, sample_manual_incident):
        alerts1 = normalize_manual(sample_manual_incident)
        alerts2 = normalize_manual(sample_manual_incident)
        assert alerts1[0].fingerprint == alerts2[0].fingerprint
