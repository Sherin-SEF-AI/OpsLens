"""Tests for agent confidence score parsing and tracking."""

import pytest

from src.agents.confidence import (
    extract_all_confidences,
    format_confidence_summary,
    parse_confidence,
)


# ---------------------------------------------------------------------------
# parse_confidence
# ---------------------------------------------------------------------------

class TestParseConfidence:
    def test_high_confidence(self):
        text = "Some analysis.\n**Confidence:** 85% - Strong correlation with known pattern"
        result = parse_confidence(text)
        assert result["score"] == 85
        assert result["low_confidence"] is False
        assert "Strong correlation" in result["reason"]

    def test_low_confidence(self):
        text = "Analysis incomplete.\n**Confidence:** 30% - Insufficient data available"
        result = parse_confidence(text)
        assert result["score"] == 30
        assert result["low_confidence"] is True
        assert "Insufficient" in result["reason"]

    def test_boundary_50_is_not_low(self):
        text = "**Confidence:** 50% - Borderline match"
        result = parse_confidence(text)
        assert result["score"] == 50
        assert result["low_confidence"] is False

    def test_49_is_low(self):
        text = "**Confidence:** 49% - Almost certain"
        result = parse_confidence(text)
        assert result["score"] == 49
        assert result["low_confidence"] is True

    def test_100_percent(self):
        text = "**Confidence:** 100% - Exact match"
        result = parse_confidence(text)
        assert result["score"] == 100

    def test_over_100_capped(self):
        text = "**Confidence:** 150% - Very sure"
        result = parse_confidence(text)
        assert result["score"] == 100

    def test_zero_percent(self):
        text = "**Confidence:** 0% - No idea"
        result = parse_confidence(text)
        assert result["score"] == 0
        assert result["low_confidence"] is True

    def test_no_confidence_line(self):
        text = "Just some analysis with no confidence line."
        result = parse_confidence(text)
        assert result["score"] is None
        assert result["low_confidence"] is True
        assert "No confidence line" in result["reason"]

    def test_empty_text(self):
        result = parse_confidence("")
        assert result["score"] is None
        assert result["low_confidence"] is True

    def test_case_insensitive(self):
        text = "**confidence:** 75% - Good match"
        result = parse_confidence(text)
        assert result["score"] == 75

    def test_em_dash_separator(self):
        text = "**Confidence:** 60% \u2014 Moderate confidence"
        result = parse_confidence(text)
        assert result["score"] == 60
        assert "Moderate" in result["reason"]


# ---------------------------------------------------------------------------
# extract_all_confidences
# ---------------------------------------------------------------------------

class TestExtractAllConfidences:
    def test_multiple_agents(self):
        agent_results = {
            "triage": {"analysis": "Done.\n**Confidence:** 85% - High certainty"},
            "correlation": {"analysis": "Found links.\n**Confidence:** 40% - Weak signal"},
            "remediation": {"analysis": ""},
        }
        confidences = extract_all_confidences(agent_results)
        assert confidences["triage"]["score"] == 85
        assert confidences["triage"]["low_confidence"] is False
        assert confidences["correlation"]["score"] == 40
        assert confidences["correlation"]["low_confidence"] is True
        assert confidences["remediation"]["score"] is None
        assert confidences["remediation"]["low_confidence"] is True

    def test_empty_results(self):
        confidences = extract_all_confidences({})
        assert confidences == {}

    def test_missing_analysis_key(self):
        agent_results = {"triage": {"status": "done"}}
        confidences = extract_all_confidences(agent_results)
        assert confidences["triage"]["score"] is None
        assert "No analysis output" in confidences["triage"]["reason"]


# ---------------------------------------------------------------------------
# format_confidence_summary
# ---------------------------------------------------------------------------

class TestFormatConfidenceSummary:
    def test_all_high_confidence(self):
        confidences = {
            "triage": {"score": 90, "reason": "Strong", "low_confidence": False},
            "correlation": {"score": 75, "reason": "Good", "low_confidence": False},
        }
        summary = format_confidence_summary(confidences)
        assert "Agent Confidence Summary" in summary
        assert "90%" in summary
        assert "75%" in summary
        assert "WARNING" not in summary

    def test_low_confidence_warning(self):
        confidences = {
            "triage": {"score": 90, "reason": "Good", "low_confidence": False},
            "correlation": {"score": 20, "reason": "Weak", "low_confidence": True},
        }
        summary = format_confidence_summary(confidences)
        assert "WARNING" in summary
        assert "LOW" in summary

    def test_none_score(self):
        confidences = {
            "triage": {"score": None, "reason": "No output", "low_confidence": True},
        }
        summary = format_confidence_summary(confidences)
        assert "N/A" in summary
        assert "WARNING" in summary

    def test_agent_names_are_title_cased(self):
        confidences = {
            "triage": {"score": 80, "reason": "OK", "low_confidence": False},
        }
        summary = format_confidence_summary(confidences)
        assert "**Triage:**" in summary
