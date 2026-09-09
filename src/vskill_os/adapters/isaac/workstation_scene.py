"""Procedural SimReady-style P0/P1 workstation and mobile-chair authoring."""

from __future__ import annotations

import math
from typing import Any


def author_workstation_asset_chain(*, stage: Any, sim_utils: Any, chain: Any) -> dict[str, Any]:
    """Author visible workstation stages and a passive five-caster chair.

    Isaac/pxr imports intentionally stay inside this function so the core
    package remains importable without the optional simulator runtime.
    """

    from pxr import Gf, PhysxSchema, Sdf, UsdGeom, UsdPhysics, UsdShade

    workstation = chain.workstation
    chair = chain.chair
    static_material = sim_utils.RigidBodyMaterialCfg(
        static_friction=0.8,
        dynamic_friction=0.7,
        restitution=0.0,
    )

    sim_utils.create_prim("/World/Workstation", "Xform")
    desk_center = tuple(float(value) for value in workstation["desk_top_center_m"])
    desk_size = tuple(float(value) for value in workstation["desk_top_size_m"])
    desk_cfg = sim_utils.CuboidCfg(
        size=desk_size,
        collision_props=sim_utils.CollisionPropertiesCfg(),
        physics_material=static_material,
        visual_material=sim_utils.PreviewSurfaceCfg(diffuse_color=(0.20, 0.22, 0.25)),
    )
    desk_cfg.func("/World/Workstation/Desk", desk_cfg, translation=desk_center)

    leg_height = float(workstation["desk_leg_height_m"])
    leg_cfg = sim_utils.CuboidCfg(
        size=(0.055, 0.055, leg_height),
        collision_props=sim_utils.CollisionPropertiesCfg(),
        physics_material=static_material,
        visual_material=sim_utils.PreviewSurfaceCfg(diffuse_color=(0.11, 0.12, 0.14), metallic=0.35),
    )
    leg_x = desk_center[0] + 0.23
    for index, y in enumerate((-0.48, 0.48)):
        leg_cfg.func(
            f"/World/Workstation/DeskLeg_{index}",
            leg_cfg,
            translation=(leg_x, y, leg_height / 2.0),
        )

    display_center = tuple(float(value) for value in workstation["display_center_m"])
    display_size = tuple(float(value) for value in workstation["display_size_m"])
    display_cfg = sim_utils.CuboidCfg(
        size=display_size,
        collision_props=sim_utils.CollisionPropertiesCfg(),
        visual_material=sim_utils.PreviewSurfaceCfg(
            diffuse_color=(0.035, 0.075, 0.10), metallic=0.2
        ),
    )
    display_cfg.func(
        "/World/Workstation/Display", display_cfg, translation=display_center
    )
    stand_cfg = sim_utils.CuboidCfg(
        size=(0.035, 0.055, 0.30),
        collision_props=sim_utils.CollisionPropertiesCfg(),
        visual_material=sim_utils.PreviewSurfaceCfg(
            diffuse_color=(0.09, 0.10, 0.12), metallic=0.45
        ),
    )
    stand_cfg.func(
        "/World/Workstation/DisplayStand",
        stand_cfg,
        translation=(display_center[0], 0.0, 0.84),
    )
    base_cfg = sim_utils.CuboidCfg(
        size=(0.18, 0.28, 0.025),
        collision_props=sim_utils.CollisionPropertiesCfg(),
        visual_material=sim_utils.PreviewSurfaceCfg(
            diffuse_color=(0.09, 0.10, 0.12), metallic=0.45
        ),
    )
    base_cfg.func(
        "/World/Workstation/DisplayBase",
        base_cfg,
        translation=(0.99, 0.0, desk_center[2] + desk_size[2] / 2.0 + 0.0125),
    )

    sim_utils.create_prim("/World/TaskStages", "Xform")
    stage_colors = (
        (0.15, 0.55, 0.95),
        (0.10, 0.80, 0.75),
        (0.95, 0.72, 0.10),
        (0.95, 0.42, 0.12),
        (0.65, 0.35, 0.95),
        (0.20, 0.85, 0.35),
    )
    marker_paths: list[str] = []
    for index, asset_stage in enumerate(chain.stages):
        marker_path = f"/World/TaskStages/{asset_stage.id}"
        marker = UsdGeom.Cylinder.Define(stage, marker_path)
        marker.CreateAxisAttr(UsdGeom.Tokens.z)
        marker.CreateRadiusAttr(0.15 + (0.07 if asset_stage.id == "seated_stabilization" else 0.0))
        marker.CreateHeightAttr(0.004)
        marker.AddTranslateOp().Set(
            Gf.Vec3d(asset_stage.robot_xy_yaw[0], asset_stage.robot_xy_yaw[1], 0.003 + index * 0.0002)
        )
        marker.GetPrim().SetCustomDataByKey("vskill:stage", asset_stage.id)
        marker.GetPrim().SetCustomDataByKey("vskill:controlPhase", asset_stage.control_phase)
        marker.GetPrim().SetCustomDataByKey(
            "vskill:requiredEvidence", ",".join(asset_stage.required_evidence)
        )
        material = _define_preview_material(
            stage,
            f"/World/Looks/Stage_{asset_stage.id}",
            stage_colors[index],
            opacity=1.0,
            emissive=True,
        )
        UsdShade.MaterialBindingAPI.Apply(marker.GetPrim()).Bind(material)
        marker_paths.append(marker_path)

    sim_utils.create_prim("/World/MobileChair", "Xform")
    sim_utils.create_prim("/World/MobileChair/Bodies", "Xform")
    sim_utils.create_prim("/World/MobileChair/Joints", "Xform")
    chair_root = stage.GetPrimAtPath("/World/MobileChair")
    chair_root.SetCustomDataByKey("vskill:assetType", chair["asset_type"])
    chair_root.SetCustomDataByKey("vskill:passive", True)
    chair_root.SetCustomDataByKey("vskill:worldFixed", False)
    chair_root.SetCustomDataByKey("vskill:mobilityIntent", "passive_contact_driven")
    chair_root.SetCustomDataByKey("vskill:mobilityMode", "sliding_dominant")
    UsdPhysics.ArticulationRootAPI.Apply(chair_root)
    PhysxSchema.PhysxArticulationAPI.Apply(chair_root).CreateEnabledSelfCollisionsAttr().Set(False)

    initial = tuple(float(value) for value in chair["initial_center_m"])
    frame_path = "/World/MobileChair/Bodies/Frame"
    frame = UsdGeom.Xform.Define(stage, frame_path)
    frame.AddTranslateOp().Set(Gf.Vec3d(*initial))
    UsdPhysics.RigidBodyAPI.Apply(frame.GetPrim())
    UsdPhysics.MassAPI.Apply(frame.GetPrim()).CreateMassAttr().Set(float(chair["frame_mass_kg"]))
    frame.GetPrim().SetCustomDataByKey("vskill:contactSurface", "frame")

    upholstery = _define_preview_material(
        stage, "/World/Looks/ChairUpholstery", (0.075, 0.11, 0.16)
    )
    metal = _define_preview_material(
        stage, "/World/Looks/ChairMetal", (0.16, 0.18, 0.21), metallic=0.75
    )
    rubber = _define_preview_material(
        stage, "/World/Looks/ChairWheelRubber", (0.025, 0.028, 0.032)
    )
    rubber_physics = UsdPhysics.MaterialAPI.Apply(rubber.GetPrim())
    rubber_physics.CreateStaticFrictionAttr().Set(float(chair["wheel_static_friction"]))
    rubber_physics.CreateDynamicFrictionAttr().Set(float(chair["wheel_dynamic_friction"]))
    rubber_physics.CreateRestitutionAttr().Set(0.0)
    seat_size = tuple(float(value) for value in chair["seat_size_m"])
    seat_center_z = float(chair["seat_center_z_m"])
    seat_top_height = seat_center_z + seat_size[2] / 2.0
    chair_root.SetCustomDataByKey("vskill:seatTopHeightM", seat_top_height)
    seat = _add_cube(
        stage,
        f"{frame_path}/Seat",
        size=seat_size,
        translation=(0.0, 0.0, seat_center_z),
        material=upholstery,
        collision=True,
    )
    seat.SetCustomDataByKey("vskill:contactSurface", "seat")
    back_center = tuple(float(value) for value in chair["backrest_center_m"])
    back_size = tuple(float(value) for value in chair["backrest_size_m"])
    backrest = _add_cube(
        stage,
        f"{frame_path}/Backrest",
        size=back_size,
        translation=back_center,
        material=upholstery,
        collision=True,
    )
    backrest.SetCustomDataByKey("vskill:contactSurface", "backrest")
    seat_bottom = seat_center_z - seat_size[2] / 2.0
    column_bottom = 0.11
    column_height = seat_bottom - column_bottom
    _add_cylinder(
        stage,
        f"{frame_path}/Column",
        radius=0.035,
        height=column_height,
        axis=UsdGeom.Tokens.z,
        translation=(0.0, 0.0, (seat_bottom + column_bottom) / 2.0),
        material=metal,
        collision=True,
    )
    _add_cylinder(
        stage,
        f"{frame_path}/Hub",
        radius=0.07,
        height=0.055,
        axis=UsdGeom.Tokens.z,
        translation=(0.0, 0.0, 0.085),
        material=metal,
        collision=True,
    )

    caster_radius = float(chair["caster_radius_from_center_m"])
    wheel_radius = float(chair["wheel_radius_m"])
    wheel_width = float(chair["wheel_width_m"])
    swivel_paths: list[str] = []
    wheel_joint_paths: list[str] = []
    contact_paths = [seat.GetPath().pathString, backrest.GetPath().pathString]
    for index in range(int(chair["caster_count"])):
        yaw = index * 2.0 * math.pi / int(chair["caster_count"])
        radial = (math.cos(yaw), math.sin(yaw))
        midpoint = (caster_radius * 0.52 * radial[0], caster_radius * 0.52 * radial[1], 0.085)
        _add_cube(
            stage,
            f"{frame_path}/Leg_{index}",
            size=(caster_radius, 0.045, 0.035),
            translation=midpoint,
            yaw_rad=yaw,
            material=metal,
            collision=True,
        )

        caster_x = initial[0] + caster_radius * radial[0]
        caster_y = initial[1] + caster_radius * radial[1]
        caster_z = wheel_radius + 0.010
        fork_path = f"/World/MobileChair/Bodies/Caster_{index}_Fork"
        fork = UsdGeom.Xform.Define(stage, fork_path)
        fork.AddTranslateOp().Set(Gf.Vec3d(caster_x, caster_y, caster_z))
        fork.AddOrientOp().Set(
            Gf.Quatf(math.cos(yaw / 2.0), 0.0, 0.0, math.sin(yaw / 2.0))
        )
        UsdPhysics.RigidBodyAPI.Apply(fork.GetPrim())
        UsdPhysics.MassAPI.Apply(fork.GetPrim()).CreateMassAttr().Set(0.22)
        _add_cube(
            stage,
            f"{fork_path}/ForkVisual",
            size=(0.055, 0.045, 0.070),
            translation=(0.0, 0.0, 0.025),
            material=metal,
            collision=True,
        )

        wheel_path = f"/World/MobileChair/Bodies/Caster_{index}_Wheel"
        wheel = UsdGeom.Xform.Define(stage, wheel_path)
        wheel.AddTranslateOp().Set(Gf.Vec3d(caster_x, caster_y, caster_z))
        wheel.AddOrientOp().Set(
            Gf.Quatf(math.cos(yaw / 2.0), 0.0, 0.0, math.sin(yaw / 2.0))
        )
        UsdPhysics.RigidBodyAPI.Apply(wheel.GetPrim())
        UsdPhysics.MassAPI.Apply(wheel.GetPrim()).CreateMassAttr().Set(0.18)
        wheel_geom = _add_cylinder(
            stage,
            f"{wheel_path}/WheelVisual",
            radius=wheel_radius,
            height=wheel_width,
            axis=UsdGeom.Tokens.y,
            translation=(0.0, 0.0, 0.0),
            material=rubber,
            collision=True,
        )
        UsdShade.MaterialBindingAPI.Apply(wheel_geom).Bind(
            rubber, UsdShade.Tokens.weakerThanDescendants, "physics"
        )
        wheel_geom.SetCustomDataByKey("vskill:contactSurface", f"caster_{index}")
        contact_paths.append(wheel_geom.GetPath().pathString)

        swivel_path = f"/World/MobileChair/Joints/Caster_{index}_Swivel"
        swivel = UsdPhysics.RevoluteJoint.Define(stage, swivel_path)
        swivel.CreateBody0Rel().SetTargets([Sdf.Path(frame_path)])
        swivel.CreateBody1Rel().SetTargets([Sdf.Path(fork_path)])
        swivel.CreateLocalPos0Attr().Set(
            Gf.Vec3f(caster_radius * radial[0], caster_radius * radial[1], caster_z)
        )
        swivel.CreateLocalPos1Attr().Set(Gf.Vec3f(0.0))
        swivel.CreateAxisAttr().Set(UsdPhysics.Tokens.z)
        PhysxSchema.PhysxJointAPI.Apply(swivel.GetPrim()).CreateJointFrictionAttr().Set(
            float(chair["swivel_joint_friction"])
        )
        swivel_paths.append(swivel_path)

        rolling_path = f"/World/MobileChair/Joints/Caster_{index}_Rolling"
        rolling = UsdPhysics.RevoluteJoint.Define(stage, rolling_path)
        rolling.CreateBody0Rel().SetTargets([Sdf.Path(fork_path)])
        rolling.CreateBody1Rel().SetTargets([Sdf.Path(wheel_path)])
        rolling.CreateLocalPos0Attr().Set(Gf.Vec3f(0.0))
        rolling.CreateLocalPos1Attr().Set(Gf.Vec3f(0.0))
        rolling.CreateAxisAttr().Set(UsdPhysics.Tokens.y)
        rolling_physx = PhysxSchema.PhysxJointAPI.Apply(rolling.GetPrim())
        rolling_physx.CreateJointFrictionAttr().Set(
            float(chair["rolling_joint_friction"])
        )
        rolling_physx.CreateMaxJointVelocityAttr().Set(
            float(chair["rolling_joint_max_velocity_deg_s"])
        )
        wheel_joint_paths.append(rolling_path)

    return {
        "desk_prim": "/World/Workstation/Desk",
        "desk_leg_count": 2,
        "display_prim": "/World/Workstation/Display",
        "display_stand_prim": "/World/Workstation/DisplayStand",
        "stage_marker_paths": marker_paths,
        "chair_prim": "/World/MobileChair",
        "chair_frame_prim": frame_path,
        "chair_rigid_body_count": 1 + 2 * int(chair["caster_count"]),
        "chair_swivel_joint_paths": swivel_paths,
        "chair_wheel_joint_paths": wheel_joint_paths,
        "chair_contact_paths": contact_paths,
        "chair_initial_center_m": list(initial),
        "chair_working_center_m": list(chair["working_center_m"]),
        "chair_seat_top_height_m": seat_top_height,
        "chair_world_fixed": False,
    }


