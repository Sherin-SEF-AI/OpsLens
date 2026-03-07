"""Bi-directional Notion sync: detect human edits and trigger agent reactions.

Polls active incident pages in Notion periodically. When a human changes
severity, adds a root cause, writes an ESCALATE comment, or resolves the
incident directly in Notion, the system detects the diff and kicks off
the appropriate agent workflow.
"""

import asyncio
import json as _json
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine

import structlog

from src.incidents.manager import IncidentManager
from src.incidents.models import Incident, IncidentStatus, TimelineEventType
from src.notion_mcp.tools import NotionMCPTools

logger = structlog.get_logger()


class NotionChangeEvent:
    """Describes a single detected change on a Notion page."""

    def __init__(
        self,
        incident_id: str,
        change_type: str,
        old_value: Any = None,
        new_value: Any = None,
        detail: str = "",
    ):
        self.incident_id = incident_id
        self.change_type = change_type  # severity | status | root_cause | comment_escalate
        self.old_value = old_value
        self.new_value = new_value
        self.detail = detail

    def __repr__(self) -> str:
        return (
            f"NotionChangeEvent({self.change_type}: "
            f"{self.old_value!r} -> {self.new_value!r})"
        )


# Type alias for reaction callbacks
ReactionCallback = Callable[[NotionChangeEvent, Incident], Coroutine[Any, Any, None]]


