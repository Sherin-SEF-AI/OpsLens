"""Tests for the incident finite state machine."""

import pytest

from src.incidents.models import IncidentStatus
from src.incidents.state_machine import (
    InvalidTransition,
    VALID_TRANSITIONS,
    execute_transition,
    validate_transition,
)


# ---------------------------------------------------------------------------
# Valid transitions
# ---------------------------------------------------------------------------

class TestValidTransitions:
    """Test that all expected valid transitions are allowed."""

    @pytest.mark.parametrize(
        "current, target",
        [
            (IncidentStatus.TRIGGERED, IncidentStatus.TRIAGED),
            (IncidentStatus.TRIAGED, IncidentStatus.INVESTIGATING),
            (IncidentStatus.INVESTIGATING, IncidentStatus.MITIGATED),
            (IncidentStatus.INVESTIGATING, IncidentStatus.RESOLVED),
            (IncidentStatus.MITIGATED, IncidentStatus.RESOLVED),
            (IncidentStatus.MITIGATED, IncidentStatus.INVESTIGATING),  # regression
            (IncidentStatus.RESOLVED, IncidentStatus.POSTMORTEM),
            (IncidentStatus.RESOLVED, IncidentStatus.INVESTIGATING),  # re-opened
        ],
    )
    def test_valid_transition_succeeds(self, current, target):
        assert validate_transition(current, target) is True

    @pytest.mark.parametrize(
        "current, target",
        [
            (IncidentStatus.TRIGGERED, IncidentStatus.TRIAGED),
            (IncidentStatus.TRIAGED, IncidentStatus.INVESTIGATING),
            (IncidentStatus.INVESTIGATING, IncidentStatus.MITIGATED),
            (IncidentStatus.MITIGATED, IncidentStatus.RESOLVED),
            (IncidentStatus.RESOLVED, IncidentStatus.POSTMORTEM),
        ],
    )
    def test_execute_transition_returns_target(self, current, target):
        result = execute_transition(current, target, incident_id="OPSLENS-0001")
        assert result == target


# ---------------------------------------------------------------------------
# Invalid transitions
# ---------------------------------------------------------------------------

class TestInvalidTransitions:
    """Test that invalid transitions are rejected."""

    @pytest.mark.parametrize(
        "current, target",
        [
            # Skip states
            (IncidentStatus.TRIGGERED, IncidentStatus.INVESTIGATING),
            (IncidentStatus.TRIGGERED, IncidentStatus.MITIGATED),
            (IncidentStatus.TRIGGERED, IncidentStatus.RESOLVED),
            (IncidentStatus.TRIGGERED, IncidentStatus.POSTMORTEM),
            # Backwards (non-regression)
            (IncidentStatus.TRIAGED, IncidentStatus.TRIGGERED),
            (IncidentStatus.INVESTIGATING, IncidentStatus.TRIGGERED),
            (IncidentStatus.INVESTIGATING, IncidentStatus.TRIAGED),
            (IncidentStatus.MITIGATED, IncidentStatus.TRIGGERED),
            (IncidentStatus.MITIGATED, IncidentStatus.TRIAGED),
            # Terminal state
            (IncidentStatus.POSTMORTEM, IncidentStatus.TRIGGERED),
            (IncidentStatus.POSTMORTEM, IncidentStatus.TRIAGED),
            (IncidentStatus.POSTMORTEM, IncidentStatus.INVESTIGATING),
            (IncidentStatus.POSTMORTEM, IncidentStatus.MITIGATED),
            (IncidentStatus.POSTMORTEM, IncidentStatus.RESOLVED),
            # Self-transition
            (IncidentStatus.TRIGGERED, IncidentStatus.TRIGGERED),
            (IncidentStatus.RESOLVED, IncidentStatus.RESOLVED),
        ],
    )
    def test_invalid_transition_rejected(self, current, target):
        assert validate_transition(current, target) is False

    @pytest.mark.parametrize(
        "current, target",
        [
            (IncidentStatus.TRIGGERED, IncidentStatus.RESOLVED),
            (IncidentStatus.POSTMORTEM, IncidentStatus.TRIGGERED),
        ],
    )
    def test_execute_transition_raises_invalid(self, current, target):
        with pytest.raises(InvalidTransition) as exc_info:
            execute_transition(current, target, incident_id="OPSLENS-9999")
        assert exc_info.value.current == current
        assert exc_info.value.target == target
        assert current.value in str(exc_info.value)
        assert target.value in str(exc_info.value)


