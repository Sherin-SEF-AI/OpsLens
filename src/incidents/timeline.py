"""Timeline event tracking for incidents."""

from datetime import datetime, timezone

from src.incidents.models import TimelineEvent, TimelineEventType


def create_event(
    event_type: TimelineEventType,
    message: str,
    actor: str = "system",
) -> TimelineEvent:
    """Create a new timeline event."""
    return TimelineEvent(
        timestamp=datetime.now(timezone.utc),
        event_type=event_type,
        message=message,
        actor=actor,
    )


def format_timeline_comment(event: TimelineEvent) -> str:
    """Format a timeline event for posting as a Notion comment."""
    ts = event.timestamp.strftime("%Y-%m-%d %H:%M:%S UTC")
    type_emoji = {
        TimelineEventType.CREATED: "🆕",
        TimelineEventType.STATUS_CHANGE: "🔄",
        TimelineEventType.AGENT_TRIAGE: "🔍",
        TimelineEventType.AGENT_CORRELATION: "🔗",
        TimelineEventType.AGENT_REMEDIATION: "🛠️",
        TimelineEventType.AGENT_POSTMORTEM: "📝",
        TimelineEventType.AGENT_COMMS: "📢",
        TimelineEventType.ALERT_GROUPED: "📎",
        TimelineEventType.COMMENT: "💬",
        TimelineEventType.ESCALATION: "🚨",
        TimelineEventType.MANUAL_ACTION: "👤",
    }
    emoji = type_emoji.get(event.event_type, "ℹ️")
    return f"[{ts}] {emoji} [{event.event_type.value}] {event.message} (by {event.actor})"


def format_timeline_for_postmortem(events: list[TimelineEvent]) -> str:
    """Format the full timeline for inclusion in a postmortem."""
    if not events:
        return "_No timeline events recorded._"

    lines = []
    for event in sorted(events, key=lambda e: e.timestamp):
        ts = event.timestamp.strftime("%H:%M:%S UTC")
        lines.append(f"- **{ts}** — {event.message}")
    return "\n".join(lines)
