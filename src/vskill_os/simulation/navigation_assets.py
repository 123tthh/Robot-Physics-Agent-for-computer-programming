"""P1 navigation-room asset contract without Isaac dependencies."""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
from typing import Any, Mapping

from ..config import load_json


def _finite_tuple(data: Any, length: int, label: str) -> tuple[float, ...]:
    values = tuple(float(value) for value in data)
    if len(values) != length or not all(math.isfinite(value) for value in values):
        raise ValueError(f"{label} must contain {length} finite values")
    return values


@dataclass(frozen=True, slots=True)
class NavigationWorkstation:
    id: str
    offset_m: tuple[float, float, float]
    target: bool
    detail_level: str
    approach_xy_yaw: tuple[float, float, float]
    chair_alignment_xy_yaw: tuple[float, float, float]
    work_pose_xy_yaw: tuple[float, float, float]

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "NavigationWorkstation":
        result = cls(
            id=str(data["id"]),
            offset_m=_finite_tuple(data["offset_m"], 3, "workstation offset"),
            target=bool(data["target"]),
            detail_level=str(data["detail_level"]),
            approach_xy_yaw=_finite_tuple(
                data["approach_xy_yaw"], 3, "workstation approach pose"
            ),
            chair_alignment_xy_yaw=_finite_tuple(
                data["chair_alignment_xy_yaw"], 3, "chair alignment pose"
            ),
            work_pose_xy_yaw=_finite_tuple(
                data["work_pose_xy_yaw"], 3, "work pose"
            ),
        )
        if not result.id:
            raise ValueError("workstation id cannot be empty")
        if result.detail_level not in {"full_interaction", "navigation_static"}:
            raise ValueError("unsupported workstation detail level")
        return result


@dataclass(frozen=True, slots=True)
class NavigationRoom:
    schema_version: int
    name: str
    meters_per_unit: float
    up_axis: str
    room: Mapping[str, Any]
    robot_clearance_radius_m: float
    workstations: tuple[NavigationWorkstation, ...]
    spawn_points: tuple[Mapping[str, Any], ...]
    static_obstacles: tuple[Mapping[str, Any], ...]
    validation: Mapping[str, Any]

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "NavigationRoom":
        result = cls(
            schema_version=int(data["schema_version"]),
            name=str(data["name"]),
            meters_per_unit=float(data["meters_per_unit"]),
            up_axis=str(data["up_axis"]),
            room=dict(data["room"]),
            robot_clearance_radius_m=float(data["robot_clearance_radius_m"]),
            workstations=tuple(
                NavigationWorkstation.from_dict(item) for item in data["workstations"]
            ),
            spawn_points=tuple(dict(item) for item in data["spawn_points"]),
            static_obstacles=tuple(dict(item) for item in data["static_obstacles"]),
            validation=dict(data["validation"]),
        )
        result.validate()
        return result

    @classmethod
    def from_json(cls, path: str | Path) -> "NavigationRoom":
        return cls.from_dict(load_json(path))

    def validate(self) -> None:
        if self.schema_version != 1:
            raise ValueError("unsupported navigation-room schema")
        if self.meters_per_unit != 1.0 or self.up_axis != "Z":
            raise ValueError("navigation room must use meters and Z-up")
        room_size = _finite_tuple(self.room["size_m"], 3, "room size")
        if any(value <= 0.0 for value in room_size):
            raise ValueError("room dimensions must be positive")
        if int(self.room.get("logical_wall_count", 0)) != 4:
            raise ValueError("P1 navigation room requires four logical walls")
        if int(self.room.get("wall_collider_count", 0)) != 6:
            raise ValueError("entry opening requires six wall collider segments")
        door_width = float(self.room.get("door_width_m", 0.0))
        door_height = float(self.room.get("door_height_m", 0.0))
        if not 0.9 <= door_width < room_size[1] or not 2.0 <= door_height < room_size[2]:
            raise ValueError("navigation-room doorway is not G1-compatible")
        if not 0.25 <= self.robot_clearance_radius_m <= 0.60:
            raise ValueError("robot clearance radius is outside the supported range")

        station_ids = tuple(station.id for station in self.workstations)
        if len(self.workstations) < 3 or len(station_ids) != len(set(station_ids)):
            raise ValueError("navigation room requires at least three unique workstations")
        targets = [station for station in self.workstations if station.target]
        if len(targets) != 1 or targets[0].detail_level != "full_interaction":
            raise ValueError("exactly one full-interaction target workstation is required")
        if any(
            station.detail_level != "navigation_static"
            for station in self.workstations
            if not station.target
        ):
            raise ValueError("non-target workstations must use navigation-static detail")

        spawn_ids: set[str] = set()
        half_x, half_y = room_size[0] / 2.0, room_size[1] / 2.0
        for spawn in self.spawn_points:
            spawn_id = str(spawn["id"])
            pose = _finite_tuple(spawn["xy_yaw"], 3, "spawn pose")
            if not spawn_id or spawn_id in spawn_ids:
                raise ValueError("spawn ids must be unique and non-empty")
            spawn_ids.add(spawn_id)
            if abs(pose[0]) >= half_x or abs(pose[1]) >= half_y:
                raise ValueError("spawn pose must be inside the room")
        if len(spawn_ids) < 2:
            raise ValueError("navigation room requires multiple spawn points")

        obstacle_ids: set[str] = set()
        for obstacle in self.static_obstacles:
            obstacle_id = str(obstacle["id"])
            center = _finite_tuple(obstacle["center_m"], 3, "obstacle center")
            size = _finite_tuple(obstacle["size_m"], 3, "obstacle size")
            if not obstacle_id or obstacle_id in obstacle_ids:
                raise ValueError("obstacle ids must be unique and non-empty")
            obstacle_ids.add(obstacle_id)
            if any(value <= 0.0 for value in size):
                raise ValueError("obstacle size must be positive")
            if abs(center[0]) + size[0] / 2.0 >= half_x or abs(center[1]) + size[1] / 2.0 >= half_y:
                raise ValueError("obstacle must fit inside the room")

        required_paths = tuple(self.validation.get("required_prim_paths", ()))
        if not required_paths or len(required_paths) != len(set(required_paths)):
            raise ValueError("navigation-room required prim paths must be unique")

    @property
    def target_workstation(self) -> NavigationWorkstation:
        return next(station for station in self.workstations if station.target)

    def summary(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "room_size_m": list(self.room["size_m"]),
            "logical_wall_count": int(self.room["logical_wall_count"]),
            "wall_collider_count": int(self.room["wall_collider_count"]),
            "door_width_m": float(self.room["door_width_m"]),
            "workstation_ids": [station.id for station in self.workstations],
            "workstation_count": len(self.workstations),
            "target_workstation_id": self.target_workstation.id,
            "spawn_count": len(self.spawn_points),
            "obstacle_count": len(self.static_obstacles),
            "anchor_count": len(self.spawn_points) + 3 * len(self.workstations),
        }
