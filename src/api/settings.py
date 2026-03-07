"""Settings API: lets customers configure integrations from the dashboard."""

from __future__ import annotations

import json
import secrets
from pathlib import Path
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = structlog.get_logger()

router = APIRouter(prefix="/api/settings", tags=["settings"])

# Set by main.py for hot-reload
_app_ref = None


def set_app_ref(app):
    global _app_ref
    _app_ref = app

# Settings file path (persisted across restarts)
SETTINGS_FILE = Path(__file__).parent.parent.parent / "settings.json"

# ---- Schemas ----


class NotionMCPSettings(BaseModel):
    mcp_url: str = "http://localhost:3100/mcp"
    auth_token: str = ""
    incidents_db_id: str = ""
    runbooks_db_id: str = ""
    postmortems_db_id: str = ""
    services_db_id: str = ""
    poll_interval_seconds: int = 30


class SlackSettings(BaseModel):
    enabled: bool = False
    webhook_url: str = ""
    bot_token: str = ""
    channel: str = "#incidents"
    create_war_rooms: bool = True


class GitHubSettings(BaseModel):
    enabled: bool = False
    token: str = ""
    org: str = ""
    default_branch: str = "main"


class JiraSettings(BaseModel):
    enabled: bool = False
    base_url: str = ""
    email: str = ""
    api_token: str = ""
    project_key: str = ""
    default_issue_type: str = "Task"


class LinearSettings(BaseModel):
    enabled: bool = False
    api_key: str = ""
    team_id: str = ""


class AWSSettings(BaseModel):
    enabled: bool = False
    access_key_id: str = ""
    secret_access_key: str = ""
    region: str = "us-east-1"


class GCPSettings(BaseModel):
    enabled: bool = False
    project_id: str = ""
    credentials_json: str = ""
    region: str = "us-central1"


class AzureSettings(BaseModel):
    enabled: bool = False
    tenant_id: str = ""
    client_id: str = ""
    client_secret: str = ""
    subscription_id: str = ""


class WebhookSourceSettings(BaseModel):
    enabled: bool = True
    secret: str = ""


class WebhookSettings(BaseModel):
    alertmanager: WebhookSourceSettings = WebhookSourceSettings()
    grafana: WebhookSourceSettings = WebhookSourceSettings()
    pagerduty: WebhookSourceSettings = WebhookSourceSettings()
    generic: WebhookSourceSettings = WebhookSourceSettings(enabled=True)
    manual: WebhookSourceSettings = WebhookSourceSettings(enabled=True)


class AISettings(BaseModel):
    llm_provider: str = "gemini"  # "anthropic" or "gemini"
    llm_fallback_provider: str = ""  # optional fallback
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-20250514"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash"
    max_concurrent_agents: int = 5


class OperationalSettings(BaseModel):
    dedup_window_seconds: int = 300
    auto_escalation_minutes: int = 30
    ticket_provider: str = ""  # "jira" or "linear"


class AllSettings(BaseModel):
    notion_mcp: NotionMCPSettings = NotionMCPSettings()
    slack: SlackSettings = SlackSettings()
    github: GitHubSettings = GitHubSettings()
    jira: JiraSettings = JiraSettings()
    linear: LinearSettings = LinearSettings()
    aws: AWSSettings = AWSSettings()
    gcp: GCPSettings = GCPSettings()
    azure: AzureSettings = AzureSettings()
    webhooks: WebhookSettings = WebhookSettings()
    ai: AISettings = AISettings()
    operational: OperationalSettings = OperationalSettings()


# ---- Helpers ----


def _mask_secret(value: str) -> str:
    """Mask a secret for safe display: show first 4 and last 4 chars."""
    if not value or len(value) < 12:
        return "****" if value else ""
    return value[:4] + "*" * (len(value) - 8) + value[-4:]


