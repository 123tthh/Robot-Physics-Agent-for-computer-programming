"""Independent evidence-grounded state verification."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Callable, Iterable

from .domain import Evidence, StateSnapshot


VerificationRule = Callable[[StateSnapshot, tuple[Evidence, ...]], bool]


@dataclass(frozen=True, slots=True)
class VerificationReport:
    verified: tuple[str, ...]
    rejected: tuple[str, ...]


class RuleBasedVerifier:
    """Small deterministic verifier; learned verifiers can keep this interface."""

    def __init__(self, *, trusted_sources: set[str] | None = None) -> None:
        self._rules: dict[str, VerificationRule] = {}
        self._trusted_sources = set(trusted_sources or ())

    def register(self, state_name: str, rule: VerificationRule) -> None:
        if state_name in self._rules:
            raise ValueError(f"verification rule already exists: {state_name}")
        self._rules[state_name] = rule

    def verify(
        self,
        claims: Iterable[str],
        state: StateSnapshot,
        evidence: tuple[Evidence, ...],
        *,
        known_states: set[str] | None = None,
    ) -> VerificationReport:
        ordered_claims = tuple(dict.fromkeys(claims))
        accepted = set() if known_states is None else set(known_states)
        decisions: dict[str, bool] = {}

        # Atomic observations are checked first. Derived rules may only consume
        # previously verified facts or atomic claims backed by trusted evidence.
        for claim in ordered_claims:
            if claim in self._rules:
                continue
            is_verified = claim in accepted or self._has_trusted_state_evidence(
                claim, evidence
            )
            decisions[claim] = is_verified
            if is_verified:
                accepted.add(claim)

        pending = [claim for claim in ordered_claims if claim in self._rules]
        while pending:
            progress = False
            for claim in tuple(pending):
                working_state = StateSnapshot(
                    facts=set(accepted),
                    values=dict(state.values),
                    revision=state.revision,
                )
                is_verified = claim in accepted or self._rules[claim](
                    working_state, evidence
                )
                if is_verified:
                    decisions[claim] = True
                    accepted.add(claim)
                    pending.remove(claim)
                    progress = True
            if not progress:
                break

        for claim in pending:
            decisions[claim] = False

        verified = tuple(claim for claim in ordered_claims if decisions[claim])
        rejected = tuple(claim for claim in ordered_claims if not decisions[claim])
        return VerificationReport(verified, rejected)

    def _has_trusted_state_evidence(
        self,
        claim: str,
        evidence: tuple[Evidence, ...],
    ) -> bool:
        for item in evidence:
            if item.source not in self._trusted_sources or item.kind != "state_observation":
                continue
            if isinstance(item.value, str) and item.value == claim:
                return True
            if isinstance(item.value, dict):
                if item.value.get("state") == claim and item.value.get("present") is True:
                    return True
        return False


def evidence_value(evidence: tuple[Evidence, ...], kind: str) -> object | None:
    matching = [item for item in evidence if item.kind == kind]
    if not matching:
        return None
    return max(matching, key=lambda item: item.confidence).value


def seated_locomotion_is_verified(
    state: StateSnapshot,
    evidence: tuple[Evidence, ...],
) -> bool:
    """Verify progress, tracking, contact, upright posture, and final settling."""

    if not state.has_all({"seated_stable", "work_pose_ready", "robot_safe"}):
        return False
    matching = [
        item
        for item in evidence
        if item.kind == "seated_locomotion_metrics"
        and item.source == "sim_robot"
        and isinstance(item.value, dict)
    ]
    if not matching:
        return False
    value = max(matching, key=lambda item: item.confidence).value
    try:
        issued = value["issued_command"]
        tracking = value["tracking_rmse"]
        duration = _finite_float(value["duration_s"])
        vx = _finite_float(issued["vx_mps"])
        vy = _finite_float(issued["vy_mps"])
        yaw_rate = _finite_float(issued["yaw_rate_rps"])
        if abs(vx) > 0.5 or abs(vy) > 0.5 or abs(yaw_rate) > 0.5:
            return False
        planar_demand = math.hypot(vx, vy) * duration
        yaw_demand = abs(yaw_rate) * duration
        if duration <= 0.0 or (planar_demand < 0.02 and yaw_demand < 0.02):
            return False
        planar_progress = _finite_float(value["path_length_m"])
        yaw_progress = _finite_float(value["absolute_yaw_rad"])
        if planar_demand >= 0.02 and planar_progress < 0.5 * planar_demand:
            return False
        if yaw_demand >= 0.02 and yaw_progress < 0.5 * yaw_demand:
            return False
        left_contact = _finite_float(value["left_foot_contact_fraction"])
        right_contact = _finite_float(value["right_foot_contact_fraction"])
        if not (0.0 <= left_contact <= 1.0 and 0.0 <= right_contact <= 1.0):
            return False
        return (
            _finite_float(tracking["vx_mps"]) <= 0.20
            and _finite_float(tracking["vy_mps"]) <= 0.20
            and _finite_float(tracking["yaw_rate_rps"]) <= 0.20
            and _finite_float(value["pelvis_seat_rms_m"]) <= 0.05
            and _finite_float(value["minimum_seat_contact_confidence"]) >= 0.85
            and _finite_float(value["maximum_contact_loss_s"]) <= 0.25
            and _finite_float(value["maximum_torso_tilt_rad"]) <= 0.20
            and _finite_float(value["maximum_chair_tilt_rad"]) <= 0.20
            and left_contact + right_contact >= 0.1
            and value["settled"] is True
        )
    except (KeyError, TypeError, ValueError):
        return False


def _finite_float(value: object) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("verification metric must be finite")
    return result


def build_default_verifier() -> RuleBasedVerifier:
    verifier = RuleBasedVerifier(
        trusted_sources={
            "sim_robot",
            "sim_keyboard",
            "sim_screen",
            "sim_code_agent",
            "sim_test_runner",
            "mock_keyboard",
            "mock_screen_verifier",
            "mock_code_agent",
        }
    )
    verifier.register(
        "terminal_ready",
        lambda state, _: state.has_all(
            {"terminal_visible", "terminal_focused", "fingers_released"}
        )
        and (
            "work_pose_ready" in state.facts
            or "work_pose_stable" in state.facts
        ),
    )
    verifier.register(
        "test_passed",
        lambda _, evidence: evidence_value(evidence, "process_exit_code") == 0,
    )
    verifier.register("seated_reposition_verified", seated_locomotion_is_verified)
    return verifier
