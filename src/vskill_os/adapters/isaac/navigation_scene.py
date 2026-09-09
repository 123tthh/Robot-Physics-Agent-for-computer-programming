"""Procedural room shell and multi-workstation navigation assets."""

from __future__ import annotations

import math
from typing import Any


def author_navigation_environment(
    *, stage: Any, sim_utils: Any, room: Any, asset_chain: Any
) -> dict[str, Any]:
    """Author a four-wall room, station proxies, anchors, and obstacles."""

    from pxr import Gf, Sdf, UsdGeom

    room_cfg = room.room
    room_size = tuple(float(value) for value in room_cfg["size_m"])
    width, depth, height = room_size
    wall_t = float(room_cfg["wall_thickness_m"])
    floor_t = float(room_cfg["floor_thickness_m"])
    door_y = float(room_cfg["door_center_y_m"])
    door_w = float(room_cfg["door_width_m"])
    door_h = float(room_cfg["door_height_m"])

    floor_material = sim_utils.RigidBodyMaterialCfg(
        static_friction=0.8,
        dynamic_friction=0.7,
        restitution=0.0,
    )
    ground_cfg = sim_utils.CuboidCfg(
        size=(width, depth, floor_t),
        collision_props=sim_utils.CollisionPropertiesCfg(),
        physics_material=floor_material,
        visual_material=sim_utils.PreviewSurfaceCfg(
            diffuse_color=(0.28, 0.30, 0.33), roughness=0.78
        ),
    )
    ground_cfg.func("/World/Ground", ground_cfg, translation=(0.0, 0.0, -floor_t / 2.0))

    sim_utils.create_prim("/World/Navigation", "Xform")
    sim_utils.create_prim("/World/Navigation/Room", "Xform")
    sim_utils.create_prim("/World/Navigation/Workstations", "Xform")
    sim_utils.create_prim("/World/Navigation/Anchors", "Xform")
    sim_utils.create_prim("/World/Navigation/Obstacles", "Xform")
    room_prim = stage.GetPrimAtPath("/World/Navigation/Room")
    room_prim.SetCustomDataByKey("vskill:assetType", "simple_navigation_room")
    room_prim.SetCustomDataByKey("vskill:logicalWallCount", 4)
    room_prim.SetCustomDataByKey("vskill:doorWidthM", door_w)

    wall_material = sim_utils.PreviewSurfaceCfg(
        diffuse_color=(0.72, 0.76, 0.80), roughness=0.82
    )
    wall_specs: list[tuple[str, tuple[float, float, float], tuple[float, float, float]]] = []
    west_segment = (depth - door_w) / 2.0
    wall_specs.extend(
        (
            (
                "WallWestSouth",
                (wall_t, west_segment, height),
                (-width / 2.0, door_y - door_w / 2.0 - west_segment / 2.0, height / 2.0),
            ),
            (
                "WallWestNorth",
                (wall_t, west_segment, height),
                (-width / 2.0, door_y + door_w / 2.0 + west_segment / 2.0, height / 2.0),
            ),
            (
                "WallWestHeader",
                (wall_t, door_w, height - door_h),
                (-width / 2.0, door_y, door_h + (height - door_h) / 2.0),
            ),
            ("WallEast", (wall_t, depth, height), (width / 2.0, 0.0, height / 2.0)),
            ("WallSouth", (width, wall_t, height), (0.0, -depth / 2.0, height / 2.0)),
            ("WallNorth", (width, wall_t, height), (0.0, depth / 2.0, height / 2.0)),
        )
    )
    wall_paths: list[str] = []
    for name, size, center in wall_specs:
        path = f"/World/Navigation/Room/{name}"
        cfg = sim_utils.CuboidCfg(
            size=size,
            collision_props=sim_utils.CollisionPropertiesCfg(),
            visual_material=wall_material,
        )
        cfg.func(path, cfg, translation=center)
        prim = stage.GetPrimAtPath(path)
        prim.SetCustomDataByKey("vskill:logicalBoundary", "west" if name.startswith("WallWest") else name[4:].lower())
        wall_paths.append(path)

    station_paths: list[str] = []
    anchor_paths: list[str] = []
    for station in room.workstations:
        root_path = f"/World/Navigation/Workstations/Workstation_{station.id}"
        sim_utils.create_prim(root_path, "Xform", translation=station.offset_m)
        root = stage.GetPrimAtPath(root_path)
        root.SetCustomDataByKey("vskill:workstationId", station.id)
        root.SetCustomDataByKey("vskill:navigationTarget", station.target)
        root.SetCustomDataByKey("vskill:detailLevel", station.detail_level)
        if station.target:
            root.CreateRelationship("vskill:assetRoots").SetTargets(
                [
                    Sdf.Path("/World/Workstation"),
                    Sdf.Path("/World/Keyboard"),
                    Sdf.Path("/World/MobileChair"),
                ]
            )
            stage.GetPrimAtPath("/World/Workstation").SetCustomDataByKey(
                "vskill:workstationId", station.id
            )
            stage.GetPrimAtPath("/World/Keyboard").SetCustomDataByKey(
                "vskill:workstationId", station.id
            )
            stage.GetPrimAtPath("/World/MobileChair").SetCustomDataByKey(
                "vskill:workstationId", station.id
            )
        else:
            _author_static_workstation_proxy(
                root_path=root_path,
                stage=stage,
                sim_utils=sim_utils,
                asset_chain=asset_chain,
                station_id=station.id,
            )
        station_paths.append(root_path)

        anchor_specs = (
            ("Approach", station.approach_xy_yaw, "workstation_approach"),
            ("ChairAlignment", station.chair_alignment_xy_yaw, "chair_alignment"),
            ("WorkPose", station.work_pose_xy_yaw, "work_pose"),
        )
        for suffix, pose, role in anchor_specs:
            anchor_path = f"/World/Navigation/Anchors/Workstation_{station.id}_{suffix}"
            _author_anchor(
                stage=stage,
                path=anchor_path,
                pose=pose,
                role=role,
                station_id=station.id,
                color=(0.10, 0.72, 0.95) if station.target else (0.52, 0.58, 0.65),
            )
            anchor_paths.append(anchor_path)

    spawn_paths: list[str] = []
    for spawn in room.spawn_points:
        path = f"/World/Navigation/Anchors/Spawn_{spawn['id']}"
        _author_anchor(
            stage=stage,
            path=path,
            pose=spawn["xy_yaw"],
            role="robot_spawn",
            station_id="",
            color=(0.20, 0.88, 0.38),
        )
        spawn_paths.append(path)
        anchor_paths.append(path)

    obstacle_paths: list[str] = []
    for obstacle in room.static_obstacles:
        path = f"/World/Navigation/Obstacles/{obstacle['id']}"
        cfg = sim_utils.CuboidCfg(
            size=tuple(float(value) for value in obstacle["size_m"]),
            collision_props=sim_utils.CollisionPropertiesCfg(),
            visual_material=sim_utils.PreviewSurfaceCfg(
                diffuse_color=(0.36, 0.22, 0.12), roughness=0.72
            ),
        )
        cfg.func(
            path,
            cfg,
            translation=tuple(float(value) for value in obstacle["center_m"]),
        )
        prim = stage.GetPrimAtPath(path)
        prim.SetCustomDataByKey("vskill:obstacleId", str(obstacle["id"]))
        prim.SetCustomDataByKey("vskill:obstacleClass", "static_navigation_obstacle")
        obstacle_paths.append(path)

    return {
        "room_prim": "/World/Navigation/Room",
        "wall_paths": wall_paths,
        "station_paths": station_paths,
        "anchor_paths": anchor_paths,
        "spawn_paths": spawn_paths,
        "obstacle_paths": obstacle_paths,
        "target_workstation_id": room.target_workstation.id,
    }


