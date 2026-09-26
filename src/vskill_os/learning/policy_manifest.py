"""Named, versioned low-level actions. No simulator or learning dependency."""

from dataclasses import dataclass
from typing import Literal

from .contracts import JointRecord, _nonempty, _unique
from .environment import ActionRejected, JointControlSpec
from .serialization import JsonRecord


def dex5_action_names(hand: str) -> tuple[str, ...]:
    """Seven arm/wrist and sixteen independent hand drives; never mimic DOFs."""
    if hand not in ("left", "right"):
        raise ValueError("hand must be left or right")
    arm = ("shoulder_pitch", "shoulder_roll", "shoulder_yaw", "elbow",
           "wrist_roll", "wrist_pitch", "wrist_yaw")
    return tuple(f"{hand}_{name}_joint" for name in arm) + tuple(
        f"{hand}_hand_{finger}_{index}_joint"
        for finger in ("thumb", "index", "middle", "ring", "little")
        for index in range(4 if finger == "thumb" else 3)
    )


@dataclass(frozen=True, slots=True)
class JointMask(JsonRecord):
    active: tuple[str, ...]
    held: tuple[str, ...]

    def __post_init__(self) -> None:
        JsonRecord.__post_init__(self)
        _unique(self.active, "active joints")
        if self.held:
            _unique(self.held, "held joints")
        if not self.active or set(self.active) & set(self.held):
            raise ValueError("empty active mask or overlapping joint owners")


@dataclass(frozen=True, slots=True)
class ControlClaim(JsonRecord):
    controller: str
    joints: tuple[str, ...]

    def __post_init__(self) -> None:
        JsonRecord.__post_init__(self)
        _nonempty(self.controller)
        if self.joints:
            _unique(self.joints, "claimed joints")


@dataclass(frozen=True, slots=True)
class ActionScale(JsonRecord):
    schema_version: Literal["vf-action-scale/1"] = "vf-action-scale/1"
    reference_dt_s: float = 1 / 60
    delta_rad: tuple[float, ...] = (0.01,) * 23
    initial_tracking_tolerance_rad: float = 0.05

    def __post_init__(self) -> None:
        JsonRecord.__post_init__(self)
        if (len(self.delta_rad) != 23 or min(self.delta_rad) <= 0 or self.reference_dt_s <= 0
                or not 0 < self.initial_tracking_tolerance_rad <= 0.05):
            raise ValueError("invalid 23-joint scale, reference dt or initialization tolerance")


@dataclass(frozen=True, slots=True)
class ActionSchema(JsonRecord):
    schema_version: Literal["vf-action-schema/1"]
    hand: Literal["left", "right"]
    control: JointControlSpec
    mask: JointMask
    scale: ActionScale
    support_mode: Literal["fixed_pelvis_seated_pd"] = "fixed_pelvis_seated_pd"
    initialization: Literal["hand_authored_direct_joint_v1"] = "hand_authored_direct_joint_v1"

    def __post_init__(self) -> None:
        JsonRecord.__post_init__(self)
        names = dex5_action_names(self.hand)
        if (self.control.joint_names != names or self.control.enabled_joints != self.mask.active
                or self.mask.active != tuple(n for n in names if n in self.mask.active)
                or self.mask.held != tuple(n for n in names if n not in self.mask.active)):
            raise ValueError("action order, drive mask and PD hold partition must agree")

    @classmethod
    def from_control(cls, control: JointControlSpec, scale: ActionScale, hand: str = "left"):
        return cls("vf-action-schema/1", hand, control,
                   JointMask(control.enabled_joints, tuple(n for n in control.joint_names
                                                          if n not in control.enabled_joints)), scale)

    @property
    def step_scale_rad(self) -> tuple[float, ...]:
        return tuple(v * self.control.control_dt_s / self.scale.reference_dt_s for v in self.scale.delta_rad)

    def claims(self) -> tuple[ControlClaim, ...]:
        return (ControlClaim("joint_policy", self.mask.active), ControlClaim("frozen_body_pd", self.mask.held))

    def check_claims(self, claims: tuple[ControlClaim, ...]) -> None:
        # The scoped 23 coordinates have exactly one target writer each. The
        # backend owns all remaining body/mimic coordinates, outside this API.
        if claims != self.claims():
            raise ActionRejected("CONTROL_OWNERSHIP")


@dataclass(frozen=True, slots=True)
class NormalizedJointAction(JsonRecord):
    joint_names: tuple[str, ...]
    values: tuple[float, ...]
    duration_s: float
    valid_until_s: float
    owner_token: str
    sequence: int

    def __post_init__(self) -> None:
        JsonRecord.__post_init__(self)
        _unique(self.joint_names, "normalized action joints")
        _nonempty(self.owner_token)
        if len(self.values) != len(self.joint_names) or self.duration_s <= 0 or self.sequence < 0:
            raise ValueError("invalid normalized action dimensions, duration or sequence")


@dataclass(frozen=True, slots=True)
class PolicyLease(JsonRecord):
    env_id: str
    episode_id: str
    owner_token: str
    schema_sha256: str


@dataclass(frozen=True, slots=True)
class MappingEvent(JsonRecord):
    kind: Literal["reset", "policy", "hold", "stop", "resume", "rejected", "fault", "finish"]
    before_time_s: float
    after_time_s: float
    state: Literal["active", "paused", "latched", "finished"]
    lease_token: str
    policy_sequence: int
    port_token: str
    action: NormalizedJointAction | None
    proposed_targets_rad: tuple[float, ...]
    filtered_targets_rad: tuple[float, ...]
    joints: tuple[JointRecord, ...]
    backend_action_index: int | None
    failure_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MappingTrace(JsonRecord):
    schema_version: Literal["vf-action-mapping/1"]
    env_id: str
    episode_id: str
    action_schema: ActionSchema
    control_claims: tuple[ControlClaim, ...]
    physical_episode_sha256: str
    events: tuple[MappingEvent, ...]
