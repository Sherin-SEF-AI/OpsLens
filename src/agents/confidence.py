"""Confidence score parser and tracker for agent outputs."""

import re
from typing import Any

import structlog

logger = structlog.get_logger()

# Regex to extract **Confidence:** XX% from agent text
_CONFIDENCE_RE = re.compile(
    r"\*\*Confidence:\*\*\s*(\d{1,3})%\s*[-—]\s*(.+)",
    re.IGNORECASE,
)


def parse_confidence(text: str) -> dict[str, Any]:
    """Extract confidence score and reason from agent output text.

    Returns dict with keys: score (int|None), reason (str), low_confidence (bool).
    """
    match = _CONFIDENCE_RE.search(text)
    if not match:
        return {"score": None, "reason": "No confidence line found", "low_confidence": True}

    score = int(match.group(1))
    reason = match.group(2).strip()
    return {
        "score": min(score, 100),
        "reason": reason,
        "low_confidence": score < 50,
    }


def extract_all_confidences(agent_results: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Extract confidence from multiple agent results.

    Args:
        agent_results: {"triage": {"analysis": "..."}, "correlation": {...}, ...}

    Returns:
        {"triage": {"score": 85, "reason": "...", "low_confidence": False}, ...}
    """
    confidences = {}
    for agent_name, result in agent_results.items():
        analysis_text = result.get("analysis", "")
        if analysis_text:
            confidences[agent_name] = parse_confidence(analysis_text)
        else:
            confidences[agent_name] = {
                "score": None,
                "reason": "No analysis output",
                "low_confidence": True,
            }
    return confidences


def format_confidence_summary(confidences: dict[str, dict[str, Any]]) -> str:
    """Format confidence scores into a summary comment for the incident page."""
    lines = ["## Agent Confidence Summary\n"]
    any_low = False

    for agent_name, conf in confidences.items():
        score = conf.get("score")
        reason = conf.get("reason", "")
        low = conf.get("low_confidence", False)

        if score is not None:
            indicator = "LOW" if low else "OK"
            if low:
                any_low = True
            lines.append(
                f"- **{agent_name.title()}:** {score}% ({indicator}) - {reason}"
            )
        else:
            any_low = True
            lines.append(f"- **{agent_name.title()}:** N/A - {reason}")

    if any_low:
        lines.insert(1, "**WARNING: One or more agents reported low confidence. Manual review recommended.**\n")

    return "\n".join(lines)
