"""OpsLens FastAPI application entry point."""

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import structlog
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from src.agents.orchestrator import AgentOrchestrator
from src.api.router import router as api_router
from src.api.router import set_alert_handler, set_commander, set_dependencies, set_search_dependencies
from src.api.integrations_router import router as integrations_router
from src.api.integrations_router import set_integration_deps
from src.api.settings import router as settings_router, _load_settings, set_app_ref
from src.config import OpsLensConfig, get_config
from src.incidents.manager import IncidentManager
from src.incidents.models import IncidentStatus
from src.integrations.slack_notifier import send_slack_notification
from src.integrations.github_integration import GitHubIntegration
from src.integrations.slack_integration import SlackIntegration
from src.integrations.jira_integration import JiraIntegration, LinearIntegration
from src.integrations.cloud_providers import (
    AWSIntegration, GCPIntegration, AzureIntegration, CloudProviderManager,
)
from src.integrations.knowledge_base import KnowledgeBase, EmbeddingProvider
from src.integrations.outbound_webhooks import OutboundWebhookManager
from src.notion_mcp.client import NotionMCPClient
from src.notion_mcp.tools import NotionMCPTools
from src.sync.command_center import CommandCenter
from src.sync.notion_watcher import NotionWatcher
from src.webhooks.router import router as webhook_router
from src.webhooks.router import set_incident_handler
from src.webhooks.schemas import UnifiedAlert

# Configure structlog
structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer()
        if True
        else structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(
        structlog.get_config().get("min_level", 0)
    ),
)

logger = structlog.get_logger()


# --- WebSocket Manager ---