def _define_preview_material(
    stage: Any,
    path: str,
    color: tuple[float, float, float],
    *,
    metallic: float = 0.0,
    opacity: float = 1.0,
    emissive: bool = False,
) -> Any:
    from pxr import Gf, Sdf, UsdShade

    material = UsdShade.Material.Define(stage, path)
    shader = UsdShade.Shader.Define(stage, f"{path}/PreviewSurface")
    shader.CreateIdAttr("UsdPreviewSurface")
    shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*color))
    shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.52)
    shader.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(metallic)
    shader.CreateInput("opacity", Sdf.ValueTypeNames.Float).Set(opacity)
    if emissive:
        shader.CreateInput("emissiveColor", Sdf.ValueTypeNames.Color3f).Set(
            Gf.Vec3f(*(0.45 * value for value in color))
        )
    shader.CreateOutput("surface", Sdf.ValueTypeNames.Token)
    material.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")
    return material


def _add_cube(
    stage: Any,
    path: str,
    *,
    size: tuple[float, float, float],
    translation: tuple[float, float, float],
    material: Any,
    collision: bool,
    yaw_rad: float = 0.0,
) -> Any:
    from pxr import Gf, UsdGeom, UsdPhysics, UsdShade

    cube = UsdGeom.Cube.Define(stage, path)
    cube.CreateSizeAttr(1.0)
    cube.AddTranslateOp().Set(Gf.Vec3d(*translation))
    if yaw_rad:
        cube.AddOrientOp().Set(
            Gf.Quatf(math.cos(yaw_rad / 2.0), 0.0, 0.0, math.sin(yaw_rad / 2.0))
        )
    cube.AddScaleOp().Set(Gf.Vec3f(*size))
    if collision:
        UsdPhysics.CollisionAPI.Apply(cube.GetPrim())
    UsdShade.MaterialBindingAPI.Apply(cube.GetPrim()).Bind(material)
    return cube.GetPrim()


def _add_cylinder(
    stage: Any,
    path: str,
    *,
    radius: float,
    height: float,
    axis: Any,
    translation: tuple[float, float, float],
    material: Any,
    collision: bool,
) -> Any:
    from pxr import Gf, UsdGeom, UsdPhysics, UsdShade

    cylinder = UsdGeom.Cylinder.Define(stage, path)
    cylinder.CreateRadiusAttr(radius)
    cylinder.CreateHeightAttr(height)
    cylinder.CreateAxisAttr(axis)
    cylinder.AddTranslateOp().Set(Gf.Vec3d(*translation))
    if collision:
        UsdPhysics.CollisionAPI.Apply(cylinder.GetPrim())
    UsdShade.MaterialBindingAPI.Apply(cylinder.GetPrim()).Bind(material)
    return cylinder.GetPrim()