def _load_settings() -> dict[str, Any]:
    """Load settings from file, falling back to env-based defaults."""
    if SETTINGS_FILE.exists():
        saved = json.loads(SETTINGS_FILE.read_text())
        # Merge with defaults so new integration sections aren't missing
        defaults = AllSettings().model_dump()
        for key, value in defaults.items():
            if key not in saved:
                saved[key] = value
        return saved
    # Bootstrap from current env config
    from src.config import get_config
    cfg = get_config()
    settings = AllSettings(
        notion_mcp=NotionMCPSettings(
            mcp_url=cfg.NOTION_MCP_URL,
            auth_token=cfg.MCP_AUTH_TOKEN,
            incidents_db_id=cfg.NOTION_INCIDENTS_DB_ID,
            runbooks_db_id=cfg.NOTION_RUNBOOKS_DB_ID,
            postmortems_db_id=cfg.NOTION_POSTMORTEMS_DB_ID,
            services_db_id=cfg.NOTION_SERVICES_DB_ID,
            poll_interval_seconds=cfg.NOTION_POLL_INTERVAL_SECONDS,
        ),
        slack=SlackSettings(
            enabled=bool(cfg.SLACK_WEBHOOK_URL or cfg.SLACK_BOT_TOKEN),
            webhook_url=cfg.SLACK_WEBHOOK_URL,
            bot_token=cfg.SLACK_BOT_TOKEN,
            channel=cfg.SLACK_CHANNEL,
            create_war_rooms=cfg.SLACK_CREATE_WAR_ROOMS,
        ),
        github=GitHubSettings(
            enabled=bool(cfg.GITHUB_TOKEN),
            token=cfg.GITHUB_TOKEN,
            org=cfg.GITHUB_ORG,
            default_branch=cfg.GITHUB_DEFAULT_BRANCH,
        ),
        jira=JiraSettings(
            enabled=bool(cfg.JIRA_BASE_URL and cfg.JIRA_API_TOKEN),
            base_url=cfg.JIRA_BASE_URL,
            email=cfg.JIRA_EMAIL,
            api_token=cfg.JIRA_API_TOKEN,
            project_key=cfg.JIRA_PROJECT_KEY,
            default_issue_type=cfg.JIRA_DEFAULT_ISSUE_TYPE,
        ),
        linear=LinearSettings(
            enabled=bool(cfg.LINEAR_API_KEY),
            api_key=cfg.LINEAR_API_KEY,
            team_id=cfg.LINEAR_TEAM_ID,
        ),
        aws=AWSSettings(
            enabled=bool(cfg.AWS_ACCESS_KEY_ID),
            access_key_id=cfg.AWS_ACCESS_KEY_ID,
            secret_access_key=cfg.AWS_SECRET_ACCESS_KEY,
            region=cfg.AWS_REGION,
        ),
        gcp=GCPSettings(
            enabled=bool(cfg.GCP_PROJECT_ID),
            project_id=cfg.GCP_PROJECT_ID,
            credentials_json=cfg.GCP_CREDENTIALS_JSON,
            region=cfg.GCP_REGION,
        ),
        azure=AzureSettings(
            enabled=bool(cfg.AZURE_TENANT_ID),
            tenant_id=cfg.AZURE_TENANT_ID,
            client_id=cfg.AZURE_CLIENT_ID,
            client_secret=cfg.AZURE_CLIENT_SECRET,
            subscription_id=cfg.AZURE_SUBSCRIPTION_ID,
        ),
        webhooks=WebhookSettings(
            alertmanager=WebhookSourceSettings(secret=cfg.ALERTMANAGER_SECRET),
            grafana=WebhookSourceSettings(secret=cfg.GRAFANA_SECRET),
            pagerduty=WebhookSourceSettings(secret=cfg.PAGERDUTY_WEBHOOK_SECRET),
        ),
        ai=AISettings(
            llm_provider=cfg.LLM_PROVIDER,
            llm_fallback_provider=cfg.LLM_FALLBACK_PROVIDER,
            anthropic_api_key=cfg.ANTHROPIC_API_KEY,
            anthropic_model=cfg.ANTHROPIC_MODEL,
            gemini_api_key=cfg.GEMINI_API_KEY,
            gemini_model=cfg.GEMINI_MODEL,
            max_concurrent_agents=cfg.MAX_CONCURRENT_AGENTS,
        ),
        operational=OperationalSettings(
            dedup_window_seconds=cfg.DEDUP_WINDOW_SECONDS,
            auto_escalation_minutes=cfg.AUTO_ESCALATION_MINUTES,
            ticket_provider=cfg.TICKET_PROVIDER,
        ),
    )
    return settings.model_dump()


def _save_settings(data: dict[str, Any]) -> None:
    """Persist settings to file."""
    SETTINGS_FILE.write_text(json.dumps(data, indent=2))
    logger.info("settings_saved")


