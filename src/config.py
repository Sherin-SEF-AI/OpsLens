"""OpsLens configuration via environment variables."""

from pydantic_settings import BaseSettings
from pydantic import Field


class OpsLensConfig(BaseSettings):
    """All OpsLens configuration loaded from environment variables / .env file."""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    # Core
    APP_NAME: str = "OpsLens"
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000
    LOG_LEVEL: str = "INFO"
    ENVIRONMENT: str = "production"

    # Notion MCP Server
    NOTION_MCP_URL: str = "http://localhost:3100/mcp"
    MCP_AUTH_TOKEN: str = ""

    # Notion Integration
    NOTION_TOKEN: str = ""
    NOTION_ROOT_PAGE_ID: str = ""

    # Notion Database IDs (populated after workspace setup)
    NOTION_INCIDENTS_DB_ID: str = ""
    NOTION_INCIDENTS_DS_ID: str = ""  # data_source_id for MCP query-data-source
    NOTION_RUNBOOKS_DB_ID: str = ""
    NOTION_SERVICES_DB_ID: str = ""
    NOTION_POSTMORTEMS_DB_ID: str = ""
    NOTION_ONCALL_DB_ID: str = ""
    NOTION_CONFIDENCE_DB_ID: str = ""
    NOTION_COMMAND_CENTER_PAGE_ID: str = ""

    # LLM Provider
    LLM_PROVIDER: str = "gemini"  # "anthropic" or "gemini"
    LLM_FALLBACK_PROVIDER: str = ""  # optional fallback

    # Claude API
    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_MODEL: str = "claude-sonnet-4-20250514"

    # Google Gemini API
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-2.0-flash"

    # Webhook Security
    ALERTMANAGER_SECRET: str = ""
    GRAFANA_SECRET: str = ""
    PAGERDUTY_WEBHOOK_SECRET: str = ""

    # Slack Notifications (simple webhook)
    SLACK_WEBHOOK_URL: str = ""
    SLACK_CHANNEL: str = "#incidents"

    # Slack Deep Integration (Bot token for war rooms, interactive messages)
    SLACK_BOT_TOKEN: str = ""
    SLACK_CREATE_WAR_ROOMS: bool = True

    # GitHub Integration
    GITHUB_TOKEN: str = ""
    GITHUB_ORG: str = ""
    GITHUB_DEFAULT_BRANCH: str = "main"

    # Jira Integration
    JIRA_BASE_URL: str = ""
    JIRA_EMAIL: str = ""
    JIRA_API_TOKEN: str = ""
    JIRA_PROJECT_KEY: str = ""
    JIRA_DEFAULT_ISSUE_TYPE: str = "Task"

    # Linear Integration
    LINEAR_API_KEY: str = ""
    LINEAR_TEAM_ID: str = ""

    # AWS Cloud Integration
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""
    AWS_REGION: str = "us-east-1"

    # GCP Cloud Integration
    GCP_PROJECT_ID: str = ""
    GCP_CREDENTIALS_JSON: str = ""
    GCP_REGION: str = "us-central1"

    # Azure Cloud Integration
    AZURE_TENANT_ID: str = ""
    AZURE_CLIENT_ID: str = ""
    AZURE_CLIENT_SECRET: str = ""
    AZURE_SUBSCRIPTION_ID: str = ""

    # Ticket Management Provider
    TICKET_PROVIDER: str = ""  # "jira" or "linear"

    # Operational
    DEDUP_WINDOW_SECONDS: int = 300
    AUTO_ESCALATION_MINUTES: int = 30
    MAX_CONCURRENT_AGENTS: int = 5
    NOTION_POLL_INTERVAL_SECONDS: int = 30


def get_config() -> OpsLensConfig:
    """Get the application configuration singleton."""
    return OpsLensConfig()
