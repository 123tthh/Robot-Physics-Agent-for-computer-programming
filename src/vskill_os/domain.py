"""Shared domain models for skills, evidence, state, and execution results."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping


class EvidenceDomain(StrEnum):
    PHYSICAL = "physical"
    GUI = "gui"
    SOFTWARE = "software"
    SYSTEM = "system"


class SkillStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REJECTED = "rejected"
    CANCELED = "canceled"


@dataclass(frozen=True, slots=True)
class Evidence:
    domain: EvidenceDomain
    kind: str
    value: Any
    source: str
    confidence: float = 1.0

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("evidence confidence must be within [0, 1]")

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain.value,
            "kind": self.kind,
            "value": self.value,
            "source": self.source,
            "confidence": self.confidence,
        }


@dataclass(slots=True)
class StateSnapshot:
    """Current verified state plus non-state values used by skills/verifiers."""

    facts: set[str] = field(default_factory=set)
    values: dict[str, Any] = field(default_factory=dict)

    def has_all(self, required: tuple[str, ...] | list[str] | set[str]) -> bool:
        return set(required).issubset(self.facts)

    def missing(self, required: tuple[str, ...] | list[str] | set[str]) -> set[str]:
        return set(required) - self.facts


@dataclass(frozen=True, slots=True)
class SkillRequest:
    task_id: str
    skill: str
    arguments: Mapping[str, Any] = field(default_factory=dict)
    required_postconditions: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SkillExecution:
    """Raw output from a controller before independent verification."""

    status: SkillStatus
    claimed_states: tuple[str, ...] = ()
    evidence: tuple[Evidence, ...] = ()
    failure_code: str | None = None
    message: str = ""


@dataclass(frozen=True, slots=True)
class SkillResult:
    call_id: str
    task_id: str
    skill: str
    status: SkillStatus
    achieved: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    failure_code: str | None = None
    recommended_recovery: str | None = None
    evidence: tuple[Evidence, ...] = ()
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "call_id": self.call_id,
            "task_id": self.task_id,
            "skill": self.skill,
            "status": self.status.value,
            "achieved": list(self.achieved),
            "missing": list(self.missing),
            "failure_code": self.failure_code,
            "recommended_recovery": self.recommended_recovery,
            "evidence": [item.to_dict() for item in self.evidence],
            "message": self.message,
        }