class WSManager:
    """Simple WebSocket connection manager with broadcast."""

    def __init__(self):
        self._connections: list[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._connections.append(ws)
        logger.info("ws_client_connected", total=len(self._connections))

    def disconnect(self, ws: WebSocket) -> None:
        if ws in self._connections:
            self._connections.remove(ws)
        logger.info("ws_client_disconnected", total=len(self._connections))

    async def broadcast(self, message: dict[str, Any]) -> None:
        """Broadcast a message to all connected clients."""
        dead: list[WebSocket] = []
        data = json.dumps(message, default=str)
        for ws in self._connections:
            try:
                await ws.send_text(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


ws_manager = WSManager()


def _create_integrations(config: OpsLensConfig, log=None):
    """Create all integration objects from settings.json (with env var fallback)."""
    log = log or logger
    saved = _load_settings()

    gh_cfg = saved.get("github", {})
    sl_cfg = saved.get("slack", {})
    ji_cfg = saved.get("jira", {})
    li_cfg = saved.get("linear", {})
    aw_cfg = saved.get("aws", {})
    gc_cfg = saved.get("gcp", {})
    az_cfg = saved.get("azure", {})
    ai_cfg = saved.get("ai", {})

    github = GitHubIntegration(
        token=gh_cfg.get("token") or config.GITHUB_TOKEN,
        org=gh_cfg.get("org") or config.GITHUB_ORG,
        default_branch=gh_cfg.get("default_branch") or config.GITHUB_DEFAULT_BRANCH,
    )
    if github.enabled:
        log.info("github_integration_enabled", org=github.org)

    slack_integration = SlackIntegration(
        bot_token=sl_cfg.get("bot_token") or config.SLACK_BOT_TOKEN,
        webhook_url=sl_cfg.get("webhook_url") or config.SLACK_WEBHOOK_URL,
        default_channel=sl_cfg.get("channel") or config.SLACK_CHANNEL,
        create_war_rooms=sl_cfg.get("create_war_rooms", config.SLACK_CREATE_WAR_ROOMS),
    )
    if slack_integration.enabled:
        log.info("slack_deep_integration_enabled")

    jira = JiraIntegration(
        base_url=ji_cfg.get("base_url") or config.JIRA_BASE_URL,
        email=ji_cfg.get("email") or config.JIRA_EMAIL,
        api_token=ji_cfg.get("api_token") or config.JIRA_API_TOKEN,
        project_key=ji_cfg.get("project_key") or config.JIRA_PROJECT_KEY,
        default_issue_type=ji_cfg.get("default_issue_type") or config.JIRA_DEFAULT_ISSUE_TYPE,
    )
    if jira.enabled:
        log.info("jira_integration_enabled")

    linear = LinearIntegration(
        api_key=li_cfg.get("api_key") or config.LINEAR_API_KEY,
        team_id=li_cfg.get("team_id") or config.LINEAR_TEAM_ID,
    )
    if linear.enabled:
        log.info("linear_integration_enabled")

    cloud = CloudProviderManager(
        aws=AWSIntegration(
            access_key_id=aw_cfg.get("access_key_id") or config.AWS_ACCESS_KEY_ID,
            secret_access_key=aw_cfg.get("secret_access_key") or config.AWS_SECRET_ACCESS_KEY,
            region=aw_cfg.get("region") or config.AWS_REGION,
        ),
        gcp=GCPIntegration(
            project_id=gc_cfg.get("project_id") or config.GCP_PROJECT_ID,
            credentials_json=gc_cfg.get("credentials_json") or config.GCP_CREDENTIALS_JSON,
            region=gc_cfg.get("region") or config.GCP_REGION,
        ),
        azure=AzureIntegration(
            tenant_id=az_cfg.get("tenant_id") or config.AZURE_TENANT_ID,
            client_id=az_cfg.get("client_id") or config.AZURE_CLIENT_ID,
            client_secret=az_cfg.get("client_secret") or config.AZURE_CLIENT_SECRET,
            subscription_id=az_cfg.get("subscription_id") or config.AZURE_SUBSCRIPTION_ID,
        ),
    )
    if cloud.any_enabled:
        log.info("cloud_providers_enabled", aws=cloud.aws.enabled, gcp=cloud.gcp.enabled, azure=cloud.azure.enabled)

    api_key = ai_cfg.get("gemini_api_key") or ai_cfg.get("anthropic_api_key") or config.GEMINI_API_KEY or config.ANTHROPIC_API_KEY
    embedding_provider = EmbeddingProvider(
        provider=ai_cfg.get("llm_provider") or config.LLM_PROVIDER,
        api_key=api_key,
    )
    knowledge_base = KnowledgeBase(embedding_provider=embedding_provider)
    knowledge_base.load_from_disk()
    log.info("knowledge_base_initialized", documents=knowledge_base.document_count)

    outbound_webhooks = OutboundWebhookManager()

    return github, slack_integration, jira, linear, cloud, knowledge_base, outbound_webhooks


async def reload_integrations(app: FastAPI) -> dict[str, bool]:
    """Hot-reload all integrations from settings.json. Called after settings update."""
    config = app.state.config
    log = logger.bind(action="reload_integrations")

    github, slack_integration, jira, linear, cloud, knowledge_base, outbound_webhooks = (
        _create_integrations(config, log)
    )

    # Preserve existing outbound webhook subscriptions
    if hasattr(app.state, "outbound_webhooks"):
        outbound_webhooks._subscriptions = app.state.outbound_webhooks._subscriptions

    # Update app state
    app.state.github = github
    app.state.slack_integration = slack_integration
    app.state.jira = jira
    app.state.linear = linear
    app.state.cloud = cloud
    app.state.knowledge_base = knowledge_base
    app.state.outbound_webhooks = outbound_webhooks

    # Re-wire orchestrator
    if hasattr(app.state, "orchestrator"):
        app.state.orchestrator.set_integrations(
            github=github,
            slack_integration=slack_integration,
            jira=jira,
            linear=linear,
            cloud=cloud,
            knowledge_base=knowledge_base,
            outbound_webhooks=outbound_webhooks,
        )

    # Re-wire API router dependencies
    incident_manager = getattr(app.state, "incident_manager", None)
    orchestrator = getattr(app.state, "orchestrator", None)
    set_integration_deps(
        github=github,
        slack=slack_integration,
        jira=jira,
        linear=linear,
        cloud=cloud,
        knowledge_base=knowledge_base,
        outbound_webhooks=outbound_webhooks,
        incident_manager=incident_manager,
        orchestrator=orchestrator,
    )

    log.info(
        "integrations_reloaded",
        github=github.enabled,
        slack=slack_integration.enabled,
        jira=jira.enabled,
        linear=linear.enabled,
    )

    return {
        "github": github.enabled,
        "slack": slack_integration.enabled,
        "jira": jira.enabled,
        "linear": linear.enabled,
        "aws": cloud.aws.enabled,
        "gcp": cloud.gcp.enabled,
        "azure": cloud.azure.enabled,
    }


# --- Lifespan ---

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown."""
    config: OpsLensConfig = app.state.config
    log = logger.bind(app=config.APP_NAME)

    # Initialize MCP client
    mcp_client = NotionMCPClient(config.NOTION_MCP_URL, config.MCP_AUTH_TOKEN)
    notion_tools = NotionMCPTools(mcp_client)

    # Initialize incident manager
    incident_manager = IncidentManager(config, notion_tools)
    incident_manager.set_ws_broadcast(ws_manager.broadcast)

    # Initialize agent orchestrator
    orchestrator = AgentOrchestrator(config, notion_tools, incident_manager)

    # Initialize Incident Commander
    from src.agents.commander import IncidentCommander
    from src.agents.llm_client import LLMClient
    commander_llm = LLMClient.from_config(config)
    commander_model = config.GEMINI_MODEL if config.LLM_PROVIDER == "gemini" else config.ANTHROPIC_MODEL
    commander = IncidentCommander(commander_llm, commander_model, notion_tools)
    set_commander(commander)

    # Initialize bi-directional Notion watcher
    notion_watcher = NotionWatcher(
        notion_tools=notion_tools,
        incident_manager=incident_manager,
        poll_interval=config.NOTION_POLL_INTERVAL_SECONDS,
    )
    # Register human-in-the-loop reactions
    notion_watcher.on_change("severity", orchestrator.handle_severity_change)
    notion_watcher.on_change("status", orchestrator.handle_status_change)
    notion_watcher.on_change("root_cause", orchestrator.handle_root_cause_added)
    notion_watcher.on_change("comment_escalate", orchestrator.handle_escalation)

    # --- Enterprise Integrations (from settings.json, not just env vars) ---
    github, slack_integration, jira, linear, cloud, knowledge_base, outbound_webhooks = (
        _create_integrations(config, log)
    )

    # Wire orchestrator with integrations
    orchestrator.set_integrations(
        github=github,
        slack_integration=slack_integration,
        jira=jira,
        linear=linear,
        cloud=cloud,
        knowledge_base=knowledge_base,
        outbound_webhooks=outbound_webhooks,
    )

    # Wire up API dependencies
    set_dependencies(incident_manager, orchestrator, notion_watcher)
    set_search_dependencies(notion_tools=notion_tools, knowledge_base=knowledge_base)
    set_integration_deps(
        github=github,
        slack=slack_integration,
        jira=jira,
        linear=linear,
        cloud=cloud,
        knowledge_base=knowledge_base,
        outbound_webhooks=outbound_webhooks,
        incident_manager=incident_manager,
        orchestrator=orchestrator,
    )

    # Wire up webhook handler
    async def handle_alert(alert: UnifiedAlert) -> None:
        """Process a normalized alert into an incident."""
        incident = await incident_manager.create_incident(alert)

        # Send Slack notification (deep integration or simple webhook)
        if slack_integration.enabled:
            asyncio.create_task(
                slack_integration.send_incident_notification(incident, "created")
            )
            # Create war room for P0/P1
            if slack_integration.create_war_rooms and incident.severity in ("P0-Critical", "P1-High"):
                asyncio.create_task(slack_integration.create_war_room(incident))
        elif config.SLACK_WEBHOOK_URL:
            asyncio.create_task(
                send_slack_notification(
                    config.SLACK_WEBHOOK_URL,
                    config.SLACK_CHANNEL,
                    incident,
                    notify_type="created",
                )
            )

        # Dispatch outbound webhooks
        asyncio.create_task(
            outbound_webhooks.dispatch(
                "incident.created",
                {"alert_id": alert.alert_id},
                incident,
            )
        )

        # Run agent pipeline if firing
        if alert.status.value == "firing":
            asyncio.create_task(
                orchestrator.handle_new_incident(incident, alert)
            )

    set_incident_handler(handle_alert)
    set_alert_handler(handle_alert)

    # Try to initialize MCP session
    try:
        await mcp_client.initialize()
        log.info("mcp_session_initialized")
    except Exception:
        log.warning("mcp_session_init_failed", msg="Will retry on first use")

    # Rehydrate incidents from Notion DB
    try:
        loaded = await incident_manager.rehydrate_from_notion()
        log.info("incidents_rehydrated", count=loaded)
    except Exception:
        log.warning("rehydrate_failed_on_startup")

    # Start Notion watcher for bi-directional sync
    await notion_watcher.start()

    # Initialize Command Center (living Notion stats page)
    command_center = CommandCenter(
        notion_tools=notion_tools,
        incident_manager=incident_manager,
        page_id=config.NOTION_COMMAND_CENTER_PAGE_ID,
        update_interval=120,
    )
    await command_center.start()

    # Store references on app state
    app.state.mcp_client = mcp_client
    app.state.notion_tools = notion_tools
    app.state.incident_manager = incident_manager
    app.state.orchestrator = orchestrator
    app.state.notion_watcher = notion_watcher
    app.state.command_center = command_center
    app.state.github = github
    app.state.slack_integration = slack_integration
    app.state.jira = jira
    app.state.linear = linear
    app.state.cloud = cloud
    app.state.knowledge_base = knowledge_base
    app.state.outbound_webhooks = outbound_webhooks

    log.info("opslens_started", host=config.APP_HOST, port=config.APP_PORT)
    yield

    # Shutdown
    await command_center.stop()
    await notion_watcher.stop()
    await mcp_client.close()
    log.info("opslens_shutdown")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    config = get_config()

    app = FastAPI(
        title="OpsLens",
        description="Autonomous Incident Response Orchestrator via Notion MCP",
        version="1.0.0",
        lifespan=lifespan,
    )

    # Store config on app state
    app.state.config = config
    set_app_ref(app)

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Include routers
    app.include_router(webhook_router)
    app.include_router(api_router)
    app.include_router(settings_router)
    app.include_router(integrations_router)

    # Health check
    @app.get("/health")
    async def health():
        mcp_ok = False
        try:
            if hasattr(app.state, "mcp_client") and app.state.mcp_client._initialized:
                mcp_ok = True
        except Exception:
            pass

        active_count = 0
        if hasattr(app.state, "incident_manager"):
            active_count = len(app.state.incident_manager.get_active_incidents())

        watcher_ok = False
        try:
            if hasattr(app.state, "notion_watcher"):
                watcher_ok = app.state.notion_watcher._running
        except Exception:
            pass

        command_center_ok = False
        try:
            if hasattr(app.state, "command_center"):
                command_center_ok = app.state.command_center._running
        except Exception:
            pass

        # Integration statuses
        integrations = {}
        for name in ("github", "slack_integration", "jira", "linear"):
            try:
                obj = getattr(app.state, name, None)
                integrations[name.replace("_integration", "")] = obj.enabled if obj else False
            except Exception:
                integrations[name] = False

        cloud_enabled = {}
        try:
            if hasattr(app.state, "cloud"):
                cloud_enabled = {
                    "aws": app.state.cloud.aws.enabled,
                    "gcp": app.state.cloud.gcp.enabled,
                    "azure": app.state.cloud.azure.enabled,
                }
        except Exception:
            pass

        kb_docs = 0
        try:
            if hasattr(app.state, "knowledge_base"):
                kb_docs = app.state.knowledge_base.document_count
        except Exception:
            pass

        outbound_count = 0
        try:
            if hasattr(app.state, "outbound_webhooks"):
                outbound_count = len(app.state.outbound_webhooks._subscriptions)
        except Exception:
            pass

        return {
            "status": "healthy",
            "mcp_connected": mcp_ok,
            "notion_watcher": watcher_ok,
            "command_center": command_center_ok,
            "active_incidents": active_count,
            "ws_clients": len(ws_manager._connections),
            "integrations": integrations,
            "cloud_providers": cloud_enabled,
            "knowledge_base_documents": kb_docs,
            "outbound_webhook_subscriptions": outbound_count,
        }

    # WebSocket endpoint
    @app.websocket("/ws/incidents")
    async def ws_incidents(ws: WebSocket):
        await ws_manager.connect(ws)
        try:
            while True:
                # Keep connection alive, receive any client messages
                data = await ws.receive_text()
                # Could handle client commands here
        except WebSocketDisconnect:
            ws_manager.disconnect(ws)

    # Serve static dashboard files if they exist
    dashboard_dir = Path(__file__).parent / "dashboard"
    if dashboard_dir.exists() and any(dashboard_dir.iterdir()):
        app.mount("/", StaticFiles(directory=str(dashboard_dir), html=True), name="dashboard")

    return app


app = create_app()

if __name__ == "__main__":
    config = get_config()
    uvicorn.run(
        "src.main:app",
        host=config.APP_HOST,
        port=config.APP_PORT,
        reload=config.ENVIRONMENT == "development",
    )