def _author_static_workstation_proxy(
    *, root_path: str, stage: Any, sim_utils: Any, asset_chain: Any, station_id: str
) -> None:
    workstation = asset_chain.workstation
    chair = asset_chain.chair
    desk_center = tuple(float(value) for value in workstation["desk_top_center_m"])
    desk_size = tuple(float(value) for value in workstation["desk_top_size_m"])
    furniture = sim_utils.PreviewSurfaceCfg(
        diffuse_color=(0.24, 0.27, 0.31), roughness=0.58
    )
    dark = sim_utils.PreviewSurfaceCfg(
        diffuse_color=(0.06, 0.08, 0.10), roughness=0.50
    )

    def cuboid(name: str, size: tuple[float, float, float], center: tuple[float, float, float], material: Any) -> None:
        path = f"{root_path}/{name}"
        cfg = sim_utils.CuboidCfg(
            size=size,
            collision_props=sim_utils.CollisionPropertiesCfg(),
            visual_material=material,
        )
        cfg.func(path, cfg, translation=center)

    cuboid("Desk", desk_size, desk_center, furniture)
    leg_height = float(workstation["desk_leg_height_m"])
    for index, y in enumerate((-0.48, 0.48)):
        cuboid(
            f"DeskLeg_{index}",
            (0.055, 0.055, leg_height),
            (desk_center[0] + 0.23, y, leg_height / 2.0),
            dark,
        )
    display_center = tuple(float(value) for value in workstation["display_center_m"])
    cuboid(
        "Display",
        tuple(float(value) for value in workstation["display_size_m"]),
        display_center,
        dark,
    )
    cuboid("DisplayStand", (0.035, 0.055, 0.30), (display_center[0], 0.0, 0.84), dark)
    keyboard_center = tuple(float(value) for value in workstation["keyboard_center_m"])
    cuboid("KeyboardShell", (0.12, 0.34, 0.025), keyboard_center, dark)

    seat_z = float(chair["seat_center_z_m"])
    sim_utils.create_prim(f"{root_path}/Chair", "Xform")
    cuboid(
        "Chair/Seat",
        tuple(float(value) for value in chair["seat_size_m"]),
        (-0.36, 0.0, seat_z),
        dark,
    )
    back_size = tuple(float(value) for value in chair["backrest_size_m"])
    cuboid("Chair/Backrest", back_size, (-0.565, 0.0, float(chair["backrest_center_m"][2])), dark)
    cuboid("Chair/Column", (0.07, 0.07, 0.34), (-0.36, 0.0, 0.25), furniture)
    for index in range(5):
        yaw = index * 2.0 * math.pi / 5.0
        cuboid(
            f"Chair/Caster_{index}",
            (0.18, 0.045, 0.045),
            (-0.36 + 0.18 * math.cos(yaw), 0.18 * math.sin(yaw), 0.05),
            dark,
        )
    stage.GetPrimAtPath(root_path).SetCustomDataByKey("vskill:workstationId", station_id)


