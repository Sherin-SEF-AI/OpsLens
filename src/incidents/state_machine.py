"""Incident finite state machine with valid transitions."""

import structlog

from src.incidents.models import IncidentStatus

logger = structlog.get_logger()

# Valid state transitions: current_state -> set of allowed next states
VALID_TRANSITIONS: dict[IncidentStatus, set[IncidentStatus]] = {
    IncidentStatus.TRIGGERED: {IncidentStatus.TRIAGED},
    IncidentStatus.TRIAGED: {IncidentStatus.INVESTIGATING},
    IncidentStatus.INVESTIGATING: {
        IncidentStatus.MITIGATED,
        IncidentStatus.RESOLVED,
    },
    IncidentStatus.MITIGATED: {
        IncidentStatus.RESOLVED,
        IncidentStatus.INVESTIGATING,  # regression
    },
    IncidentStatus.RESOLVED: {
        IncidentStatus.POSTMORTEM,
        IncidentStatus.INVESTIGATING,  # re-opened
    },
    IncidentStatus.POSTMORTEM: set(),  # terminal
}


class InvalidTransition(Exception):
    """Raised when an invalid state transition is attempted."""

    def __init__(self, current: IncidentStatus, target: IncidentStatus):
        self.current = current
        self.target = target
        super().__init__(
            f"Invalid transition: {current.value} -> {target.value}"
        )


def validate_transition(
    current: IncidentStatus, target: IncidentStatus
) -> bool:
    """Check if a state transition is valid."""
    allowed = VALID_TRANSITIONS.get(current, set())
    return target in allowed


def execute_transition(
    current: IncidentStatus,
    target: IncidentStatus,
    incident_id: str = "",
) -> IncidentStatus:
    """
    Validate and execute a state transition.
    Returns the new status or raises InvalidTransition.
    """
    if not validate_transition(current, target):
        logger.warning(
            "invalid_state_transition",
            incident_id=incident_id,
            current=current.value,
            target=target.value,
        )
        raise InvalidTransition(current, target)

    logger.info(
        "state_transition",
        incident_id=incident_id,
        from_status=current.value,
        to_status=target.value,
    )
    return target