def _mask_settings(data: dict[str, Any]) -> dict[str, Any]:
    """Return settings with secrets masked for the frontend."""
    masked = json.loads(json.dumps(data))  # deep copy
    # Mask sensitive fields
    secret_paths = [
        ("notion_mcp", "auth_token"),
        ("slack", "webhook_url"),
        ("slack", "bot_token"),
        ("github", "token"),
        ("jira", "api_token"),
        ("linear", "api_key"),
        ("aws", "access_key_id"),
        ("aws", "secret_access_key"),
        ("gcp", "credentials_json"),
        ("azure", "client_secret"),
        ("webhooks", "alertmanager", "secret"),
        ("webhooks", "grafana", "secret"),
        ("webhooks", "pagerduty", "secret"),
        ("ai", "anthropic_api_key"),
        ("ai", "gemini_api_key"),
    ]
    for path in secret_paths:
        obj = masked
        for key in path[:-1]:
            obj = obj.get(key, {})
        if path[-1] in obj and obj[path[-1]]:
            obj[path[-1]] = _mask_secret(obj[path[-1]])
    return masked


# ---- Endpoints ----


@router.get("")
async def get_settings():
    """Get current settings (secrets masked)."""
    data = _load_settings()
    return _mask_settings(data)


@router.put("")
async def update_settings(updates: dict[str, Any]):
    """Update settings. Only provided fields are changed.
    Fields with value '****' or masked values are skipped (not overwritten).
    """
    current = _load_settings()

    def _merge(base: dict, patch: dict, secret_fields: set[str] | None = None) -> dict:
        for key, value in patch.items():
            if isinstance(value, dict) and isinstance(base.get(key), dict):
                _merge(base[key], value, secret_fields)
            else:
                # Skip masked values (don't overwrite secrets with masks)
                if isinstance(value, str) and ("****" in value):
                    continue
                base[key] = value
        return base

    _merge(current, updates)
    # Validate
    try:
        AllSettings(**current)
    except Exception as e:
        raise HTTPException(400, f"Invalid settings: {e}")

    _save_settings(current)

    # Hot-reload integrations with new settings
    if _app_ref:
        try:
            from src.main import reload_integrations
            result = await reload_integrations(_app_ref)
            logger.info("integrations_hot_reloaded", result=result)
        except Exception as e:
            logger.error("hot_reload_failed", error=str(e))

    return _mask_settings(current)


@router.post("/generate-secret")
async def generate_webhook_secret():
    """Generate a random webhook secret for a source."""
    return {"secret": secrets.token_hex(32)}


