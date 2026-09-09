"""Shared domain models for skills, evidence, state, and execution results."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import math
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
class FactValidity:
    """Lifetime and dependency metadata for one independently verified fact."""

    verified_at: float
    ttl_seconds: float | None = None
    dependencies: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not math.isfinite(self.verified_at):
            raise ValueError("fact verification time must be finite")
        if self.ttl_seconds is not None and (
            not math.isfinite(self.ttl_seconds) or self.ttl_seconds <= 0.0
        ):
            raise ValueError("fact ttl_seconds must be finite and positive")
        if len(self.dependencies) != len(set(self.dependencies)):
            raise ValueError("fact dependencies contain duplicate entries")

    def is_expired(self, now: float) -> bool:
        return (
            self.ttl_seconds is not None
            and now >= self.verified_at + self.ttl_seconds
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "verified_at": self.verified_at,
            "ttl_seconds": self.ttl_seconds,
            "dependencies": list(self.dependencies),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "FactValidity":
        ttl = data.get("ttl_seconds")
        return cls(
            verified_at=float(data["verified_at"]),
            ttl_seconds=None if ttl is None else float(ttl),
            dependencies=tuple(str(item) for item in data.get("dependencies", ())),
        )


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
    revision: int = 0
    validity: dict[str, FactValidity] = field(default_factory=dict)

    def has_all(self, required: tuple[str, ...] | list[str] | set[str]) -> bool:
        return set(required).issubset(self.facts)

    def missing(self, required: tuple[str, ...] | list[str] | set[str]) -> set[str]:
        return set(required) - self.facts

    def copy(self) -> "StateSnapshot":
        return StateSnapshot(
            set(self.facts),
            dict(self.values),
            self.revision,
            dict(self.validity),
        )

    def mark_verified(
        self,
        fact: str,
        *,
        verified_at: float,
        ttl_seconds: float | None = None,
        dependencies: tuple[str, ...] = (),
    ) -> None:
        """Record a verified fact without allowing a controller to self-verify it."""

        self.facts.add(fact)
        self.validity[fact] = FactValidity(
            verified_at=verified_at,
            ttl_seconds=ttl_seconds,
            dependencies=dependencies,
        )

    def invalidate(self, facts: set[str] | tuple[str, ...] | list[str]) -> tuple[str, ...]:
        """Remove facts and every tracked fact that depends on them."""

        triggers = set(facts)
        removed: set[str] = triggers & self.facts
        changed = True
        while changed:
            changed = False
            for fact, metadata in tuple(self.validity.items()):
                if fact in removed:
                    continue
                if any(
                    dependency in triggers or dependency in removed
                    for dependency in metadata.dependencies
                ):
                    removed.add(fact)
                    changed = True
        self.facts.difference_update(removed)
        for fact in removed:
            self.validity.pop(fact, None)
        return tuple(sorted(removed))

    def expire(self, now: float) -> tuple[str, ...]:
        """Expire stale facts and propagate invalidation through dependencies."""

        if not math.isfinite(now):
            raise ValueError("state clock value must be finite")
        stale = {
            fact
            for fact, metadata in self.validity.items()
            if metadata.is_expired(now)
            or any(dependency not in self.facts for dependency in metadata.dependencies)
        }
        return self.invalidate(stale)

    def to_dict(self) -> dict[str, Any]:
        return {
            "facts": sorted(self.facts),
            "values": dict(self.values),
            "revision": self.revision,
            "validity": {
                fact: metadata.to_dict()
                for fact, metadata in sorted(self.validity.items())
                if fact in self.facts
            },
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "StateSnapshot":
        return cls(
            facts=set(data.get("facts", ())),
            values=dict(data.get("values", {})),
            revision=int(data.get("revision", 0)),
            validity={
                str(fact): FactValidity.from_dict(metadata)
                for fact, metadata in dict(data.get("validity", {})).items()
            },
        )


@dataclass(frozen=True, slots=True)
class SkillRequest:
    task_id: str
    skill: str
    arguments: Mapping[str, Any] = field(default_factory=dict)
    required_postconditions: tuple[str, ...] = ()
    timeout_seconds: float = 30.0
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0:
            raise ValueError("skill timeout_seconds must be positive")


@dataclass(frozen=True, slots=True)
class SkillExecution:
    """Raw output from a controller before independent verification."""

    status: SkillStatus
    claimed_states: tuple[str, ...] = ()
    invalidated_states: tuple[str, ...] = ()
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
    duration_seconds: float = 0.0

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
            "duration_seconds": self.duration_seconds,
        }
