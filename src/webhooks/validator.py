"""Webhook signature/secret validation per source."""

import hashlib
import hmac

from fastapi import Header, HTTPException, Request

from src.config import OpsLensConfig


async def validate_alertmanager(
    request: Request, config: OpsLensConfig
) -> bytes:
    """Validate AlertManager webhook. Returns raw body."""
    body = await request.body()
    secret = config.ALERTMANAGER_SECRET
    if secret:
        sig = request.headers.get("X-Webhook-Signature", "")
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            raise HTTPException(status_code=401, detail="Invalid webhook signature")
    return body


async def validate_grafana(
    request: Request, config: OpsLensConfig
) -> bytes:
    """Validate Grafana webhook. Returns raw body."""
    body = await request.body()
    secret = config.GRAFANA_SECRET
    if secret:
        auth = request.headers.get("Authorization", "")
        if auth != f"Bearer {secret}":
            raise HTTPException(status_code=401, detail="Invalid webhook token")
    return body


async def validate_pagerduty(
    request: Request, config: OpsLensConfig
) -> bytes:
    """Validate PagerDuty webhook v3 signature. Returns raw body."""
    body = await request.body()
    secret = config.PAGERDUTY_WEBHOOK_SECRET
    if secret:
        sig = request.headers.get("X-PagerDuty-Signature", "")
        computed = "v1=" + hmac.new(
            secret.encode(), body, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(sig, computed):
            raise HTTPException(status_code=401, detail="Invalid PagerDuty signature")
    return body
