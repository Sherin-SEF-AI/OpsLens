#!/usr/bin/env python3
"""Send realistic test alerts to OpsLens webhook endpoints."""

import argparse
import json
import sys
from datetime import datetime, timezone

import httpx

BASE_URL = "http://localhost:8000"


def _set_base_url(url: str):
    global BASE_URL
    BASE_URL = url


def alertmanager_payload(severity: str, service: str) -> dict:
    """Generate a realistic Prometheus AlertManager webhook payload."""
    alert_map = {
        "api-gateway": {
            "alertname": "HighErrorRate",
            "summary": f"High 5xx error rate on {service}",
            "description": f"The {service} is returning 5xx errors at a rate of 12.5% over the last 5 minutes. Normal baseline is < 0.1%.",
        },
        "payment-service": {
            "alertname": "PaymentLatencyHigh",
            "summary": f"Payment processing latency exceeding SLA on {service}",
            "description": f"P99 latency for {service} is 4500ms, exceeding the 2000ms SLA threshold.",
        },
        "database-cluster": {
            "alertname": "DatabaseConnectionPoolExhausted",
            "summary": f"Connection pool near exhaustion on {service}",
            "description": f"The {service} connection pool is at 97% utilization. Only 3 connections remaining out of 100.",
        },
        "auth-service": {
            "alertname": "AuthenticationFailureSpike",
            "summary": f"Spike in authentication failures on {service}",
            "description": f"Authentication failure rate on {service} increased from 2% to 35% in the last 10 minutes.",
        },
    }
    info = alert_map.get(service, {
        "alertname": "GenericAlert",
        "summary": f"Alert on {service}",
        "description": f"An alert was triggered for {service}.",
    })

    return {
        "version": "4",
        "groupKey": f"{{alertname=\"{info['alertname']}\"}}:{{service=\"{service}\"}}",
        "truncatedAlerts": 0,
        "status": "firing",
        "receiver": "opslens-webhook",
        "groupLabels": {"alertname": info["alertname"]},
        "commonLabels": {
            "alertname": info["alertname"],
            "severity": severity,
            "service": service,
            "environment": "production",
            "team": "platform",
        },
        "commonAnnotations": {
            "summary": info["summary"],
            "description": info["description"],
        },
        "externalURL": "http://alertmanager.internal:9093",
        "alerts": [
            {
                "status": "firing",
                "labels": {
                    "alertname": info["alertname"],
                    "severity": severity,
                    "service": service,
                    "instance": f"{service}-prod-1:8080",
                    "job": service,
                    "environment": "production",
                    "namespace": "production",
                },
                "annotations": {
                    "summary": info["summary"],
                    "description": info["description"],
                    "dashboard_url": f"https://grafana.internal/d/{service}",
                    "runbook_url": f"https://wiki.internal/runbooks/{info['alertname']}",
                },
                "startsAt": datetime.now(timezone.utc).isoformat(),
                "endsAt": "0001-01-01T00:00:00Z",
                "generatorURL": f"http://prometheus.internal:9090/graph?g0.expr=rate(http_requests_total{{service=\"{service}\",code=~\"5..\"}}[5m])",
                "fingerprint": f"abc{hash(service + info['alertname']) % 10000:04d}",
            }
        ],
    }


def grafana_payload(severity: str, service: str) -> dict:
    """Generate a realistic Grafana alerting webhook payload."""
    return {
        "receiver": "opslens",
        "status": "firing",
        "orgId": 1,
        "alerts": [
            {
                "status": "firing",
                "labels": {
                    "alertname": "HighCPUUsage",
                    "severity": severity,
                    "service": service,
                    "grafana_folder": "Production Alerts",
                },
                "annotations": {
                    "summary": f"CPU usage above 90% on {service}",
                    "description": f"The {service} has been running at 94% CPU utilization for the last 10 minutes. Auto-scaling has not kicked in.",
                },
                "startsAt": datetime.now(timezone.utc).isoformat(),
                "endsAt": None,
                "generatorURL": f"https://grafana.internal/alerting/grafana/{service}/view",
                "fingerprint": f"grf{hash(service) % 10000:04d}",
                "silenceURL": f"https://grafana.internal/alerting/silence/new?alertmanager=grafana&matcher=service%3D{service}",
                "dashboardURL": f"https://grafana.internal/d/{service}-overview",
                "panelURL": f"https://grafana.internal/d/{service}-overview?viewPanel=cpu",
                "values": {"cpu_percent": 94.2},
            }
        ],
        "groupLabels": {"alertname": "HighCPUUsage"},
        "commonLabels": {"severity": severity, "service": service},
        "commonAnnotations": {},
        "externalURL": "https://grafana.internal",
        "version": "1",
        "groupKey": f"HighCPUUsage-{service}",
        "truncatedAlerts": 0,
        "title": f"[FIRING:1] HighCPUUsage {service}",
        "state": "alerting",
        "message": f"CPU usage above 90% on {service} - currently at 94.2%",
    }


