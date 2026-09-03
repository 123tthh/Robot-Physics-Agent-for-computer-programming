"""Independent evidence-grounded state verification."""

from __future__ import annotations

from dataclasses import dataclass
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
    return verifier
