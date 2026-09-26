"""Low-level reset/step contracts and independent joint-target limits.

These APIs belong to a policy adapter, never to Agent/SkillGraph actions. No
simulator, model library, text oracle or verifier result enters an observation.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
import math
from typing import Literal, Protocol

from .contracts import JointRecord, PhysicalKeyRecord, _nonempty, _unique
from .serialization import JsonRecord


@dataclass(frozen=True, slots=True)
class ResetSpec(JsonRecord):
    episode_id: str
    seed: int = 7
    target_key: str = "a"
    maximum_time_s: float = 3.0

    def __post_init__(self) -> None:
        JsonRecord.__post_init__(self)
        _nonempty(self.episode_id, self.target_key)
        if re.fullmatch(r"[A-Za-z0-9_-]{1,128}", self.episode_id) is None:
            raise ValueError("episode_id must be a bounded filename-safe identifier")
        if not 0 <= self.seed < 2**63 or self.maximum_time_s <= 0:
            raise ValueError("seed must be a nonnegative 63-bit integer and episode horizon positive")


@dataclass(frozen=True, slots=True)
class JointTargetAction(JsonRecord):
    joint_names: tuple[str, ...]
    delta_rad: tuple[float, ...]
    duration_s: float
    valid_until_s: float
    owner_token: str
    sequence: int

    def __post_init__(self) -> None:
        JsonRecord.__post_init__(self)
        _unique(self.joint_names, "action joints")
        _nonempty(self.owner_token)
        if len(self.delta_rad) != len(self.joint_names) or self.duration_s <= 0 or self.sequence < 0:
            raise ValueError("action must have one finite delta per named joint and positive duration")


@dataclass(frozen=True, slots=True)
class JointControlSpec(JsonRecord):
    joint_names: tuple[str, ...]
    lower_rad: tuple[float, ...]
    upper_rad: tuple[float, ...]
    maximum_speed_rad_s: tuple[float, ...]
    enabled_joints: tuple[str, ...]
    control_dt_s: float

    def __post_init__(self) -> None:
        JsonRecord.__post_init__(self)
        _unique(self.joint_names, "control joints")
        _unique(self.enabled_joints, "enabled joints")
        if (any(len(values) != len(self.joint_names) for values in
                (self.lower_rad, self.upper_rad, self.maximum_speed_rad_s))
                or not set(self.enabled_joints) <= set(self.joint_names)
                or self.control_dt_s <= 0):
            raise ValueError("invalid joint map or control period")
        if any(low >= high or speed <= 0 for low, high, speed in zip(
            self.lower_rad, self.upper_rad, self.maximum_speed_rad_s, strict=True
        )):
            raise ValueError("joint limits must be ordered, with positive speed limits")


class ActionRejected(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class JointTargetGuard:
    """Independent of the policy; mask, rate and position limits always apply."""

    def __init__(self, spec: JointControlSpec) -> None:
        self.spec = spec

    def apply(self, targets: tuple[float, ...], action: JointTargetAction, *,
              time_s: float, owner_token: str, expected_sequence: int) -> tuple[float, ...]:
        spec = self.spec
        if action.owner_token != owner_token:
            raise ActionRejected("CONTROL_OWNERSHIP")
        if action.joint_names != spec.joint_names or len(targets) != len(spec.joint_names):
            raise ActionRejected("JOINT_MAPPING")
        if (not math.isfinite(time_s) or time_s < 0 or any(not math.isfinite(value) for value in targets)
                or any(value < low - 1e-6 or value > high + 1e-6 for value, low, high in
                       zip(targets, spec.lower_rad, spec.upper_rad, strict=True))):
            raise ActionRejected("JOINT_STATE")
        if action.sequence != expected_sequence:
            raise ActionRejected("ACTION_SEQUENCE")
        if (abs(action.duration_s - spec.control_dt_s) > 1e-9
                or action.valid_until_s + 1e-9 < time_s + spec.control_dt_s):
            raise ActionRejected("ACTION_EXPIRED")
        result = []
        for name, target, delta, low, high, speed in zip(
            spec.joint_names, targets, action.delta_rad, spec.lower_rad, spec.upper_rad,
            spec.maximum_speed_rad_s, strict=True,
        ):
            if name not in spec.enabled_joints and delta != 0:
                raise ActionRejected("UNOWNED_JOINT")
            bounded_delta = max(-speed * spec.control_dt_s, min(speed * spec.control_dt_s, delta))
            result.append(max(low, min(high, target + bounded_delta)))
        return tuple(result)


@dataclass(frozen=True, slots=True)
class PolicyObservation(JsonRecord):
    """Declared privileged state teacher inputs, not a visual policy claim."""

    env_id: str
    episode_id: str
    time_s: float
    target_key: str
    joints: tuple[JointRecord, ...]
    keys: tuple[PhysicalKeyRecord, ...]
    previous_delta_rad: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class EpisodeMetadata(JsonRecord):
    env_id: str
    episode_id: str
    seed: int
    owner_token: str
    control_spec: JointControlSpec
    source: str
    support_mode: str


@dataclass(frozen=True, slots=True)
class StepResult(JsonRecord):
    observation: PolicyObservation
    reward_terms: tuple[float, ...]
    terminated: bool
    truncated: bool
    failure_codes: tuple[str, ...]
    record_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CleanupRecord(JsonRecord):
    env_id: str
    episode_id: str
    administrative_event_ids: tuple[str, ...]
    physical_release_claimed: Literal[False] = False


class TypingEnvironment(Protocol):
    def reset(self, spec: ResetSpec) -> tuple[PolicyObservation, EpisodeMetadata]: ...
    def step(self, action: JointTargetAction) -> StepResult: ...
    def finish(self) -> None: ...
    def close(self) -> CleanupRecord: ...
