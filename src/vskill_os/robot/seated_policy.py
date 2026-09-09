"""Manifest validation for passive-chair seated-locomotion policy artifacts."""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
from typing import Any, Mapping

from ..config import load_json
from .g1 import G1HardwareProfile, G1PolicyManifest


EXPECTED_ACTOR_OBSERVATIONS: tuple[tuple[str, int], ...] = (
    ("torso_angular_velocity", 3),
    ("projected_gravity", 3),
    ("joint_positions", 29),
    ("joint_velocities", 29),
    ("last_action", 29),
    ("velocity_command", 3),
)

EXPECTED_CRITIC_ONLY_OBSERVATIONS: tuple[tuple[str, int], ...] = (
    ("torso_linear_velocity", 3),
    ("foot_heights", 2),
    ("foot_air_times", 2),
    ("foot_contact_states", 2),
    ("foot_contact_forces", 6),
    ("chair_relative_pose", 9),
    ("chair_relative_velocity", 6),
    ("chair_projected_gravity", 3),
    ("caster_contact_states", 5),
    ("caster_contact_forces", 15),
    ("pelvis_seat_contact_states", 3),
    ("pelvis_seat_contact_forces", 9),
    ("chair_leg_contact_states", 14),
    ("chair_leg_contact_forces", 42),
)


def _components(data: object, field_name: str) -> tuple[tuple[str, int], ...]:
    if not isinstance(data, list):
        raise ValueError(f"{field_name} must be a list")
    result: list[tuple[str, int]] = []
    for item in data:
        if not isinstance(item, dict):
            raise ValueError(f"{field_name} entries must be objects")
        name = str(item["name"])
        dimension = int(item["dimension"])
        if not name.strip() or dimension <= 0:
            raise ValueError(f"{field_name} entries require a name and positive dimension")
        result.append((name, dimension))
    return tuple(result)


@dataclass(frozen=True, slots=True)
class SeatedLocomotionPolicyManifest:
    """Pins the asymmetric actor/critic schema beside one deployable policy."""

    deployment: G1PolicyManifest
    actor_observations: tuple[tuple[str, int], ...]
    critic_only_observations: tuple[tuple[str, int], ...]
    command_limit_vx_mps: float
    command_limit_vy_mps: float
    command_limit_yaw_rps: float
    passive_caster_count: int
    seat_rotation_unlocked: bool
    pelvis_seat_contact_loss_termination_s: float
    tilt_termination_rad: float
    symmetry_regularization: bool
    foot_slip_regularization: bool
    command_curriculum: bool
    source: str

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SeatedLocomotionPolicyManifest":
        task = data.get("seated_locomotion")
        if not isinstance(task, dict):
            raise ValueError("seated_locomotion manifest section is required")
        commands = task.get("command_limits")
        chair = task.get("passive_chair")
        termination = task.get("training_termination")
        training = task.get("training_components")
        if not all(isinstance(item, dict) for item in (commands, chair, termination, training)):
            raise ValueError("seated policy command, chair, termination, and training sections are required")
        return cls(
            deployment=G1PolicyManifest.from_dict(data),
            actor_observations=_components(
                task.get("actor_observations"), "actor_observations"
            ),
            critic_only_observations=_components(
                task.get("critic_only_observations"), "critic_only_observations"
            ),
            command_limit_vx_mps=float(commands["vx_mps"]),
            command_limit_vy_mps=float(commands["vy_mps"]),
            command_limit_yaw_rps=float(commands["yaw_rate_rps"]),
            passive_caster_count=int(chair["caster_count"]),
            seat_rotation_unlocked=bool(chair["seat_rotation_unlocked"]),
            pelvis_seat_contact_loss_termination_s=float(
                termination["pelvis_seat_contact_loss_s"]
            ),
            tilt_termination_rad=float(termination["torso_or_chair_tilt_rad"]),
            symmetry_regularization=bool(training["symmetry_regularization"]),
            foot_slip_regularization=bool(training["foot_slip_regularization"]),
            command_curriculum=bool(training["command_curriculum"]),
            source=str(task["source"]),
        )

    @classmethod
    def from_json(cls, path: str | Path) -> "SeatedLocomotionPolicyManifest":
        return cls.from_dict(load_json(path))

    @property
    def actor_dimension(self) -> int:
        return sum(dimension for _, dimension in self.actor_observations)

    @property
    def critic_dimension(self) -> int:
        return self.actor_dimension + sum(
            dimension for _, dimension in self.critic_only_observations
        )

    @property
    def training_risk_flags(self) -> tuple[str, ...]:
        if (
            self.foot_slip_regularization
            and not self.symmetry_regularization
            and not self.command_curriculum
        ):
            return ("FOOT_SLIP_ONLY_STATIONARY_LOCAL_OPTIMUM",)
        return ()

    def validate_for(self, profile: G1HardwareProfile) -> None:
        self.deployment.validate_for(profile)
        errors: list[str] = []
        if self.deployment.action_type != "joint_position_residual":
            errors.append("seated policy action_type must be joint_position_residual")
        if self.actor_observations != EXPECTED_ACTOR_OBSERVATIONS:
            errors.append(
                "actor observation schema must contain proprioception and velocity command only"
            )
        if self.critic_only_observations != EXPECTED_CRITIC_ONLY_OBSERVATIONS:
            errors.append("critic-only chair/contact observation schema differs")
        if self.actor_dimension != 96 or self.critic_dimension != 217:
            errors.append("expected actor/critic dimensions are 96/217")
        command_limits = (
            self.command_limit_vx_mps,
            self.command_limit_vy_mps,
            self.command_limit_yaw_rps,
        )
        if any(value <= 0.0 or not math.isfinite(value) for value in command_limits):
            errors.append("command limits must be finite and positive")
        if self.passive_caster_count <= 0 or not self.seat_rotation_unlocked:
            errors.append("policy must identify an unlocked passive caster chair")
        if (
            self.pelvis_seat_contact_loss_termination_s <= 0.0
            or self.tilt_termination_rad <= 0.0
        ):
            errors.append("training termination thresholds must be positive")
        if not self.source.strip():
            errors.append("policy method source is required")
        if errors:
            raise ValueError("seated locomotion policy manifest mismatch: " + "; ".join(errors))
