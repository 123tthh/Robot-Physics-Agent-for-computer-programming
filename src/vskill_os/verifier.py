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

    def __init__(self) -> None:
        self._rules: dict[str, VerificationRule] = {}

    def register(self, state_name: str, rule: VerificationRule) -> None:
        if state_name in self._rules:
            raise ValueError(f"verification rule already exists: {state_name}")
        self._rules[state_name] = rule

    def verify(
        self,
        claims: Iterable[str],
        state: StateSnapshot,
        evidence: tuple[Evidence, ...],
    ) -> VerificationReport:
        verified: list[str] = []
        rejected: list[str] = []
        for claim in dict.fromkeys(claims):
            rule = self._rules.get(claim)
            accepted = rule(state, evidence) if rule else claim in state.facts
            (verified if accepted else rejected).append(claim)
        return VerificationReport(tuple(verified), tuple(rejected))


def evidence_value(evidence: tuple[Evidence, ...], kind: str) -> object | None:
    matching = [item for item in evidence if item.kind == kind]
    if not matching:
        return None
    return max(matching, key=lambda item: item.confidence).value


def build_default_verifier() -> RuleBasedVerifier:
    verifier = RuleBasedVerifier()
    verifier.register(
        "terminal_ready",
        lambda state, _: state.has_all(
            {
                "terminal_visible",
                "terminal_focused",
                "fingers_released",
                "work_pose_stable",
            }
        ),
    )
    verifier.register(
        "test_passed",
        lambda _, evidence: evidence_value(evidence, "process_exit_code") == 0,
    )
    return verifier
