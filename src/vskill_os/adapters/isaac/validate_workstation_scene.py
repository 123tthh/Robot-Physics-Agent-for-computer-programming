"""Validate the authored P0/P1 workstation USD with the local OpenUSD runtime."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import time
from typing import Any

from vskill_os.simulation.navigation_assets import NavigationRoom
from vskill_os.simulation.workstation_assets import WorkstationAssetChain


def _parse_args() -> argparse.Namespace:
    root = Path(__file__).resolve().parents[4]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scene-usd",
        type=Path,
        default=root / "artifacts/isaac-p1/scenes/p1_g1_dex3_keyboard.usda",
    )
    parser.add_argument(
        "--asset-chain",
        type=Path,
        default=root / "configs/simulation/p0_p1_workstation_asset_chain.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=root / "reports/isaac_p1_workstation_validation.json",
    )
    parser.add_argument(
        "--navigation-room",
        type=Path,
        default=root / "configs/simulation/p1_navigation_room.json",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    from pxr import Usd, UsdGeom, UsdPhysics

    scene_path = args.scene_usd.resolve()
    chain = WorkstationAssetChain.from_json(args.asset_chain.resolve())
    navigation_room = NavigationRoom.from_json(args.navigation_room.resolve())
    stage = Usd.Stage.Open(str(scene_path)) if scene_path.is_file() else None
    checks: dict[str, bool] = {"scene_exists": scene_path.is_file(), "stage_opens": stage is not None}
    details: dict[str, Any] = {}
    errors: list[str] = []
    if stage is None:
        errors.append("USD stage does not exist or cannot be opened")
    else:
        default_prim = stage.GetDefaultPrim()
        prims = list(stage.Traverse())

        def subtree_has_collision(prim: Any) -> bool:
            return prim.IsValid() and any(
                candidate.HasAPI(UsdPhysics.CollisionAPI)
                for candidate in Usd.PrimRange(prim)
            )

        required_paths = tuple(chain.validation["required_prim_paths"])
        missing_paths = [path for path in required_paths if not stage.GetPrimAtPath(path).IsValid()]
        chair_prims = [
            prim
            for prim in prims
            if prim.GetPath().pathString.startswith("/World/MobileChair/")
        ]
        rigid_bodies = [prim for prim in chair_prims if prim.HasAPI(UsdPhysics.RigidBodyAPI)]
        joints = [prim for prim in chair_prims if prim.IsA(UsdPhysics.RevoluteJoint)]
        swivel_joints = [prim for prim in joints if prim.GetName().endswith("_Swivel")]
        rolling_joints = [prim for prim in joints if prim.GetName().endswith("_Rolling")]
        swivel_friction = [
            float(prim.GetAttribute("physxJoint:jointFriction").Get())
            for prim in swivel_joints
        ]
        rolling_friction = [
            float(prim.GetAttribute("physxJoint:jointFriction").Get())
            for prim in rolling_joints
        ]
        rolling_max_velocity = [
            float(prim.GetAttribute("physxJoint:maxJointVelocity").Get())
            for prim in rolling_joints
        ]
        fixed_joints = [prim for prim in prims if prim.IsA(UsdPhysics.FixedJoint)]
        chair_fixed_joint_paths: list[str] = []
        for prim in fixed_joints:
            joint = UsdPhysics.Joint(prim)
            targets = joint.GetBody0Rel().GetTargets() + joint.GetBody1Rel().GetTargets()
            if any(str(target).startswith("/World/MobileChair") for target in targets):
                chair_fixed_joint_paths.append(prim.GetPath().pathString)
        broken_relationships: list[str] = []
        for prim in joints:
            joint = UsdPhysics.Joint(prim)
            for relationship in (joint.GetBody0Rel(), joint.GetBody1Rel()):
                targets = relationship.GetTargets()
                if len(targets) != 1 or not stage.GetPrimAtPath(targets[0]).IsValid():
                    broken_relationships.append(prim.GetPath().pathString)
        stage_markers = [
            stage.GetPrimAtPath(f"/World/TaskStages/{stage_id}")
            for stage_id in chain.stage_order
        ]
        marker_metadata_complete = all(
            prim.IsValid()
            and prim.GetCustomDataByKey("vskill:stage") == stage_id
            and bool(prim.GetCustomDataByKey("vskill:requiredEvidence"))
            for prim, stage_id in zip(stage_markers, chain.stage_order, strict=True)
        )
        contact_prims = [
            prim
            for prim in chair_prims
            if prim.GetCustomDataByKey("vskill:contactSurface")
            in {"seat", "backrest", *(f"caster_{index}" for index in range(5))}
        ]
        up_axis = UsdGeom.GetStageUpAxis(stage)
        meters_per_unit = UsdGeom.GetStageMetersPerUnit(stage)
        navigation_required_paths = tuple(
            navigation_room.validation["required_prim_paths"]
        )
        missing_navigation_paths = [
            path
            for path in navigation_required_paths
            if not stage.GetPrimAtPath(path).IsValid()
        ]
        wall_prims = [
            stage.GetPrimAtPath(
                f"/World/Navigation/Room/{name}"
            )
            for name in (
                "WallWestSouth",
                "WallWestNorth",
                "WallWestHeader",
                "WallEast",
                "WallSouth",
                "WallNorth",
            )
        ]
        station_prims = [
            stage.GetPrimAtPath(
                f"/World/Navigation/Workstations/Workstation_{station.id}"
            )
            for station in navigation_room.workstations
        ]
        station_ids = [
            prim.GetCustomDataByKey("vskill:workstationId") for prim in station_prims
        ]
        expected_anchor_paths = [
            f"/World/Navigation/Anchors/Spawn_{spawn['id']}"
            for spawn in navigation_room.spawn_points
        ]
        for station in navigation_room.workstations:
            expected_anchor_paths.extend(
                (
                    f"/World/Navigation/Anchors/Workstation_{station.id}_Approach",
                    f"/World/Navigation/Anchors/Workstation_{station.id}_ChairAlignment",
                    f"/World/Navigation/Anchors/Workstation_{station.id}_WorkPose",
                )
            )
        anchor_prims = [stage.GetPrimAtPath(path) for path in expected_anchor_paths]
        obstacle_prims = [
            stage.GetPrimAtPath(f"/World/Navigation/Obstacles/{obstacle['id']}")
            for obstacle in navigation_room.static_obstacles
        ]
        checks.update(
            {
                "default_prim_world": default_prim.IsValid()
                and default_prim.GetPath().pathString == "/World",
                "z_up": up_axis == UsdGeom.Tokens.z,
                "meters_per_unit": meters_per_unit == 1.0,
                "required_prims_present": not missing_paths,
                "chair_rigid_body_count": len(rigid_bodies) == 11,
                "chair_revolute_joint_count": len(joints) == 10,
                "chair_swivel_joint_count": len(swivel_joints) == 5,
                "chair_rolling_joint_count": len(rolling_joints) == 5,
                "joint_relationships_resolve": not broken_relationships,
                "stage_markers_complete": len(stage_markers) == 6
                and marker_metadata_complete,
                "chair_contact_surfaces_complete": len(contact_prims) == 7,
                "chair_root_free": not chair_fixed_joint_paths
                and stage.GetPrimAtPath("/World/MobileChair").GetCustomDataByKey(
                    "vskill:worldFixed"
                )
                is False,
                "chair_seat_height_matches_config": abs(
                    float(
                        stage.GetPrimAtPath("/World/MobileChair").GetCustomDataByKey(
                            "vskill:seatTopHeightM"
                        )
                    )
                    - (
                        float(chain.chair["seat_center_z_m"])
                        + float(chain.chair["seat_size_m"][2]) / 2.0
                    )
                )
                < 1.0e-6,
                "chair_sliding_dominant_configuration": stage.GetPrimAtPath(
                    "/World/MobileChair"
                ).GetCustomDataByKey("vskill:mobilityMode")
                == "sliding_dominant"
                and all(
                    abs(value - float(chain.chair["swivel_joint_friction"]))
                    < 1.0e-6
                    for value in swivel_friction
                )
                and all(
                    abs(value - float(chain.chair["rolling_joint_friction"]))
                    < 1.0e-6
                    for value in rolling_friction
                )
                and all(
                    abs(
                        value
                        - float(chain.chair["rolling_joint_max_velocity_deg_s"])
                    )
                    < 1.0e-6
                    for value in rolling_max_velocity
                ),
                "desk_supports_present": all(
                    stage.GetPrimAtPath(f"/World/Workstation/DeskLeg_{index}").IsValid()
                    for index in range(2)
                ),
                "display_stand_present": stage.GetPrimAtPath(
                    "/World/Workstation/DisplayStand"
                ).IsValid(),
                "navigation_required_prims_present": not missing_navigation_paths,
                "navigation_four_wall_shell": len(wall_prims)
                == int(navigation_room.room["wall_collider_count"])
                and all(subtree_has_collision(prim) for prim in wall_prims)
                and stage.GetPrimAtPath(
                    "/World/Navigation/Room"
                ).GetCustomDataByKey("vskill:logicalWallCount")
                == 4,
                "navigation_workstations_unique": station_ids
                == [station.id for station in navigation_room.workstations]
                and len(station_ids) == len(set(station_ids)),
                "navigation_target_workstation": sum(
                    bool(prim.GetCustomDataByKey("vskill:navigationTarget"))
                    for prim in station_prims
                )
                == 1,
                "navigation_anchors_complete": len(anchor_prims)
                == navigation_room.summary()["anchor_count"]
                and all(
                    prim.IsValid()
                    and bool(prim.GetCustomDataByKey("vskill:navigationRole"))
                    for prim in anchor_prims
                ),
                "navigation_obstacles_collidable": len(obstacle_prims)
                == len(navigation_room.static_obstacles)
                and all(subtree_has_collision(prim) for prim in obstacle_prims),
            }
        )
        details = {
            "default_prim": default_prim.GetPath().pathString if default_prim.IsValid() else None,
            "up_axis": str(up_axis),
            "meters_per_unit": meters_per_unit,
            "prim_count": len(prims),
            "used_layer_count": len(stage.GetUsedLayers()),
            "chair_rigid_body_paths": [prim.GetPath().pathString for prim in rigid_bodies],
            "chair_joint_paths": [prim.GetPath().pathString for prim in joints],
            "chair_contact_paths": [prim.GetPath().pathString for prim in contact_prims],
            "stage_order": list(chain.stage_order),
            "missing_required_paths": missing_paths,
            "broken_joint_relationships": sorted(set(broken_relationships)),
            "chair_fixed_joint_paths": chair_fixed_joint_paths,
            "chair_seat_top_height_m": float(
                stage.GetPrimAtPath("/World/MobileChair").GetCustomDataByKey(
                    "vskill:seatTopHeightM"
                )
            ),
            "chair_swivel_joint_friction": swivel_friction,
            "chair_rolling_joint_friction": rolling_friction,
            "chair_rolling_joint_max_velocity_deg_s": rolling_max_velocity,
            "navigation_wall_paths": [
                prim.GetPath().pathString for prim in wall_prims
            ],
            "navigation_workstation_ids": station_ids,
            "navigation_anchor_paths": expected_anchor_paths,
            "navigation_obstacle_paths": [
                prim.GetPath().pathString for prim in obstacle_prims
            ],
            "missing_navigation_paths": missing_navigation_paths,
        }
        errors.extend(name for name, passed in checks.items() if not passed)

    report = {
        "schema_version": 1,
        "phase": "P1",
        "validator": "vskill_openusd_workstation_topology",
        "status": "passed" if all(checks.values()) else "failed",
        "generated_at_unix": time.time(),
        "scene_usd": str(scene_path),
        "asset_chain_config": str(args.asset_chain.resolve()),
        "navigation_room_config": str(args.navigation_room.resolve()),
        "checks": checks,
        "details": details,
        "errors": errors,
        "formal_simready_conformance": "not_claimed",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
