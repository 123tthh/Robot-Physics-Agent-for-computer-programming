"""Raw physical samples and independent per-substep safety checks."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Literal

from .contracts import RecordStamp
from .environment import JointControlSpec, JointTargetAction
from .serialization import JsonRecord


@dataclass(frozen=True, slots=True)
class CoordinateLimit(JsonRecord):
    name: str
    unit: Literal["rad", "m"]
    lower: float
    upper: float
    maximum_speed: float

    def __post_init__(self) -> None:
        JsonRecord.__post_init__(self)
        if not self.name or self.lower >= self.upper or self.maximum_speed <= 0:
            raise ValueError("invalid physical coordinate limits")


@dataclass(frozen=True, slots=True)
class PhysicsSample(JsonRecord):
    stamp: RecordStamp
    joint_positions: tuple[float, ...]
    joint_velocities: tuple[float, ...]
    joint_targets: tuple[float, ...]
    key_joint_positions_m: tuple[float, ...]
    key_joint_targets_m: tuple[float, ...]
    key_force_vectors_n: tuple[float, ...]
    key_body_positions_xyz_m: tuple[float, ...]
    fingertip_xyz_m: tuple[float, ...]
    pelvis_xyz_m: tuple[float, ...]
    torso_quaternion_wxyz: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class AppliedAction(JsonRecord):
    time_s: float
    action: JointTargetAction
    previous_targets_rad: tuple[float, ...]
    applied_targets_rad: tuple[float, ...]
    failure_code: str | None


@dataclass(frozen=True, slots=True)
class PhysicsTrace(JsonRecord):
    schema_version: Literal["vf-physical/1"]
    source: Literal["isaac_physx_single_hand_v1"]
    physics_dt_s: float
    control_spec: JointControlSpec
    owner_token: str
    coordinate_limits: tuple[CoordinateLimit, ...]
    key_names: tuple[str, ...]
    initial_joint_positions: tuple[float, ...]
    support_origin_xyz_m: tuple[float, ...]
    maximum_force_n: float
    support_tolerance_m: float
    samples: tuple[PhysicsSample, ...]
    actions: tuple[AppliedAction, ...]
    failure_codes: tuple[str, ...]
    episode_sha256: str


class PhysicsSafetyMonitor:
    """Always active at physics frequency; the policy cannot disable checks."""

    def __init__(self, limits: tuple[CoordinateLimit, ...], *, maximum_force_n: float,
                 support_origin_xyz_m: tuple[float, ...], support_tolerance_m: float) -> None:
        self.limits = limits
        self.maximum_force_n = maximum_force_n
        self.origin = support_origin_xyz_m
        self.support_tolerance_m = support_tolerance_m
        if (not limits or len({limit.name for limit in limits}) != len(limits)
                or len(self.origin) != 3 or not all(math.isfinite(v) for v in self.origin)
                or not math.isfinite(maximum_force_n) or maximum_force_n <= 0
                or not math.isfinite(support_tolerance_m) or support_tolerance_m <= 0):
            raise ValueError("invalid independent safety configuration")

    def check(self, sample: PhysicsSample) -> tuple[str, ...]:
        failures = []
        if (len(sample.joint_positions) != len(self.limits)
                or len(sample.joint_velocities) != len(self.limits)
                or len(sample.joint_targets) != len(self.limits)
                or len(sample.pelvis_xyz_m) != 3 or len(sample.torso_quaternion_wxyz) != 4
                or len(sample.fingertip_xyz_m) != 3
                or len(sample.key_joint_targets_m) != len(sample.key_joint_positions_m)
                or not sample.key_joint_positions_m
                or len(sample.key_body_positions_xyz_m) != 3 * len(sample.key_joint_positions_m)
                or len(sample.key_force_vectors_n) != 3 * len(sample.key_joint_positions_m)):
            return ("INVALID_PHYSICS_SAMPLE",)
        if any(value != 0.0 for value in sample.key_joint_targets_m):
            failures.append("COMMANDED_KEYCAP")
        if any(value < -0.0045 or value > 0.0005 for value in sample.key_joint_positions_m):
            failures.append("KEY_TRAVEL_LIMIT")
        for limit, position, velocity in zip(self.limits, sample.joint_positions, sample.joint_velocities, strict=True):
            tolerance = 0.03 if limit.unit == "rad" else 0.005
            if position < limit.lower - tolerance or position > limit.upper + tolerance:
                failures.append("JOINT_LIMIT")
            if abs(velocity) > limit.maximum_speed + 0.05:
                failures.append("JOINT_SPEED")
        if any(math.sqrt(sum(v * v for v in sample.key_force_vectors_n[i:i + 3])) > self.maximum_force_n
               for i in range(0, len(sample.key_force_vectors_n), 3)):
            failures.append("EXCESS_FORCE")
        if math.dist(sample.pelvis_xyz_m, self.origin) > self.support_tolerance_m:
            failures.append("UNSAFE_SUPPORT")
        w, x, y, z = sample.torso_quaternion_wxyz
        if abs(w*w + x*x + y*y + z*z - 1) > 0.02 or 1 - 2 * (x*x + y*y) < math.cos(0.25):
            failures.append("UNSAFE_POSTURE")
        return tuple(dict.fromkeys(failures))