@router.post("/test/notion")
async def test_notion_connection():
    """Test Notion MCP server connectivity."""
    data = _load_settings()
    mcp_url = data.get("notion_mcp", {}).get("mcp_url", "")
    auth_token = data.get("notion_mcp", {}).get("auth_token", "")
    if not mcp_url:
        return {"status": "error", "message": "MCP URL not configured"}

    try:
        from src.notion_mcp.client import NotionMCPClient
        client = NotionMCPClient(mcp_url, auth_token)
        await client.initialize()
        tools = await client.list_tools()
        await client.close()
        return {
            "status": "ok",
            "message": f"Connected to Notion MCP. {len(tools)} tools available.",
            "tools": [t.get("name") for t in tools],
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.post("/test/slack")
async def test_slack_connection():
    """Send a test Slack notification."""
    data = _load_settings()
    webhook_url = data.get("slack", {}).get("webhook_url", "")
    if not webhook_url:
        return {"status": "error", "message": "Slack webhook URL not configured"}

    try:
        import httpx
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(webhook_url, json={
                "text": "OpsLens test notification - connection successful!",
            })
            resp.raise_for_status()
        return {"status": "ok", "message": "Test message sent to Slack"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.post("/test/ai")
async def test_ai_connection():
    """Test LLM API connectivity (Anthropic or Gemini)."""
    data = _load_settings()
    ai = data.get("ai", {})
    provider = ai.get("llm_provider", "gemini")

    if provider == "anthropic":
        api_key = ai.get("anthropic_api_key", "")
        model = ai.get("anthropic_model", "claude-sonnet-4-20250514")
        if not api_key:
            return {"status": "error", "message": "Anthropic API key not configured"}
        try:
            import anthropic
            client = anthropic.AsyncAnthropic(api_key=api_key)
            response = await client.messages.create(
                model=model,
                max_tokens=50,
                messages=[{"role": "user", "content": "Reply with exactly: OpsLens connection OK"}],
            )
            text = response.content[0].text if response.content else ""
            return {"status": "ok", "message": f"Claude API connected. Model: {model}", "response": text}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    elif provider == "gemini":
        api_key = ai.get("gemini_api_key", "")
        model = ai.get("gemini_model", "gemini-2.0-flash")
        if not api_key:
            return {"status": "error", "message": "Gemini API key not configured"}
        try:
            from google import genai
            client = genai.Client(api_key=api_key)
            response = await client.aio.models.generate_content(
                model=model,
                contents="Reply with exactly: OpsLens connection OK",
            )
            text = response.text if response.text else ""
            return {"status": "ok", "message": f"Gemini API connected. Model: {model}", "response": text}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    return {"status": "error", "message": f"Unknown provider: {provider}"}


@router.post("/test/github")
async def test_github_connection():
    """Test GitHub API connectivity."""
    data = _load_settings()
    gh = data.get("github", {})
    if not gh.get("token"):
        return {"status": "disabled", "message": "GitHub token not configured"}

    try:
        from src.integrations.github_integration import GitHubIntegration
        integration = GitHubIntegration(
            token=gh["token"],
            org=gh.get("org", ""),
            default_branch=gh.get("default_branch", "main"),
        )
        return await integration.test_connection()
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.post("/test/jira")
async def test_jira_connection():
    """Test Jira API connectivity."""
    data = _load_settings()
    jira = data.get("jira", {})
    if not jira.get("base_url") or not jira.get("api_token"):
        return {"status": "disabled", "message": "Jira not configured"}

    try:
        from src.integrations.jira_integration import JiraIntegration
        integration = JiraIntegration(
            base_url=jira["base_url"],
            email=jira.get("email", ""),
            api_token=jira["api_token"],
            project_key=jira.get("project_key", ""),
        )
        return await integration.test_connection()
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.post("/test/linear")
async def test_linear_connection():
    """Test Linear API connectivity."""
    data = _load_settings()
    linear = data.get("linear", {})
    if not linear.get("api_key"):
        return {"status": "disabled", "message": "Linear not configured"}

    try:
        from src.integrations.jira_integration import LinearIntegration
        integration = LinearIntegration(
            api_key=linear["api_key"],
            team_id=linear.get("team_id", ""),
        )
        return await integration.test_connection()
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.post("/test/aws")
async def test_aws_connection():
    """Test AWS API connectivity."""
    data = _load_settings()
    aws = data.get("aws", {})
    if not aws.get("access_key_id"):
        return {"status": "disabled", "message": "AWS not configured"}

    try:
        from src.integrations.cloud_providers import AWSIntegration
        integration = AWSIntegration(
            access_key_id=aws["access_key_id"],
            secret_access_key=aws.get("secret_access_key", ""),
            region=aws.get("region", "us-east-1"),
        )
        return await integration.test_connection()
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.post("/test/gcp")
async def test_gcp_connection():
    """Test GCP API connectivity."""
    data = _load_settings()
    gcp = data.get("gcp", {})
    if not gcp.get("project_id"):
        return {"status": "disabled", "message": "GCP not configured"}

    try:
        from src.integrations.cloud_providers import GCPIntegration
        integration = GCPIntegration(
            project_id=gcp["project_id"],
            credentials_json=gcp.get("credentials_json", ""),
            region=gcp.get("region", "us-central1"),
        )
        return await integration.test_connection()
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.post("/test/azure")
async def test_azure_connection():
    """Test Azure API connectivity."""
    data = _load_settings()
    azure = data.get("azure", {})
    if not azure.get("tenant_id"):
        return {"status": "disabled", "message": "Azure not configured"}

    try:
        from src.integrations.cloud_providers import AzureIntegration
        integration = AzureIntegration(
            tenant_id=azure["tenant_id"],
            client_id=azure.get("client_id", ""),
            client_secret=azure.get("client_secret", ""),
            subscription_id=azure.get("subscription_id", ""),
        )
        return await integration.test_connection()
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.get("/webhook-urls")
async def get_webhook_urls():
    """Return the webhook endpoint URLs for customer to configure in their tools."""
    from src.config import get_config
    cfg = get_config()
    host = cfg.APP_HOST if cfg.APP_HOST != "0.0.0.0" else "localhost"
    base = f"http://{host}:{cfg.APP_PORT}"
    return {
        "base_url": base,
        "endpoints": {
            "alertmanager": {
                "url": f"{base}/webhooks/alertmanager",
                "method": "POST",
                "auth": "HMAC-SHA256 via X-Webhook-Signature header",
                "docs": "Configure in Prometheus AlertManager as a webhook receiver",
            },
            "grafana": {
                "url": f"{base}/webhooks/grafana",
                "method": "POST",
                "auth": "Bearer token via Authorization header",
                "docs": "Add as Contact Point in Grafana Alerting > Contact Points",
            },
            "pagerduty": {
                "url": f"{base}/webhooks/pagerduty",
                "method": "POST",
                "auth": "PagerDuty v3 webhook signature via X-PagerDuty-Signature",
                "docs": "Add as Generic Webhook (v3) in PagerDuty > Integrations > Webhooks",
            },
            "generic": {
                "url": f"{base}/webhooks/generic",
                "method": "POST",
                "auth": "None (open)",
                "docs": "Send JSON: {title, description, severity, service, labels}",
            },
            "manual": {
                "url": f"{base}/webhooks/manual",
                "method": "POST",
                "auth": "None (open)",
                "docs": "Create incidents manually: {title, description, severity, service}",
            },
        },
    }


@router.get("/setup-status")
async def get_setup_status():
    """Check which integrations are configured and working."""
    data = _load_settings()
    mcp = data.get("notion_mcp", {})
    slack = data.get("slack", {})
    ai = data.get("ai", {})
    webhooks = data.get("webhooks", {})
    github = data.get("github", {})
    jira = data.get("jira", {})
    linear = data.get("linear", {})
    aws = data.get("aws", {})
    gcp = data.get("gcp", {})
    azure = data.get("azure", {})

    steps = {
        "notion_mcp": {
            "name": "Notion MCP Server",
            "configured": bool(mcp.get("mcp_url") and mcp.get("auth_token")),
            "required": True,
            "description": "Connect your Notion workspace via MCP server",
        },
        "notion_databases": {
            "name": "Notion Databases",
            "configured": bool(mcp.get("incidents_db_id")),
            "required": True,
            "description": "Set up incident tracking databases in Notion",
        },
        "ai_agents": {
            "name": f"AI Agents ({ai.get('llm_provider', 'gemini').title()})",
            "configured": bool(ai.get("anthropic_api_key") or ai.get("gemini_api_key")),
            "required": True,
            "description": "Configure LLM API for AI-powered incident analysis",
        },
        "slack": {
            "name": "Slack Integration",
            "configured": bool((slack.get("webhook_url") or slack.get("bot_token")) and slack.get("enabled")),
            "required": False,
            "description": "Slack notifications, war rooms, and interactive messages",
        },
        "github": {
            "name": "GitHub Integration",
            "configured": bool(github.get("token") and github.get("enabled")),
            "required": False,
            "description": "Deploy correlation, commit linking, rollback PRs",
        },
        "jira": {
            "name": "Jira Integration",
            "configured": bool(jira.get("api_token") and jira.get("enabled")),
            "required": False,
            "description": "Auto-create tickets from postmortem action items",
        },
        "linear": {
            "name": "Linear Integration",
            "configured": bool(linear.get("api_key") and linear.get("enabled")),
            "required": False,
            "description": "Create Linear issues from incident action items",
        },
        "aws": {
            "name": "AWS Cloud",
            "configured": bool(aws.get("access_key_id") and aws.get("enabled")),
            "required": False,
            "description": "CloudWatch alerts, ECS health, auto-remediation",
        },
        "gcp": {
            "name": "GCP Cloud",
            "configured": bool(gcp.get("project_id") and gcp.get("enabled")),
            "required": False,
            "description": "Cloud Monitoring, GKE pod health",
        },
        "azure": {
            "name": "Azure Cloud",
            "configured": bool(azure.get("tenant_id") and azure.get("enabled")),
            "required": False,
            "description": "Monitor alerts, AKS health, VM actions",
        },
        "alertmanager": {
            "name": "Prometheus AlertManager",
            "configured": bool(webhooks.get("alertmanager", {}).get("secret")),
            "required": False,
            "description": "Receive alerts from Prometheus",
        },
        "grafana": {
            "name": "Grafana Alerting",
            "configured": bool(webhooks.get("grafana", {}).get("secret")),
            "required": False,
            "description": "Receive alerts from Grafana",
        },
        "pagerduty": {
            "name": "PagerDuty",
            "configured": bool(webhooks.get("pagerduty", {}).get("secret")),
            "required": False,
            "description": "Receive alerts from PagerDuty",
        },
    }

    required_done = all(s["configured"] for s in steps.values() if s["required"])
    total_configured = sum(1 for s in steps.values() if s["configured"])

    return {
        "ready": required_done,
        "configured_count": total_configured,
        "total_count": len(steps),
        "steps": steps,
    }
