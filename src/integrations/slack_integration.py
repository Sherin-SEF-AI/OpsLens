"""Slack Deep Integration for OpsLens.

Features:
- War room channels: auto-create dedicated incident channels
- Interactive messages: buttons for acknowledge/resolve/escalate
- Thread-based updates: sync incident timeline to Slack threads
- Slash command support: /opslens status, /opslens escalate
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx
import structlog

from src.incidents.models import Incident

logger = structlog.get_logger()

SEVERITY_COLORS = {
    "P0-Critical": "#FF0000",
    "P1-High": "#FF8C00",
    "P2-Medium": "#FFD700",
    "P3-Low": "#4169E1",
}

SEVERITY_EMOJI = {
    "P0-Critical": ":red_circle:",
    "P1-High": ":large_orange_circle:",
    "P2-Medium": ":large_yellow_circle:",
    "P3-Low": ":large_blue_circle:",
}


class SlackIntegration:
    """Deep Slack integration with war rooms, interactive messages, and thread sync."""

    def __init__(
        self,
        bot_token: str = "",
        webhook_url: str = "",
        default_channel: str = "#incidents",
        create_war_rooms: bool = True,
    ):
        self.bot_token = bot_token
        self.webhook_url = webhook_url
        self.default_channel = default_channel
        self.create_war_rooms = create_war_rooms
        self._enabled = bool(bot_token or webhook_url)
        # Track thread timestamps for incident updates
        self._incident_threads: dict[str, dict[str, str]] = {}
        # channel_id -> ts for the main incident message

    @property
    def enabled(self) -> bool:
        return self._enabled

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.bot_token}",
            "Content-Type": "application/json",
        }

    async def _api_call(
        self, method: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Make a Slack Web API call."""
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"https://slack.com/api/{method}",
                headers=self._headers(),
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            if not data.get("ok"):
                logger.error(
                    "slack_api_error",
                    method=method,
                    error=data.get("error", "unknown"),
                )
            return data

    # --- War Room Channel Creation ---

    async def create_war_room(
        self,
        incident: Incident,
        invite_users: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create a dedicated incident war room channel.

        Channel name format: inc-XXXX-<service>-<short-title>
        """
        if not self.bot_token:
            return {"error": "Bot token required for war room creation"}

        # Build channel name (Slack limits: lowercase, no spaces, max 80 chars)
        slug = incident.title.lower()
        # Keep only alphanumeric and hyphens
        slug = "".join(c if c.isalnum() else "-" for c in slug)
        slug = "-".join(part for part in slug.split("-") if part)[:30]
        channel_name = f"inc-{incident.incident_id.lower()}-{slug}"[:80]

        try:
            # Create channel
            result = await self._api_call(
                "conversations.create",
                {"name": channel_name, "is_private": False},
            )

            if not result.get("ok"):
                # Channel might already exist
                if result.get("error") == "name_taken":
                    # Find the existing channel
                    search_result = await self._api_call(
                        "conversations.list",
                        {"types": "public_channel", "limit": 200},
                    )
                    for ch in search_result.get("channels", []):
                        if ch["name"] == channel_name:
                            channel_id = ch["id"]
                            break
                    else:
                        return {"error": f"Channel {channel_name} exists but not found"}
                else:
                    return {"error": result.get("error", "Unknown error")}
            else:
                channel_id = result["channel"]["id"]

            # Set channel topic
            topic = (
                f"{SEVERITY_EMOJI.get(incident.severity, ':warning:')} "
                f"{incident.incident_id} | {incident.severity} | "
                f"Service: {incident.service} | Status: {incident.status.value}"
            )
            await self._api_call(
                "conversations.setTopic",
                {"channel": channel_id, "topic": topic[:250]},
            )

            # Set channel purpose
            purpose = (
                f"War room for incident {incident.incident_id}: {incident.title}. "
                f"All incident discussion and updates happen here."
            )
            await self._api_call(
                "conversations.setPurpose",
                {"channel": channel_id, "purpose": purpose[:250]},
            )

            # Post initial incident summary with interactive buttons
            await self._post_incident_card(channel_id, incident)

            # Invite users if specified
            if invite_users:
                for user_id in invite_users:
                    try:
                        await self._api_call(
                            "conversations.invite",
                            {"channel": channel_id, "users": user_id},
                        )
                    except Exception:
                        pass

            # Pin the incident summary
            # (We'll pin it after posting)

            logger.info(
                "slack_war_room_created",
                channel=channel_name,
                incident_id=incident.incident_id,
            )

            return {
                "channel_id": channel_id,
                "channel_name": channel_name,
                "status": "created",
            }

        except Exception as exc:
            logger.error(
                "slack_war_room_error",
                incident_id=incident.incident_id,
                error=str(exc),
            )
            return {"error": str(exc)}

    # --- Interactive Messages ---

    async def _post_incident_card(
        self, channel_id: str, incident: Incident
    ) -> str | None:
        """Post an interactive incident card with action buttons."""
        color = SEVERITY_COLORS.get(incident.severity, "#808080")
        emoji = SEVERITY_EMOJI.get(incident.severity, ":warning:")

        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{incident.incident_id}: {incident.title}",
                },
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Severity:*\n{emoji} {incident.severity}"},
                    {"type": "mrkdwn", "text": f"*Status:*\n{incident.status.value}"},
                    {"type": "mrkdwn", "text": f"*Service:*\n{incident.service}"},
                    {"type": "mrkdwn", "text": f"*Source:*\n{incident.source}"},
                ],
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Description:*\n{incident.description[:500]}",
                },
            },
            {"type": "divider"},
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Acknowledge"},
                        "style": "primary",
                        "action_id": "incident_acknowledge",
                        "value": incident.incident_id,
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Escalate"},
                        "style": "danger",
                        "action_id": "incident_escalate",
                        "value": incident.incident_id,
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Resolve"},
                        "action_id": "incident_resolve",
                        "value": incident.incident_id,
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "View in OpsLens"},
                        "action_id": "incident_view",
                        "value": incident.incident_id,
                    },
                ],
            },
        ]

        if incident.notion_page_url:
            blocks.insert(-1, {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"<{incident.notion_page_url}|View in Notion>",
                    }
                ],
            })

        try:
            result = await self._api_call(
                "chat.postMessage",
                {
                    "channel": channel_id,
                    "blocks": blocks,
                    "text": f"[{incident.incident_id}] {incident.title} - {incident.severity}",
                    "unfurl_links": False,
                },
            )

            if result.get("ok"):
                ts = result.get("ts", "")
                # Store thread reference
                self._incident_threads[incident.incident_id] = {
                    "channel_id": channel_id,
                    "thread_ts": ts,
                }
                # Pin the message
                try:
                    await self._api_call(
                        "pins.add", {"channel": channel_id, "timestamp": ts}
                    )
                except Exception:
                    pass
                return ts

        except Exception as exc:
            logger.error("slack_incident_card_error", error=str(exc))

        return None

    async def send_incident_notification(
        self, incident: Incident, notify_type: str = "created"
    ) -> dict[str, Any]:
        """Send incident notification to the default channel with interactive buttons."""
        if not self._enabled:
            return {"error": "Slack not configured"}

        # Use bot token API if available, otherwise fall back to webhook
        if self.bot_token:
            channel = self.default_channel.lstrip("#")
            ts = await self._post_incident_card(channel, incident)
            if ts:
                return {"status": "ok", "thread_ts": ts}
            # Bot token failed (not_in_channel, etc) — fall back to webhook
            logger.warning("slack_bot_post_failed_falling_back_to_webhook", channel=channel)

        # Fallback: simple webhook (no interactivity)
        if self.webhook_url:
            return await self._send_webhook(incident, notify_type)

        return {"error": "No Slack credentials configured"}

    async def _send_webhook(
        self, incident: Incident, notify_type: str
    ) -> dict[str, Any]:
        """Send a simple webhook notification (fallback when no bot token)."""
        color = SEVERITY_COLORS.get(incident.severity, "#808080")

        if notify_type == "created":
            title = f"New Incident: [{incident.incident_id}] {incident.title}"
            text = (
                f"*Severity:* {incident.severity}\n"
                f"*Service:* {incident.service}\n"
                f"*Source:* {incident.source}\n"
                f"*Description:* {incident.description[:200]}"
            )
        elif notify_type == "resolved":
            title = f"Resolved: [{incident.incident_id}] {incident.title}"
            duration = ""
            if incident.duration_seconds:
                mins = incident.duration_seconds // 60
                duration = f" (Duration: {mins}m)"
            text = f"*Service:* {incident.service}{duration}"
        else:
            title = f"[{incident.incident_id}] {incident.title} - {notify_type}"
            text = f"*Status:* {incident.status.value}"

        payload = {
            "text": title,
            "attachments": [
                {"color": color, "text": text, "footer": "OpsLens Incident Response"}
            ],
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(self.webhook_url, json=payload)
                resp.raise_for_status()
            return {"status": "ok"}
        except Exception as exc:
            return {"error": str(exc)}

    # --- Thread-Based Updates ---

    async def post_thread_update(
        self,
        incident_id: str,
        message: str,
        channel_id: str = "",
    ) -> dict[str, Any]:
        """Post an update as a thread reply to the incident's main message."""
        if not self.bot_token:
            return {"error": "Bot token required for thread updates"}

        thread_info = self._incident_threads.get(incident_id, {})
        ch = channel_id or thread_info.get("channel_id", "")
        ts = thread_info.get("thread_ts", "")

        if not ch or not ts:
            # Post to default channel as a new message
            ch = self.default_channel.lstrip("#")
            ts = ""

        payload: dict[str, Any] = {
            "channel": ch,
            "text": message,
            "unfurl_links": False,
        }
        if ts:
            payload["thread_ts"] = ts

        try:
            result = await self._api_call("chat.postMessage", payload)
            return {"status": "ok" if result.get("ok") else "error"}
        except Exception as exc:
            return {"error": str(exc)}

    async def update_incident_status_in_thread(
        self, incident: Incident, old_status: str, new_status: str
    ) -> None:
        """Post a status change update in the incident's Slack thread."""
        emoji_map = {
            "Triggered": ":rotating_light:",
            "Triaged": ":mag:",
            "Investigating": ":microscope:",
            "Mitigated": ":construction:",
            "Resolved": ":white_check_mark:",
            "Postmortem": ":memo:",
        }
        emoji = emoji_map.get(new_status, ":arrows_counterclockwise:")
        message = f"{emoji} Status changed: *{old_status}* → *{new_status}*"
        await self.post_thread_update(incident.incident_id, message)

    async def post_agent_update(
        self, incident_id: str, agent_name: str, summary: str
    ) -> None:
        """Post an agent analysis update to the incident's Slack thread."""
        agent_emoji = {
            "triage": ":mag:",
            "correlation": ":link:",
            "remediation": ":hammer_and_wrench:",
            "postmortem": ":notebook:",
            "communications": ":loudspeaker:",
        }
        emoji = agent_emoji.get(agent_name, ":robot_face:")
        message = f"{emoji} *{agent_name.title()} Agent:*\n{summary[:1000]}"
        await self.post_thread_update(incident_id, message)

    # --- Slash Command Handler ---

    async def handle_slash_command(
        self,
        command: str,
        text: str,
        user_id: str,
        channel_id: str,
        response_url: str,
    ) -> dict[str, Any]:
        """Handle /opslens slash commands.

        Supported commands:
        - /opslens create <severity> <service> <title> - Create an incident
        - /opslens status [incident_id] - Get incident status
        - /opslens escalate <incident_id> - Escalate an incident
        - /opslens list - List active incidents
        - /opslens help - Show help
        """
        parts = text.strip().split(maxsplit=1)
        subcommand = parts[0].lower() if parts else "help"
        args = parts[1] if len(parts) > 1 else ""

        if subcommand == "help":
            return {
                "response_type": "ephemeral",
                "text": (
                    "*OpsLens Slash Commands:*\n"
                    "- `/opslens create <severity> <service> <title>` - Create an incident\n"
                    "- `/opslens status [incident_id]` - Get incident status\n"
                    "- `/opslens escalate <incident_id>` - Escalate an incident\n"
                    "- `/opslens list` - List active incidents\n"
                    "- `/opslens ack <incident_id>` - Acknowledge an incident\n"
                    "- `/opslens resolve <incident_id>` - Resolve an incident\n"
                    "- `/opslens help` - Show this help"
                ),
            }

        # Return the subcommand info for the API layer to process
        return {
            "subcommand": subcommand,
            "args": args,
            "user_id": user_id,
            "channel_id": channel_id,
            "response_url": response_url,
        }

    # --- Interactive Action Handler ---

    async def handle_interaction(
        self, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Handle interactive button clicks from Slack.

        Returns action info for the API layer to process.
        """
        action = payload.get("actions", [{}])[0]
        action_id = action.get("action_id", "")
        incident_id = action.get("value", "")
        user = payload.get("user", {}).get("id", "")

        return {
            "action": action_id,
            "incident_id": incident_id,
            "user_id": user,
            "channel_id": payload.get("channel", {}).get("id", ""),
            "response_url": payload.get("response_url", ""),
        }

    # --- Test Connection ---

    async def test_connection(self) -> dict[str, Any]:
        """Test Slack API connectivity."""
        if not self._enabled:
            return {"status": "disabled", "message": "Slack not configured"}

        if self.bot_token:
            try:
                result = await self._api_call("auth.test", {})
                if result.get("ok"):
                    return {
                        "status": "ok",
                        "message": f"Connected as {result.get('bot_id', 'bot')} in {result.get('team', 'workspace')}",
                        "team": result.get("team", ""),
                        "bot_id": result.get("bot_id", ""),
                    }
                return {"status": "error", "message": result.get("error", "Unknown")}
            except Exception as exc:
                return {"status": "error", "message": str(exc)}

        if self.webhook_url:
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(
                        self.webhook_url,
                        json={"text": "OpsLens connection test"},
                    )
                    resp.raise_for_status()
                return {"status": "ok", "message": "Webhook URL is valid"}
            except Exception as exc:
                return {"status": "error", "message": str(exc)}

        return {"status": "error", "message": "No credentials configured"}
