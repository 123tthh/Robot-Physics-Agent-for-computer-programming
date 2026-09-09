"""P0/P1 workstation asset-chain contract without Isaac dependencies."""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
from typing import Any, Mapping

from ..config import load_json


@dataclass(frozen=True, slots=True)
class AssetStage:
    id: str
    control_phase: str
    depends_on: tuple[str, ...]
    robot_xy_yaw: tuple[float, float, float]
    required_assets: tuple[str, ...]
    required_evidence: tuple[str, ...]

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AssetStage":
        pose = tuple(float(value) for value in data["robot_xy_yaw"])
        if len(pose) != 3 or not all(math.isfinite(value) for value in pose):
            raise ValueError("asset-stage robot pose must contain finite x/y/yaw")
        result = cls(
            id=str(data["id"]),
            control_phase=str(data["control_phase"]),
            depends_on=tuple(str(value) for value in data.get("depends_on", ())),
            robot_xy_yaw=pose,
            required_assets=tuple(str(value) for value in data["required_assets"]),
            required_evidence=tuple(str(value) for value in data["required_evidence"]),
        )
        if not result.id or not result.control_phase:
            raise ValueError("asset stage id and control phase cannot be empty")
        if not result.required_assets or not result.required_evidence:
            raise ValueError(f"asset stage {result.id!r} requires assets and evidence")
        return result


@dataclass(frozen=True, slots=True)
class WorkstationAssetChain:
    schema_version: int
    name: str
    meters_per_unit: float
    up_axis: str
    stage_order: tuple[str, ...]
    stages: tuple[AssetStage, ...]
    workstation: Mapping[str, Any]
    chair: Mapping[str, Any]
    validation: Mapping[str, Any]

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "WorkstationAssetChain":
        result = cls(
            schema_version=int(data["schema_version"]),
            name=str(data["name"]),
            meters_per_unit=float(data["meters_per_unit"]),
            up_axis=str(data["up_axis"]),
            stage_order=tuple(str(value) for value in data["stage_order"]),
            stages=tuple(AssetStage.from_dict(value) for value in data["stages"]),
            workstation=dict(data["workstation"]),
            chair=dict(data["chair"]),
            validation=dict(data["validation"]),
        )
        result.validate()
        return result

    @classmethod
    def from_json(cls, path: str | Path) -> "WorkstationAssetChain":
        return cls.from_dict(load_json(path))

    def validate(self) -> None:
        if self.schema_version != 1:
            raise ValueError("unsupported workstation asset-chain schema")
        if self.meters_per_unit != 1.0 or self.up_axis != "Z":
            raise ValueError("workstation scene must use meters and Z-up")
        ids = tuple(stage.id for stage in self.stages)
        if ids != self.stage_order or len(ids) != len(set(ids)):
            raise ValueError("asset stages must be unique and match stage_order")
        completed: set[str] = set()
        for stage in self.stages:
            if any(dependency not in completed for dependency in stage.depends_on):
                raise ValueError(f"asset stage {stage.id!r} has an invalid dependency")
            completed.add(stage.id)
        expected = (
            "room_entry",
            "workstation_approach",
            "chair_alignment",
            "pre_sit",
            "seated_stabilization",
            "work_pose",
        )
        if self.stage_order != expected:
            raise ValueError("P0/P1 asset chain is missing a required workstation stage")
        chair = self.chair
        if chair.get("asset_type") != "passive_mobile_office_chair_5caster":
            raise ValueError("P1 requires a passive five-caster office chair")
        if int(chair.get("caster_count", 0)) != 5:
            raise ValueError("mobile office chair must expose five casters")
        if int(chair.get("passive_swivel_joint_count", 0)) != 5:
            raise ValueError("each caster must expose one passive swivel joint")
        if int(chair.get("passive_wheel_joint_count", 0)) != 5:
            raise ValueError("each caster must expose one passive rolling joint")
        contacts = set(str(value) for value in chair.get("contact_surfaces", ()))
        required_contacts = {"seat", "backrest", *(f"caster_{index}" for index in range(5))}
        if not required_contacts <= contacts:
            raise ValueError("chair contact surfaces are incomplete")
        if float(chair.get("frame_mass_kg", 0.0)) <= 0.0:
            raise ValueError("chair frame mass must be positive")
        seat_size = tuple(float(value) for value in chair.get("seat_size_m", ()))
        if len(seat_size) != 3 or any(value <= 0.0 for value in seat_size):
            raise ValueError("chair seat size must contain three positive dimensions")
        seat_top_height = float(chair.get("seat_center_z_m", 0.0)) + seat_size[2] / 2.0
        if not 0.43 <= seat_top_height <= 0.50:
            raise ValueError("chair seat top must stay within the G1 sitting range [0.43, 0.50] m")
        if chair.get("world_fixed") is not False:
            raise ValueError("mobile chair root must not be fixed to the world")
        mobility_probe = chair.get("mobility_probe", {})
        for name in (
            "force_n",
            "force_duration_s",
            "coast_duration_s",
            "minimum_displacement_m",
        ):
            value = float(mobility_probe.get(name, 0.0))
            if not math.isfinite(value) or value <= 0.0:
                raise ValueError(f"chair mobility probe {name} must be finite and positive")
        rolling_ratio = float(
            mobility_probe.get("maximum_rolling_contribution_ratio", 0.0)
        )
        if not math.isfinite(rolling_ratio) or not 0.0 < rolling_ratio < 0.5:
            raise ValueError(
                "chair mobility probe must require sliding-dominant motion"
            )
        for name in ("wheel_radius_m", "wheel_width_m", "caster_radius_from_center_m"):
            value = float(chair.get(name, 0.0))
            if not math.isfinite(value) or value <= 0.0:
                raise ValueError(f"chair {name} must be finite and positive")
        for name in ("swivel_joint_friction", "rolling_joint_friction"):
            value = float(chair.get(name, -1.0))
            if not math.isfinite(value) or value < 0.0:
                raise ValueError(f"chair {name} must be finite and non-negative")
        rolling_velocity = float(chair.get("rolling_joint_max_velocity_deg_s", 0.0))
        if not math.isfinite(rolling_velocity) or rolling_velocity <= 0.0:
            raise ValueError(
                "chair rolling_joint_max_velocity_deg_s must be finite and positive"
            )
        required_paths = tuple(self.validation.get("required_prim_paths", ()))
        if not required_paths or len(required_paths) != len(set(required_paths)):
            raise ValueError("required USD prim paths must be unique and non-empty")

    def stage(self, stage_id: str) -> AssetStage:
        for stage in self.stages:
            if stage.id == stage_id:
                return stage
        raise KeyError(stage_id)

    def summary(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "stage_order": list(self.stage_order),
            "stage_count": len(self.stages),
            "chair_asset_type": self.chair["asset_type"],
            "caster_count": int(self.chair["caster_count"]),
            "passive_dof_count": int(self.chair["passive_swivel_joint_count"])
            + int(self.chair["passive_wheel_joint_count"]),
            "seat_top_height_m": float(self.chair["seat_center_z_m"])
            + float(self.chair["seat_size_m"][2]) / 2.0,
            "world_fixed": bool(self.chair["world_fixed"]),
            "contact_surfaces": list(self.chair["contact_surfaces"]),
        }
