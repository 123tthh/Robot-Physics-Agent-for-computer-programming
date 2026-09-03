"""Unitree G1 29-DoF hardware identity and policy compatibility checks.

The 29-DoF/5010 label is not sufficient to select one dynamics model.  Unitree
publishes several ``mode_machine`` variants with different hip transmission
ratios and waist locking.  This module makes that identity explicit before a
policy can cross the hardware adapter boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
from typing import Any, Mapping

from ..config import load_json


class G1Joint(IntEnum):
    """Official SDK order for the 29 actuated body joints."""

    LEFT_HIP_PITCH = 0
    LEFT_HIP_ROLL = 1
    LEFT_HIP_YAW = 2
    LEFT_KNEE = 3
    LEFT_ANKLE_PITCH = 4
    LEFT_ANKLE_ROLL = 5
    RIGHT_HIP_PITCH = 6
    RIGHT_HIP_ROLL = 7
    RIGHT_HIP_YAW = 8
    RIGHT_KNEE = 9
    RIGHT_ANKLE_PITCH = 10
    RIGHT_ANKLE_ROLL = 11
    WAIST_YAW = 12
    WAIST_ROLL = 13
    WAIST_PITCH = 14
    LEFT_SHOULDER_PITCH = 15
    LEFT_SHOULDER_ROLL = 16
    LEFT_SHOULDER_YAW = 17
    LEFT_ELBOW = 18
    LEFT_WRIST_ROLL = 19
    LEFT_WRIST_PITCH = 20
    LEFT_WRIST_YAW = 21
    RIGHT_SHOULDER_PITCH = 22
    RIGHT_SHOULDER_ROLL = 23
    RIGHT_SHOULDER_YAW = 24
    RIGHT_ELBOW = 25
    RIGHT_WRIST_ROLL = 26
    RIGHT_WRIST_PITCH = 27
    RIGHT_WRIST_YAW = 28


@dataclass(frozen=True, slots=True)
class G1HardwareProfile:
    mode_machine: int
    description_asset: str
    hip_pitch_gear_ratio: float
    hip_roll_gear_ratio: float
    wrist_motor: str
    waist_locked: bool
    body_dof: int = 29

    def __post_init__(self) -> None:
        if self.body_dof != len(G1Joint):
            raise ValueError("G1 29-DoF profile must expose exactly 29 body joints")
        if self.wrist_motor != "5010":
            raise ValueError("this project profile set only accepts the 5010 wrist")

    @property
    def compatibility_key(self) -> str:
        waist = "locked" if self.waist_locked else "free"
        return (
            f"g1-29dof-mode{self.mode_machine}-wrist5010-"
            f"hip{self.hip_pitch_gear_ratio:g}x{self.hip_roll_gear_ratio:g}-{waist}"
        )

    def validate_observation(
        self,
        *,
        mode_machine: int,
        body_dof: int,
        wrist_motor: str | None = None,
    ) -> None:
        errors: list[str] = []
        if mode_machine != self.mode_machine:
            errors.append(
                f"mode_machine observed {mode_machine}, expected {self.mode_machine}"
            )
        if body_dof != self.body_dof:
            errors.append(f"body_dof observed {body_dof}, expected {self.body_dof}")
        if wrist_motor is not None and wrist_motor != self.wrist_motor:
            errors.append(
                f"wrist motor observed {wrist_motor!r}, expected {self.wrist_motor!r}"
            )
        if errors:
            raise ValueError("G1 hardware/profile mismatch: " + "; ".join(errors))


def _profile(
    mode_machine: int,
    pitch_ratio: float,
    roll_ratio: float,
    waist_locked: bool,
) -> G1HardwareProfile:
    return G1HardwareProfile(
        mode_machine=mode_machine,
        description_asset=f"g1_29dof_mode_{mode_machine}",
        hip_pitch_gear_ratio=pitch_ratio,
        hip_roll_gear_ratio=roll_ratio,
        wrist_motor="5010",
        waist_locked=waist_locked,
    )


# Source of truth: Unitree's g1_description README.  Keep this list explicit;
# do not infer a dynamics profile from the marketing label "29DoF 5010".
G1_5010_PROFILES: dict[int, G1HardwareProfile] = {
    13: _profile(13, 14.3, 22.5, False),
    14: _profile(14, 14.3, 22.5, True),
    15: _profile(15, 22.5, 22.5, False),
    16: _profile(16, 22.5, 22.5, True),
    18: _profile(18, 22.5, 22.5, False),
}


@dataclass(frozen=True, slots=True)
class G1DeploymentConfig:
    """Separates a convenient simulation default from real-robot identity."""

    selected_mode_machine: int | None
    simulation_mode_machine: int = 15
    require_hardware_confirmation: bool = True
    expected_body_dof: int = 29
    expected_wrist_motor: str = "5010"

    def __post_init__(self) -> None:
        if self.selected_mode_machine is not None:
            self._get_profile(self.selected_mode_machine)
        self._get_profile(self.simulation_mode_machine)
        if self.expected_body_dof != 29:
            raise ValueError("this stack currently supports G1 29-DoF only")
        if self.expected_wrist_motor != "5010":
            raise ValueError("this stack currently targets the 5010 wrist only")

    @staticmethod
    def _get_profile(mode_machine: int) -> G1HardwareProfile:
        try:
            return G1_5010_PROFILES[mode_machine]
        except KeyError as exc:
            choices = ", ".join(str(item) for item in sorted(G1_5010_PROFILES))
            raise ValueError(
                f"unsupported G1 29-DoF/5010 mode_machine {mode_machine}; "
                f"expected one of {choices}"
            ) from exc

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "G1DeploymentConfig":
        selected = data.get("selected_mode_machine")
        return cls(
            selected_mode_machine=None if selected is None else int(selected),
            simulation_mode_machine=int(data.get("simulation_mode_machine", 15)),
            require_hardware_confirmation=bool(
                data.get("require_hardware_confirmation", True)
            ),
            expected_body_dof=int(data.get("expected_body_dof", 29)),
            expected_wrist_motor=str(data.get("expected_wrist_motor", "5010")),
        )

    @classmethod
    def from_json(cls, path: str | Path) -> "G1DeploymentConfig":
        return cls.from_dict(load_json(path))

    def simulation_profile(self) -> G1HardwareProfile:
        return self._get_profile(self.simulation_mode_machine)

    def hardware_profile(
        self,
        *,
        observed_mode_machine: int | None = None,
        observed_body_dof: int = 29,
        observed_wrist_motor: str | None = None,
    ) -> G1HardwareProfile:
        if self.selected_mode_machine is None:
            raise ValueError(
                "selected_mode_machine is unset; read Machine Type in the Unitree "
                "app and pin it before enabling the hardware adapter"
            )
        profile = self._get_profile(self.selected_mode_machine)
        if self.require_hardware_confirmation and observed_mode_machine is None:
            raise ValueError("hardware mode confirmation is required")
        if observed_mode_machine is not None:
            profile.validate_observation(
                mode_machine=observed_mode_machine,
                body_dof=observed_body_dof,
                wrist_motor=observed_wrist_motor,
            )
        return profile


@dataclass(frozen=True, slots=True)
class G1PolicyManifest:
    """Metadata required beside every deployable G1 policy artifact."""

    robot: str
    mode_machine: int
    body_dof: int
    wrist_motor: str
    waist_locked: bool
    hip_gear_ratio: tuple[float, float]
    joint_order: tuple[str, ...]
    action_type: str
    policy_hz: int
    low_level_hz: int
    simulator: str
    simulator_version: str
    training_commit: str

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "G1PolicyManifest":
        ratios = tuple(float(item) for item in data["hip_gear_ratio"])
        if len(ratios) != 2:
            raise ValueError("hip_gear_ratio must contain pitch and roll")
        return cls(
            robot=str(data["robot"]),
            mode_machine=int(data["mode_machine"]),
            body_dof=int(data["body_dof"]),
            wrist_motor=str(data["wrist_motor"]),
            waist_locked=bool(data["waist_locked"]),
            hip_gear_ratio=(ratios[0], ratios[1]),
            joint_order=tuple(str(item) for item in data["joint_order"]),
            action_type=str(data["action_type"]),
            policy_hz=int(data["policy_hz"]),
            low_level_hz=int(data["low_level_hz"]),
            simulator=str(data["simulator"]),
            simulator_version=str(data["simulator_version"]),
            training_commit=str(data["training_commit"]),
        )

    @classmethod
    def from_json(cls, path: str | Path) -> "G1PolicyManifest":
        return cls.from_dict(load_json(path))

    def validate_for(self, profile: G1HardwareProfile) -> None:
        expected_order = tuple(joint.name.lower() for joint in G1Joint)
        errors: list[str] = []
        if self.robot != "unitree_g1":
            errors.append(f"robot is {self.robot!r}, expected 'unitree_g1'")
        if self.mode_machine != profile.mode_machine:
            errors.append("mode_machine differs")
        if self.body_dof != profile.body_dof:
            errors.append("body_dof differs")
        if self.wrist_motor != profile.wrist_motor:
            errors.append("wrist motor differs")
        if self.waist_locked != profile.waist_locked:
            errors.append("waist lock state differs")
        if any(
            abs(observed - expected) > 1e-6
            for observed, expected in zip(
                self.hip_gear_ratio,
                (profile.hip_pitch_gear_ratio, profile.hip_roll_gear_ratio),
                strict=True,
            )
        ):
            errors.append("hip gear ratio differs")
        if self.joint_order != expected_order:
            errors.append("joint order differs")
        if self.action_type not in {
            "joint_position_target",
            "joint_position_residual",
            "root_velocity",
        }:
            errors.append(f"unsupported action_type {self.action_type!r}")
        if self.policy_hz <= 0 or self.low_level_hz <= 0:
            errors.append("control frequencies must be positive")
        if self.policy_hz > self.low_level_hz:
            errors.append("policy_hz cannot exceed low_level_hz")
        if not self.simulator_version.strip() or not self.training_commit.strip():
            errors.append("simulator version and training commit are required")
        if errors:
            raise ValueError("G1 policy/profile mismatch: " + "; ".join(errors))
