"""Explicit direct-joint fixture, with no calibrated IK postures or trajectories."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import xml.etree.ElementTree as ET

from ....learning.environment import JointControlSpec
from ....learning.serialization import JsonRecord
from ....simulation.dex5_assets import (
    BODY_SOURCE, DEFAULT_ASSET_ROOT, HAND_SOURCES, audit_pinned_assets,
    compose_mode15_g1_with_dex5, validate_g1_dex5_urdf,
)
from ..dex5_scene_geometry import Dex5SceneGeometryConfig

ARM_NAMES = ("shoulder_pitch", "shoulder_roll", "shoulder_yaw", "elbow", "wrist_roll", "wrist_pitch", "wrist_yaw")
FINGERS = ("thumb", "index", "middle", "ring", "little")
LEFT_JOINTS = tuple(f"left_{name}_joint" for name in ARM_NAMES) + tuple(
    f"left_hand_{finger}_{index}_joint" for finger in FINGERS
    for index in range(4 if finger == "thumb" else 3)
)


@dataclass(frozen=True, slots=True)
class SingleHandConfig(JsonRecord):
    physics_dt_s: float = 1 / 240
    decimation: int = 4
    settling_steps: int = 240
    maximum_target_speed_rad_s: float = 0.3
    maximum_contact_force_n: float = 12.0
    maximum_joint_speed_rad_s: float = 8.0
    support_tolerance_m: float = 0.03
    enabled_joints: tuple[str, ...] = ("left_hand_index_1_joint",)
    pad_radius_m: float = 0.006
    body_contact_offset_m: float = 0.001
    key_center_z_m: float = 0.791
    keyboard_operator_row_x_m: float = 0.528
    keyboard_left_origin_y_m: float = 0.17275

    def __post_init__(self) -> None:
        JsonRecord.__post_init__(self)
        if (min(self.physics_dt_s, self.maximum_target_speed_rad_s, self.maximum_contact_force_n,
                self.maximum_joint_speed_rad_s, self.support_tolerance_m, self.pad_radius_m, self.body_contact_offset_m) <= 0
                or self.decimation < 1 or self.settling_steps < 1
                or not self.enabled_joints or not set(self.enabled_joints) <= set(LEFT_JOINTS)):
            raise ValueError("invalid physical environment limits or enabled joints")

    def geometry(self) -> Dex5SceneGeometryConfig:
        height_shift = self.key_center_z_m - 0.675
        return Dex5SceneGeometryConfig(
            key_center_z_m=self.key_center_z_m,
            keyboard_operator_row_x_m=self.keyboard_operator_row_x_m,
            keyboard_left_origin_y_m=self.keyboard_left_origin_y_m,
            table_top_z_m=0.650 + height_shift,
            keyboard_shell_top_z_m=0.664 + height_shift,
        )


def prepare_fixed_pelvis_asset(output: Path) -> Path:
    """Compose pinned OEM geometry without the movable seating fixture."""
    audit_pinned_assets()
    output.parent.mkdir(parents=True, exist_ok=True)
    asset_root = Path(DEFAULT_ASSET_ROOT)
    compose_mode15_g1_with_dex5(
        g1_urdf=asset_root / BODY_SOURCE,
        left_hand_urdf=asset_root / HAND_SOURCES["left"],
        right_hand_urdf=asset_root / HAND_SOURCES["right"],
        output=output, seating_fixture=False,
    )
    return output


def direct_joint_initialization(urdf: Path) -> dict[str, float]:
    """Hand-authored angles. Forward geometry checks are not inverse kinematics.

    The pelvis is fixed at 0.625 m, with clearance over the 0.44 m chair seat.
    Hands/arms do not load dex5_postures.json or any previous rollout.
    """
    joints = {joint.get("name"): joint for joint in ET.parse(urdf).getroot().findall("joint")}
    positions = {name: 0.0 for name, joint in joints.items() if joint.get("type") != "fixed"}
    for side in ("left", "right"):
        positions.update({f"{side}_hip_pitch_joint": -1.25, f"{side}_knee_joint": 1.6,
                          f"{side}_ankle_pitch_joint": -0.35})
        for finger in ("middle", "ring", "little"):
            positions[f"{side}_hand_{finger}_1_joint"] = 1.3
            positions[f"{side}_hand_{finger}_2_joint"] = 1.3
    positions.update({"left_shoulder_pitch_joint": -1.0, "left_shoulder_roll_joint": 0.2,
                      "left_elbow_joint": 1.0, "right_shoulder_pitch_joint": -1.5,
                      "right_shoulder_roll_joint": -0.6, "right_elbow_joint": 1.0})
    for name, joint in joints.items():
        mimic = joint.find("mimic")
        if mimic is not None:
            positions[name] = positions[mimic.get("joint")] * float(mimic.get("multiplier", "1")) + float(mimic.get("offset", "0"))
        limit = joint.find("limit")
        if name in positions and limit is not None and not float(limit.get("lower")) <= positions[name] <= float(limit.get("upper")):
            raise ValueError(f"initial joint angle outside asset limits: {name}")
    return positions


def control_spec(urdf: Path, config: SingleHandConfig) -> JointControlSpec:
    validate_g1_dex5_urdf(urdf)
    joints = {joint.get("name"): joint for joint in ET.parse(urdf).getroot().findall("joint")}
    if len(LEFT_JOINTS) != 23 or any(joints[name].find("mimic") is not None for name in LEFT_JOINTS):
        raise ValueError("single-hand actions must contain 7 arm/wrist plus 16 independent hand joints")
    limits = [joints[name].find("limit") for name in LEFT_JOINTS]
    return JointControlSpec(
        LEFT_JOINTS, tuple(float(limit.get("lower")) for limit in limits),
        tuple(float(limit.get("upper")) for limit in limits),
        tuple(min(config.maximum_target_speed_rad_s, float(limit.get("velocity"))) for limit in limits),
        config.enabled_joints, config.physics_dt_s * config.decimation,
    )