# ---------------------------------------------------------------------------
# get_valid_transitions (via VALID_TRANSITIONS dict)
# ---------------------------------------------------------------------------

class TestGetValidTransitions:
    """Test the VALID_TRANSITIONS lookup for every state."""

    def test_triggered_goes_to_triaged_only(self):
        assert VALID_TRANSITIONS[IncidentStatus.TRIGGERED] == {IncidentStatus.TRIAGED}

    def test_triaged_goes_to_investigating_only(self):
        assert VALID_TRANSITIONS[IncidentStatus.TRIAGED] == {IncidentStatus.INVESTIGATING}

    def test_investigating_goes_to_mitigated_or_resolved(self):
        expected = {IncidentStatus.MITIGATED, IncidentStatus.RESOLVED}
        assert VALID_TRANSITIONS[IncidentStatus.INVESTIGATING] == expected

    def test_mitigated_goes_to_resolved_or_investigating(self):
        expected = {IncidentStatus.RESOLVED, IncidentStatus.INVESTIGATING}
        assert VALID_TRANSITIONS[IncidentStatus.MITIGATED] == expected

    def test_resolved_goes_to_postmortem_or_investigating(self):
        expected = {IncidentStatus.POSTMORTEM, IncidentStatus.INVESTIGATING}
        assert VALID_TRANSITIONS[IncidentStatus.RESOLVED] == expected

    def test_postmortem_is_terminal(self):
        assert VALID_TRANSITIONS[IncidentStatus.POSTMORTEM] == set()

    def test_all_states_have_transition_entry(self):
        for status in IncidentStatus:
            assert status in VALID_TRANSITIONS


# ---------------------------------------------------------------------------
# Full lifecycle
# ---------------------------------------------------------------------------

class TestFullLifecycle:
    """Test the happy-path lifecycle from Triggered to Postmortem."""

    def test_happy_path(self):
        state = IncidentStatus.TRIGGERED
        incident_id = "OPSLENS-LIFECYCLE"

        state = execute_transition(state, IncidentStatus.TRIAGED, incident_id)
        assert state == IncidentStatus.TRIAGED

        state = execute_transition(state, IncidentStatus.INVESTIGATING, incident_id)
        assert state == IncidentStatus.INVESTIGATING

        state = execute_transition(state, IncidentStatus.MITIGATED, incident_id)
        assert state == IncidentStatus.MITIGATED

        state = execute_transition(state, IncidentStatus.RESOLVED, incident_id)
        assert state == IncidentStatus.RESOLVED

        state = execute_transition(state, IncidentStatus.POSTMORTEM, incident_id)
        assert state == IncidentStatus.POSTMORTEM

    def test_regression_path(self):
        """Mitigated can regress to Investigating."""
        state = IncidentStatus.TRIGGERED
        state = execute_transition(state, IncidentStatus.TRIAGED)
        state = execute_transition(state, IncidentStatus.INVESTIGATING)
        state = execute_transition(state, IncidentStatus.MITIGATED)
        # Regression
        state = execute_transition(state, IncidentStatus.INVESTIGATING)
        assert state == IncidentStatus.INVESTIGATING
        # Resume forward
        state = execute_transition(state, IncidentStatus.RESOLVED)
        assert state == IncidentStatus.RESOLVED

    def test_reopen_path(self):
        """Resolved can be re-opened to Investigating."""
        state = IncidentStatus.TRIGGERED
        state = execute_transition(state, IncidentStatus.TRIAGED)
        state = execute_transition(state, IncidentStatus.INVESTIGATING)
        state = execute_transition(state, IncidentStatus.RESOLVED)
        # Re-open
        state = execute_transition(state, IncidentStatus.INVESTIGATING)
        assert state == IncidentStatus.INVESTIGATING