def _author_anchor(
    *,
    stage: Any,
    path: str,
    pose: Any,
    role: str,
    station_id: str,
    color: tuple[float, float, float],
) -> None:
    from pxr import Gf, Sdf, UsdGeom, UsdShade

    x, y, yaw = (float(value) for value in pose)
    anchor = UsdGeom.Xform.Define(stage, path)
    anchor.AddTranslateOp().Set(Gf.Vec3d(x, y, 0.0))
    anchor.AddOrientOp().Set(
        Gf.Quatf(math.cos(yaw / 2.0), 0.0, 0.0, math.sin(yaw / 2.0))
    )
    anchor.GetPrim().SetCustomDataByKey("vskill:navigationRole", role)
    anchor.GetPrim().SetCustomDataByKey("vskill:workstationId", station_id)
    marker = UsdGeom.Cylinder.Define(stage, f"{path}/Marker")
    marker.CreateAxisAttr(UsdGeom.Tokens.z)
    marker.CreateRadiusAttr(0.09)
    marker.CreateHeightAttr(0.003)
    marker.AddTranslateOp().Set(Gf.Vec3d(0.0, 0.0, 0.002))
    material_path = path.replace("/World/Navigation/Anchors/", "/World/Looks/Nav_")
    material = UsdShade.Material.Define(stage, material_path)
    shader = UsdShade.Shader.Define(stage, f"{material_path}/PreviewSurface")
    shader.CreateIdAttr("UsdPreviewSurface")
    shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*color))
    shader.CreateInput("emissiveColor", Sdf.ValueTypeNames.Color3f).Set(
        Gf.Vec3f(*(0.25 * value for value in color))
    )
    shader.CreateOutput("surface", Sdf.ValueTypeNames.Token)
    material.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")
    UsdShade.MaterialBindingAPI.Apply(marker.GetPrim()).Bind(material)