class NotionWatcher:
    """Polls Notion incident pages and detects human-made changes.

    Maintains a snapshot of each active incident's Notion properties.
    On every poll cycle it fetches fresh data, diffs against the snapshot,
    and emits change events that trigger agent reactions.
    """

    def __init__(
        self,
        notion_tools: NotionMCPTools,
        incident_manager: IncidentManager,
        poll_interval: float = 30.0,
    ):
        self.notion = notion_tools
        self.incidents = incident_manager
        self.poll_interval = poll_interval
        self._snapshots: dict[str, dict[str, Any]] = {}
        self._known_comment_ids: dict[str, set[str]] = {}
        self._reactions: dict[str, list[ReactionCallback]] = {}
        self._running = False
        self._task: asyncio.Task | None = None

    # --- Reaction registration ---

    def on_change(self, change_type: str, callback: ReactionCallback) -> None:
        """Register a callback for a specific change type."""
        self._reactions.setdefault(change_type, []).append(callback)

    async def _emit(self, event: NotionChangeEvent, incident: Incident) -> None:
        """Fire all registered callbacks for an event."""
        callbacks = self._reactions.get(event.change_type, [])
        for cb in callbacks:
            try:
                await cb(event, incident)
            except Exception:
                logger.exception(
                    "notion_watcher_reaction_error",
                    change_type=event.change_type,
                    incident_id=event.incident_id,
                )

    # --- Snapshot helpers ---

    @staticmethod
    def _extract_property(properties: dict, name: str, prop_type: str) -> Any:
        """Extract a typed value from Notion page properties."""
        prop = properties.get(name, {})
        if prop_type == "select":
            sel = prop.get("select")
            return sel.get("name", "") if sel else ""
        elif prop_type == "rich_text":
            parts = prop.get("rich_text", [])
            return "".join(p.get("plain_text", "") for p in parts)
        elif prop_type == "title":
            parts = prop.get("title", [])
            return "".join(p.get("plain_text", "") for p in parts)
        elif prop_type == "date":
            d = prop.get("date")
            return d.get("start", "") if d else ""
        elif prop_type == "number":
            return prop.get("number")
        elif prop_type == "checkbox":
            return prop.get("checkbox", False)
        return None

    def _build_snapshot(self, page_data: dict) -> dict[str, Any]:
        """Build a comparable snapshot from a Notion page response."""
        props = page_data.get("properties", {})
        return {
            "status": self._extract_property(props, "Status", "select"),
            "severity": self._extract_property(props, "Severity", "select"),
            "root_cause": self._extract_property(props, "Root Cause", "rich_text"),
            "last_edited": page_data.get("last_edited_time", ""),
        }

    # --- Polling loop ---

    async def start(self) -> None:
        """Start the background polling loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        logger.info("notion_watcher_started", interval=self.poll_interval)

    async def stop(self) -> None:
        """Stop the polling loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("notion_watcher_stopped")

    async def _poll_loop(self) -> None:
        """Main polling loop."""
        # Initial delay to let the system settle after startup
        await asyncio.sleep(5.0)

        while self._running:
            try:
                await self._poll_once()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("notion_watcher_poll_error")

            await asyncio.sleep(self.poll_interval)

    async def _poll_once(self) -> None:
        """Single poll cycle: check all active incidents for Notion changes."""
        active = self.incidents.get_active_incidents()
        if not active:
            return

        for incident in active:
            if not incident.notion_page_id:
                continue

            try:
                await self._check_incident(incident)
            except Exception:
                logger.exception(
                    "notion_watcher_check_error",
                    incident_id=incident.incident_id,
                )

    async def _check_incident(self, incident: Incident) -> None:
        """Fetch a single incident page from Notion and diff against snapshot."""
        log = logger.bind(incident_id=incident.incident_id)

        # Fetch current page state from Notion
        raw = await self.notion.fetch_page(incident.notion_page_id)

        # Parse the JSON response
        try:
            page_data = _json.loads(raw)
        except (ValueError, TypeError):
            # fetch_page might return non-JSON text; skip
            return

        current = self._build_snapshot(page_data)
        prev = self._snapshots.get(incident.incident_id)

        if prev is None:
            # First time seeing this incident — store baseline, no diff
            self._snapshots[incident.incident_id] = current
            # Also capture existing comments
            await self._snapshot_comments(incident)
            return

        # --- Detect changes ---
        events: list[NotionChangeEvent] = []

        # 1. Severity change (human upgraded/downgraded)
        if current["severity"] and current["severity"] != prev["severity"]:
            events.append(
                NotionChangeEvent(
                    incident_id=incident.incident_id,
                    change_type="severity",
                    old_value=prev["severity"],
                    new_value=current["severity"],
                )
            )

        # 2. Status change (human resolved, mitigated, etc.)
        if current["status"] and current["status"] != prev["status"]:
            events.append(
                NotionChangeEvent(
                    incident_id=incident.incident_id,
                    change_type="status",
                    old_value=prev["status"],
                    new_value=current["status"],
                )
            )

        # 3. Root cause added (was empty, now has content)
        old_rc = prev.get("root_cause", "")
        new_rc = current.get("root_cause", "")
        if not old_rc and new_rc:
            events.append(
                NotionChangeEvent(
                    incident_id=incident.incident_id,
                    change_type="root_cause",
                    old_value="",
                    new_value=new_rc,
                )
            )

        # Update snapshot
        self._snapshots[incident.incident_id] = current

        # 4. Check for new comments with ESCALATE keyword
        escalate_events = await self._check_escalation_comments(incident)
        events.extend(escalate_events)

        # Fire reactions
        for event in events:
            log.info(
                "notion_change_detected",
                change_type=event.change_type,
                old_value=str(event.old_value)[:100],
                new_value=str(event.new_value)[:100],
            )
            await self._emit(event, incident)

    async def _snapshot_comments(self, incident: Incident) -> None:
        """Capture the current set of comment IDs for an incident page."""
        try:
            raw = await self.notion.list_comments(incident.notion_page_id)
            text = self.notion._extract_text(raw) if not isinstance(raw, str) else raw
            try:
                data = _json.loads(text)
            except (ValueError, TypeError):
                return
            results = data.get("results", [])
            ids = {c.get("id", "") for c in results if c.get("id")}
            self._known_comment_ids[incident.incident_id] = ids
        except Exception:
            logger.debug(
                "notion_watcher_comment_snapshot_error",
                incident_id=incident.incident_id,
            )

    async def _check_escalation_comments(
        self, incident: Incident
    ) -> list[NotionChangeEvent]:
        """Check for new comments containing ESCALATE keyword."""
        events: list[NotionChangeEvent] = []
        try:
            raw = await self.notion.list_comments(incident.notion_page_id)
            text = self.notion._extract_text(raw) if not isinstance(raw, str) else raw
            try:
                data = _json.loads(text)
            except (ValueError, TypeError):
                return events

            results = data.get("results", [])
            known = self._known_comment_ids.get(incident.incident_id, set())

            for comment in results:
                cid = comment.get("id", "")
                if not cid or cid in known:
                    continue

                # New comment — extract text
                rich_text = comment.get("rich_text", [])
                comment_text = " ".join(
                    rt.get("plain_text", "") for rt in rich_text
                ).strip()

                # Check for ESCALATE keyword at start of comment (avoid agent text matches)
                if comment_text.upper().startswith("ESCALATE"):
                    events.append(
                        NotionChangeEvent(
                            incident_id=incident.incident_id,
                            change_type="comment_escalate",
                            new_value=comment_text,
                            detail=f"Escalation requested via Notion comment: {comment_text[:200]}",
                        )
                    )

                known.add(cid)

            self._known_comment_ids[incident.incident_id] = known

        except Exception:
            logger.debug(
                "notion_watcher_escalation_check_error",
                incident_id=incident.incident_id,
            )

        return events

    def cleanup_incident(self, incident_id: str) -> None:
        """Remove snapshot data for a resolved/terminal incident."""
        self._snapshots.pop(incident_id, None)
        self._known_comment_ids.pop(incident_id, None)
