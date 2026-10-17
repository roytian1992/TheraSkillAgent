"""Lifecycle state machine for simulation sessions."""

from __future__ import annotations

from datetime import datetime, timezone

from .contracts import SessionStatus, SimulationSession


ALLOWED_TRANSITIONS: dict[SessionStatus, frozenset[SessionStatus]] = {
    SessionStatus.CREATED: frozenset(
        {SessionStatus.ACTIVE, SessionStatus.TERMINATED, SessionStatus.FAILED}
    ),
    SessionStatus.ACTIVE: frozenset(
        {
            SessionStatus.PAUSED,
            SessionStatus.COMPLETED,
            SessionStatus.TERMINATED,
            SessionStatus.FAILED,
        }
    ),
    SessionStatus.PAUSED: frozenset(
        {SessionStatus.ACTIVE, SessionStatus.TERMINATED, SessionStatus.FAILED}
    ),
    SessionStatus.COMPLETED: frozenset(),
    SessionStatus.TERMINATED: frozenset(),
    SessionStatus.FAILED: frozenset(),
}


class InvalidSessionTransition(ValueError):
    pass


def can_transition(current: SessionStatus | str, target: SessionStatus | str) -> bool:
    return SessionStatus(target) in ALLOWED_TRANSITIONS[SessionStatus(current)]


def transition_session(
    session: SimulationSession,
    target: SessionStatus | str,
) -> SimulationSession:
    target_status = SessionStatus(target)
    current_status = SessionStatus(session.status)
    if not can_transition(current_status, target_status):
        raise InvalidSessionTransition(
            f"invalid simulation session transition: {current_status.value} -> {target_status.value}"
        )
    return session.model_copy(
        update={"status": target_status, "updated_at": datetime.now(timezone.utc)}
    )
