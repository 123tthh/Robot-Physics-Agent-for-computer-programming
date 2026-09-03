"""Stateful physical/GUI simulator with deterministic fault injection."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from ..config import load_json
from ..control import G1ControlStack
from ..domain import Evidence, EvidenceDomain, StateSnapshot
from ..robot.g1 import G1HardwareProfile
from .sandbox import LocalCodeSandbox


@dataclass(frozen=True, slots=True)
class SimulationScenario:
    name: str
    expected_output: str = "hello, world"
    buggy_first_draft: bool = True
    faults: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    max_recovery_attempts: int = 2

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("scenario name cannot be empty")
        if self.max_recovery_attempts < 0:
            raise ValueError("max_recovery_attempts cannot be negative")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SimulationScenario":
        raw_faults = data.get("faults", {})
        if not isinstance(raw_faults, dict):
            raise ValueError("scenario faults must be an object")
        return cls(
            name=str(data["name"]),
            expected_output=str(data.get("expected_output", "hello, world")),
            buggy_first_draft=bool(data.get("buggy_first_draft", True)),
            faults={str(skill): tuple(codes) for skill, codes in raw_faults.items()},
            max_recovery_attempts=int(data.get("max_recovery_attempts", 2)),
        )

    @classmethod
    def from_json(cls, path: str | Path) -> "SimulationScenario":
        return cls.from_dict(load_json(path))


class FaultInjector:
    """Consumes configured failures once, making retries deterministic."""

    def __init__(self, schedule: Mapping[str, tuple[str, ...]] | None = None) -> None:
        self._schedule: dict[str, deque[str]] = defaultdict(deque)
        for skill, codes in (schedule or {}).items():
            self._schedule[skill].extend(codes)
        self.injected_count = 0

    def consume(self, skill: str) -> str | None:
        queue = self._schedule.get(skill)
        if not queue:
            return None
        self.injected_count += 1
        return queue.popleft()

    def pending(self) -> dict[str, list[str]]:
        return {skill: list(codes) for skill, codes in self._schedule.items() if codes}


class SimulatedWorld:
    """Ground-truth world kept separate from the Agent's verified state."""

    def __init__(
        self,
        scenario: SimulationScenario,
        *,
        workspace: str | Path | None = None,
        robot_profile: G1HardwareProfile,
    ) -> None:
        self.scenario = scenario
        self.faults = FaultInjector(scenario.faults)
        self.truth: set[str] = {"robot_safe", "task_received"}
        self.values: dict[str, Any] = {
            "expected_output": scenario.expected_output,
            "scenario": scenario.name,
            "g1_profile": robot_profile.compatibility_key,
        }
        self.control = G1ControlStack(robot_profile)
        self.sandbox = LocalCodeSandbox(workspace)
        self.action_count = 0

    def inject_for(self, skill: str) -> str | None:
        self.action_count += 1
        return self.faults.consume(skill)

    def set_states(self, *states: str) -> None:
        self.truth.update(states)

    def clear_states(self, *states: str) -> None:
        self.truth.difference_update(states)

    def has(self, *states: str) -> bool:
        return set(states).issubset(self.truth)

    def state_evidence(
        self,
        state_name: str,
        *,
        domain: EvidenceDomain,
        source: str,
        confidence: float = 1.0,
    ) -> Evidence:
        return Evidence(
            domain=domain,
            kind="state_observation",
            value={"state": state_name, "present": state_name in self.truth},
            source=source,
            confidence=confidence,
        )

    def evidence_for(
        self,
        states: tuple[str, ...] | list[str],
        *,
        domain: EvidenceDomain,
        source: str,
    ) -> tuple[Evidence, ...]:
        return tuple(
            self.state_evidence(state, domain=domain, source=source) for state in states
        )

    def synchronize_from_verified_state(self, state: StateSnapshot) -> None:
        """Restore simulator truth after a persisted task checkpoint is loaded."""

        self.truth.update(state.facts)
        self.values.update(state.values)
        self.control.synchronize(state.facts)

    def close(self) -> None:
        self.sandbox.close()

    def __enter__(self) -> "SimulatedWorld":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
