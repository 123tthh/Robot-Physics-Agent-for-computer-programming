"""SI units, torso-frame commands, world-frame poses, versioned observations."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Mapping, Sequence

from ..control.commands import RootVelocityCommand
from ..robot.seated_policy import (
    EXPECTED_ACTOR_OBSERVATIONS, EXPECTED_CRITIC_ONLY_OBSERVATIONS,
)

SCHEMA_VERSION = "chair-v1"
DT = 0.02


def finite(values: Sequence[float]) -> None:
    if any(isinstance(v, bool) or not math.isfinite(v) for v in values):
        raise ValueError("expected finite numeric values, not booleans")


def vector(values: Sequence[float], size: int) -> tuple[float, ...]:
    if len(values) != size:
        raise ValueError(f"expected {size} values, got {len(values)}")
    finite(values)
    return tuple(float(v) for v in values)


def flatten(fields: Mapping[str, Sequence[float]], schema: tuple) -> tuple[float, ...]:
    if set(fields) != {name for name, _ in schema}:
        raise ValueError("observation fields do not match the pinned schema")
    return tuple(v for name, size in schema for v in vector(fields[name], size))


@dataclass(frozen=True, slots=True)
class Observation:
    actor: tuple[float, ...]
    critic: tuple[float, ...]

    def __post_init__(self) -> None:
        vector(self.actor, 96)
        vector(self.critic, 217)

    @classmethod
    def pack(cls, actor: Mapping[str, Sequence[float]],
             clean_actor: Mapping[str, Sequence[float]],
             privileged: Mapping[str, Sequence[float]]) -> Observation:
        # Explicitly separate noisy actor inputs from uncorrupted critic inputs.
        return cls(flatten(actor, EXPECTED_ACTOR_OBSERVATIONS),
                   flatten(clean_actor, EXPECTED_ACTOR_OBSERVATIONS)
                   + flatten(privileged, EXPECTED_CRITIC_ONLY_OBSERVATIONS))


@dataclass(frozen=True, slots=True)
class ChairCommand:
    velocity: RootVelocityCommand
    duration_s: float = 2.0

    def __post_init__(self) -> None:
        finite((self.duration_s,))
        if not 0.2 <= self.duration_s <= 5.0:
            raise ValueError("skill horizon must be in [0.2, 5] seconds")
        v = self.velocity
        finite((v.vx_mps, v.vy_mps, v.yaw_rate_rps))
        if math.hypot(v.vx_mps, v.vy_mps) > 0.5 or abs(v.yaw_rate_rps) > 0.5:
            raise ValueError("deployment command exceeds planar/yaw safety limits")
        if max(math.hypot(v.vx_mps, v.vy_mps), abs(v.yaw_rate_rps)) * self.duration_s < 0.02:
            raise ValueError("reposition requires nonzero measurable demand")
        if not math.isclose(self.duration_s / DT, round(self.duration_s / DT)):
            raise ValueError("duration must be an integer number of policy ticks")


@dataclass(frozen=True, slots=True)
class ChairSample:
    """Independent observer output; yaw is continuous/unwrapped, not modulo 2π."""

    episode_id: str
    source: str
    time_s: float
    robot_pose: tuple[float, float, float]
    chair_pose: tuple[float, float, float]
    seat_confidence: float
    contact_loss_s: float
    pelvis_offset_m: float
    torso_tilt_rad: float
    chair_tilt_rad: float
    foot_contacts: tuple[bool, bool]
    screen_visible: bool
    hands_reachable: bool
    collision: bool = False
    emergency_stop: bool = False

    def __post_init__(self) -> None:
        if not self.source or not self.episode_id:
            raise ValueError("observer identity and episode id are required")
        vector(self.robot_pose, 3)
        vector(self.chair_pose, 3)
        vals = (self.time_s, self.seat_confidence, self.contact_loss_s,
                self.pelvis_offset_m, self.torso_tilt_rad, self.chair_tilt_rad)
        finite(vals)
        if min(vals) < 0 or self.seat_confidence > 1:
            raise ValueError("invalid time/contact/offset/tilt observation")
        if len(self.foot_contacts) != 2 or any(type(v) is not bool for v in (
            *self.foot_contacts, self.screen_visible, self.hands_reachable,
            self.collision, self.emergency_stop,
        )):
            raise ValueError("contact and readiness flags must be booleans")


@dataclass(frozen=True, slots=True)
class Trace:
    command: ChairCommand
    samples: tuple[ChairSample, ...]
    motion_end_index: int
    schema: str = SCHEMA_VERSION

    @classmethod
    def from_dict(cls, value: Mapping) -> Trace:
        command = value["command"]
        samples = []
        for raw in value["samples"]:
            data = dict(raw)
            for key in ("robot_pose", "chair_pose", "foot_contacts"):
                data[key] = tuple(data[key])
            samples.append(ChairSample(**data))
        index = value["motion_end_index"]
        if type(index) is not int:
            raise ValueError("motion end index must be integer")
        return cls(ChairCommand(RootVelocityCommand(**command["velocity"]),
                                command["duration_s"]), tuple(samples), index, value["schema"])


def measured_velocity(a: ChairSample, b: ChairSample, *, chair: bool = False) -> tuple[float, float, float]:
    """World displacement rotated into the preceding torso heading."""
    dt = b.time_s - a.time_s
    if dt <= 0:
        raise ValueError("observer clock must increase")
    pa, pb = (a.chair_pose, b.chair_pose) if chair else (a.robot_pose, b.robot_pose)
    dx, dy = (pb[0] - pa[0]) / dt, (pb[1] - pa[1]) / dt
    c, s = math.cos(a.robot_pose[2]), math.sin(a.robot_pose[2])
    return c * dx + s * dy, -s * dx + c * dy, (pb[2] - pa[2]) / dt
