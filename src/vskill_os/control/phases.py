"""Safety-owned phase transitions for whole-body computer use."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum


class ControlPhase(StrEnum):
    STAND_READY = "stand_ready"
    LOCOMOTION = "locomotion"
    STATION_ALIGNMENT = "station_alignment"
    SIT_TRANSITION = "sit_transition"
    SEATED_STABILIZATION = "seated_stabilization"
    SEATED_LOCOMOTION = "seated_locomotion"
    UPPER_BODY_OPERATION = "upper_body_operation"
    KEYBOARD_CALIBRATION = "keyboard_calibration"
    TYPING = "typing"
    RECOVERY = "recovery"
    SAFE_STOP = "safe_stop"


class TransitionError(RuntimeError):
    pass


ALLOWED_TRANSITIONS: dict[ControlPhase, frozenset[ControlPhase]] = {
    ControlPhase.STAND_READY: frozenset(
        {ControlPhase.LOCOMOTION, ControlPhase.STATION_ALIGNMENT}
    ),
    ControlPhase.LOCOMOTION: frozenset(
        {ControlPhase.STAND_READY, ControlPhase.STATION_ALIGNMENT}
    ),
    ControlPhase.STATION_ALIGNMENT: frozenset(
        {ControlPhase.LOCOMOTION, ControlPhase.SIT_TRANSITION}
    ),
    ControlPhase.SIT_TRANSITION: frozenset(
        {ControlPhase.STATION_ALIGNMENT, ControlPhase.SEATED_STABILIZATION}
    ),
    ControlPhase.SEATED_STABILIZATION: frozenset(
        {
            ControlPhase.SIT_TRANSITION,
            ControlPhase.SEATED_LOCOMOTION,
            ControlPhase.UPPER_BODY_OPERATION,
        }
    ),
    ControlPhase.SEATED_LOCOMOTION: frozenset(
        {ControlPhase.SEATED_STABILIZATION}
    ),
    ControlPhase.UPPER_BODY_OPERATION: frozenset(
        {
            ControlPhase.SEATED_STABILIZATION,
            ControlPhase.SEATED_LOCOMOTION,
            ControlPhase.KEYBOARD_CALIBRATION,
        }
    ),
    ControlPhase.KEYBOARD_CALIBRATION: frozenset(
        {ControlPhase.UPPER_BODY_OPERATION, ControlPhase.TYPING}
    ),
    ControlPhase.TYPING: frozenset(
        {ControlPhase.KEYBOARD_CALIBRATION, ControlPhase.UPPER_BODY_OPERATION}
    ),
    ControlPhase.RECOVERY: frozenset(),
    ControlPhase.SAFE_STOP: frozenset(),
}


@dataclass(frozen=True, slots=True)
class PhaseTransition:
    source: ControlPhase
    target: ControlPhase
    reason: str
    timestamp: str


class PhaseController:
    """Finite-state safety boundary below Agent/Skill orchestration."""

    def __init__(self, initial: ControlPhase = ControlPhase.STAND_READY) -> None:
        if initial in {ControlPhase.RECOVERY, ControlPhase.SAFE_STOP}:
            raise ValueError("initial phase must be an operational phase")
        self._phase = initial
        self._recovery_origin: ControlPhase | None = None
        self._history: list[PhaseTransition] = []

    @property
    def phase(self) -> ControlPhase:
        return self._phase

    @property
    def history(self) -> tuple[PhaseTransition, ...]:
        return tuple(self._history)

    def transition(self, target: ControlPhase, *, reason: str) -> PhaseTransition | None:
        if not reason.strip():
            raise ValueError("transition reason cannot be empty")
        if target == self._phase:
            return None
        if self._phase is ControlPhase.SAFE_STOP:
            raise TransitionError("SAFE_STOP is terminal until an external reset")
        if target in {ControlPhase.RECOVERY, ControlPhase.SAFE_STOP}:
            raise TransitionError("use fail() or safe_stop() for safety transitions")
        if self._phase is ControlPhase.RECOVERY:
            raise TransitionError("use recover() to leave RECOVERY")
        if target not in ALLOWED_TRANSITIONS[self._phase]:
            raise TransitionError(f"illegal phase transition {self._phase} -> {target}")
        return self._record(target, reason)

    def fail(self, *, reason: str) -> PhaseTransition | None:
        if self._phase in {ControlPhase.RECOVERY, ControlPhase.SAFE_STOP}:
            return None
        self._recovery_origin = self._phase
        return self._record(ControlPhase.RECOVERY, reason)

    def recover(
        self,
        *,
        target: ControlPhase | None = None,
        reason: str,
    ) -> PhaseTransition | None:
        if self._phase is not ControlPhase.RECOVERY:
            if target is None or target == self._phase:
                return None
            raise TransitionError("recover() is only valid from RECOVERY")
        destination = target or self._recovery_origin
        if destination is None or destination in {
            ControlPhase.RECOVERY,
            ControlPhase.SAFE_STOP,
        }:
            raise TransitionError("recovery destination must be operational")
        self._recovery_origin = None
        return self._record(destination, reason)

    def safe_stop(self, *, reason: str) -> PhaseTransition | None:
        if self._phase is ControlPhase.SAFE_STOP:
            return None
        return self._record(ControlPhase.SAFE_STOP, reason)

    def synchronize(self, verified_facts: set[str]) -> None:
        """Best-effort phase reconstruction after a persisted graph checkpoint."""

        if "keyboard_calibrated" in verified_facts:
            self._phase = ControlPhase.TYPING
        elif "work_pose_ready" in verified_facts:
            self._phase = ControlPhase.UPPER_BODY_OPERATION
        elif "seated_stable" in verified_facts:
            self._phase = ControlPhase.SEATED_STABILIZATION
        elif "chair_aligned" in verified_facts:
            self._phase = ControlPhase.STATION_ALIGNMENT
        elif "workstation_reached" in verified_facts:
            self._phase = ControlPhase.STATION_ALIGNMENT
        else:
            self._phase = ControlPhase.STAND_READY
        self._recovery_origin = None

    def _record(self, target: ControlPhase, reason: str) -> PhaseTransition:
        transition = PhaseTransition(
            source=self._phase,
            target=target,
            reason=reason,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        self._phase = target
        self._history.append(transition)
        return transition