def pagerduty_payload(severity: str, service: str) -> dict:
    """Generate a realistic PagerDuty v3 webhook payload."""
    urgency = "high" if severity in ("critical", "high") else "low"
    return {
        "event": {
            "id": f"01DEN{hash(service) % 100000:05d}",
            "event_type": "incident.triggered",
            "resource_type": "incident",
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "agent": {
                "type": "service_reference",
                "id": "PSERVICE01",
                "summary": service,
            },
            "client": None,
            "data": {
                "id": f"Q1INQPU{hash(service) % 1000:03d}",
                "type": "incident",
                "title": f"[{severity.upper()}] {service} - Service Degradation Detected",
                "description": f"Automated monitoring detected degraded performance on {service}. Multiple health checks are failing.",
                "urgency": urgency,
                "status": "triggered",
                "html_url": f"https://mycompany.pagerduty.com/incidents/Q1INQPU{hash(service) % 1000:03d}",
                "service": {
                    "id": "PSERVICE01",
                    "type": "service_reference",
                    "name": service,
                    "html_url": f"https://mycompany.pagerduty.com/services/PSERVICE01",
                },
                "priority": {
                    "id": "P53ZZH5",
                    "type": "priority_reference",
                    "name": severity.upper(),
                },
            },
        }
    }


def generic_payload(title: str, description: str, severity: str, service: str) -> dict:
    """Generate a generic alert payload."""
    return {
        "title": title,
        "description": description,
        "severity": severity,
        "service": service,
        "source": "generic",
        "labels": {"environment": "production", "region": "us-east-1"},
        "url": "",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def _load_secrets() -> dict[str, str]:
    """Load webhook secrets from .env file."""
    try:
        from dotenv import load_dotenv
        import os
        load_dotenv()
        return {
            "alertmanager": os.getenv("ALERTMANAGER_SECRET", ""),
            "grafana": os.getenv("GRAFANA_SECRET", ""),
            "pagerduty": os.getenv("PAGERDUTY_WEBHOOK_SECRET", ""),
        }
    except ImportError:
        return {}


def _sign_request(source: str, body: bytes, secrets: dict[str, str]) -> dict[str, str]:
    """Generate auth headers for the webhook source."""
    import hashlib
    import hmac

    headers: dict[str, str] = {}
    secret = secrets.get(source, "")
    if not secret:
        return headers

    if source == "alertmanager":
        sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        headers["X-Webhook-Signature"] = sig
    elif source == "grafana":
        headers["Authorization"] = f"Bearer {secret}"
    elif source == "pagerduty":
        sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        headers["X-PagerDuty-Signature"] = f"v1={sig}"

    return headers


def send_webhook(source: str, payload: dict) -> None:
    """Send the webhook to OpsLens with proper signing."""
    url = f"{BASE_URL}/webhooks/{source}"
    print(f"\nSending {source} webhook to {url}")
    print(f"Payload: {json.dumps(payload, indent=2, default=str)[:500]}...")

    secrets = _load_secrets()
    body = json.dumps(payload, default=str).encode()
    headers = {"Content-Type": "application/json"}
    headers.update(_sign_request(source, body, secrets))

    if any(k for k in headers if k != "Content-Type"):
        print(f"Signing: {', '.join(k for k in headers if k != 'Content-Type')}")

    try:
        response = httpx.post(url, content=body, headers=headers, timeout=10.0)
        print(f"Response [{response.status_code}]: {response.json()}")
    except httpx.ConnectError:
        print(f"ERROR: Could not connect to {BASE_URL}. Is OpsLens running?")
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: {e}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Send test alerts to OpsLens")
    parser.add_argument(
        "--source",
        choices=["alertmanager", "grafana", "pagerduty", "generic"],
        default="alertmanager",
        help="Alert source type",
    )
    parser.add_argument(
        "--severity",
        choices=["critical", "high", "medium", "low"],
        default="high",
        help="Alert severity",
    )
    parser.add_argument(
        "--service",
        default="api-gateway",
        help="Affected service name",
    )
    parser.add_argument("--title", default="Custom Alert", help="Alert title (generic only)")
    parser.add_argument("--description", default="Something broke", help="Alert description (generic only)")
    parser.add_argument("--url", default=None, help="OpsLens base URL")

    args = parser.parse_args()
    if args.url:
        _set_base_url(args.url)

    if args.source == "alertmanager":
        payload = alertmanager_payload(args.severity, args.service)
    elif args.source == "grafana":
        payload = grafana_payload(args.severity, args.service)
    elif args.source == "pagerduty":
        payload = pagerduty_payload(args.severity, args.service)
    elif args.source == "generic":
        payload = generic_payload(args.title, args.description, args.severity, args.service)
    else:
        print(f"Unknown source: {args.source}")
        sys.exit(1)

    send_webhook(args.source, payload)


if __name__ == "__main__":
    main()
