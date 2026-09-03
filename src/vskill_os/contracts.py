"""Versioned Skill contracts and execution protocol."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from .domain import SkillExecution, StateSnapshot


@dataclass(frozen=True, slots=True)
class SkillContract:
    name: str
    version: str
    preconditions: tuple[str, ...] = ()
    local_postconditions: tuple[str, ...] = ()
    next_skill_readiness: tuple[str, ...] = ()
    resources: tuple[str, ...] = ()
    failure_codes: tuple[str, ...] = ()
    recoveries: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("skill name cannot be empty")
        if not self.version.strip():
            raise ValueError("skill version cannot be empty")
        for field_name in (
            "preconditions",
            "local_postconditions",
            "next_skill_readiness",
            "resources",
            "failure_codes",
            "recoveries",
        ):
            values = getattr(self, field_name)
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} contains duplicate entries")

    @property
    def expected_states(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(self.local_postconditions + self.next_skill_readiness))


class Skill(Protocol):
    contract: SkillContract

    async def execute(
        self,
        state: StateSnapshot,
        arguments: Mapping[str, Any],
    ) -> SkillExecution:
        """Execute one finite-horizon closed-loop skill."""
