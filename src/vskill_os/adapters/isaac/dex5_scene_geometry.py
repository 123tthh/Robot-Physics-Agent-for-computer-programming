"""Dedicated, dimensioned seated Dex5 workstation and passive spring keyboard.

This module authors geometry only. Robot motion, keyboard event generation and
independent task verification belong to separate adapters. Optional Isaac/USD
imports are delayed until scene construction.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any

from .p1_scene import (
    KEYBOARD_OPERATOR_ROW,
    KEY_PITCH_M,
    _legend_mesh_data,
    _safe_name,
    key_legend,
    keyboard_key_center,
    standard_keyboard_layout,
)


@dataclass(frozen=True, slots=True)
class Dex5SceneGeometryConfig:
    """Scene dimensions in meters and spring parameters in SI units."""

    table_top_z_m: float = 0.650
    table_center_x_m: float = 0.620
    table_depth_m: float = 0.450
    table_width_m: float = 0.900
    table_thickness_m: float = 0.035
    table_leg_width_m: float = 0.040
    table_leg_inset_m: float = 0.040
    keyboard_operator_row_x_m: float = 0.450
    keyboard_left_origin_y_m: float = 0.145
    key_center_z_m: float = 0.675
    key_height_m: float = 0.010
    key_pitch_m: float = KEY_PITCH_M
    key_gap_m: float = 0.002
    key_travel_m: float = 0.004
    key_mass_per_unit_kg: float = 0.006
    key_spring_stiffness_npm: float = 350.0
    key_spring_damping_nspm: float = 2.0
    key_spring_max_force_n: float = 8.0
    key_velocity_limit_mps: float = 0.25
    key_contact_offset_m: float = 0.0005
    keyboard_shell_margin_m: float = 0.011
    keyboard_shell_top_z_m: float = 0.664
    keyboard_root_size_m: float = 0.001
    keyboard_root_mass_kg: float = 1.0
    legend_surface_gap_m: float = 0.00008
    chair_center_x_m: float = -0.030
    chair_center_y_m: float = 0.0
    chair_seat_top_z_m: float = 0.440
    chair_seat_depth_m: float = 0.420
    chair_seat_width_m: float = 0.460
    chair_seat_thickness_m: float = 0.060
    chair_back_height_m: float = 0.440
    chair_back_thickness_m: float = 0.040
    chair_leg_width_m: float = 0.035
    chair_leg_inset_m: float = 0.045
    chair_seat_mass_kg: float = 8.0
    display_center_x_m: float = 0.830
    display_center_z_m: float = 0.960
    display_width_m: float = 0.530
    display_height_m: float = 0.310
    display_thickness_m: float = 0.020
    display_stand_height_m: float = 0.160
    display_stand_width_m: float = 0.035
    display_stand_depth_m: float = 0.035
    display_foot_depth_m: float = 0.110
    display_foot_width_m: float = 0.190
    display_foot_height_m: float = 0.014
    ground_size_m: float = 6.0

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            if not math.isfinite(value):
                raise ValueError(f"{name} must be finite")
            if name not in {"chair_center_x_m", "chair_center_y_m"} and value <= 0.0:
                raise ValueError(f"{name} must be positive")
        if self.key_gap_m >= self.key_pitch_m:
            raise ValueError("key gap must be smaller than the key pitch")
        if self.key_center_z_m - self.key_height_m / 2.0 - self.key_travel_m <= self.keyboard_shell_top_z_m:
            raise ValueError("fully depressed keys must clear the keyboard shell")
        if self.keyboard_shell_top_z_m <= self.table_top_z_m:
            raise ValueError("keyboard shell must extend above the table")
        if self.chair_seat_top_z_m <= self.chair_seat_thickness_m:
            raise ValueError("chair seat must clear the ground")


def author_dex5_workstation(
    *,
    stage: Any,
    sim_utils: Any,
    config: Dex5SceneGeometryConfig | None = None,
) -> dict[str, Any]:
    """Author a fixed chair, desk and 74 independent spring-return keycaps.

    The returned ``keyboard_cfg`` constructs an Isaac Lab ``Articulation``.
    Its drives have zero rest targets throughout the demo: pressing a key must
    come from robot contact, never from changing a keycap target. ``key_centers``
    are the *top surfaces* at rest; ``key_body_centers`` are rigid-body origins.
    """

    from isaaclab.actuators import ImplicitActuatorCfg
    from isaaclab.assets import ArticulationCfg
    from pxr import Gf, PhysxSchema, Sdf, UsdGeom, UsdPhysics, UsdShade

    cfg = config or Dex5SceneGeometryConfig()
    for path in ("/World/Workstation", "/World/Chair", "/World/Keyboard"):
        if stage.GetPrimAtPath(path).IsValid():
            raise ValueError(f"dedicated Dex5 scene path already exists: {path}")
    if not stage.GetPrimAtPath("/World").IsValid():
        sim_utils.create_prim("/World", "Xform")
    stage.SetDefaultPrim(stage.GetPrimAtPath("/World"))
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    for path in ("/World/Workstation", "/World/Chair", "/World/Looks", "/World/Lighting"):
        if not stage.GetPrimAtPath(path).IsValid():
            sim_utils.create_prim(path, "Xform")

    material = sim_utils.RigidBodyMaterialCfg(
        static_friction=0.8, dynamic_friction=0.7, restitution=0.0
    )

    def box(
        path: str,
        size: tuple[float, float, float],
        center: tuple[float, float, float],
        color: tuple[float, float, float],
        *,
        kinematic: bool = False,
    ) -> None:
        box_cfg = sim_utils.CuboidCfg(
            size=size,
            collision_props=sim_utils.CollisionPropertiesCfg(),
            physics_material=material,
            visual_material=sim_utils.PreviewSurfaceCfg(diffuse_color=color),
            rigid_props=(
                sim_utils.RigidBodyPropertiesCfg(kinematic_enabled=True, disable_gravity=True)
                if kinematic else None
            ),
            mass_props=(sim_utils.MassPropertiesCfg(mass=cfg.chair_seat_mass_kg) if kinematic else None),
            activate_contact_sensors=kinematic,
        )
        box_cfg.func(path, box_cfg, translation=center)

    ground_cfg = sim_utils.GroundPlaneCfg(
        size=(cfg.ground_size_m, cfg.ground_size_m), color=(0.20, 0.24, 0.29),
        physics_material=material,
    )
    ground_cfg.func("/World/Ground", ground_cfg)
    dome_cfg = sim_utils.DomeLightCfg(intensity=2200.0, color=(0.92, 0.95, 1.0))
    dome_cfg.func("/World/Lighting/Dome", dome_cfg)
    distant_cfg = sim_utils.DistantLightCfg(intensity=1600.0, angle=1.0)
    distant_cfg.func("/World/Lighting/Key", distant_cfg)

    table_leg_height = cfg.table_top_z_m - cfg.table_thickness_m
    box(
        "/World/Workstation/Desk",
        (cfg.table_depth_m, cfg.table_width_m, cfg.table_thickness_m),
        (cfg.table_center_x_m, 0.0, cfg.table_top_z_m - cfg.table_thickness_m / 2.0),
        (0.44, 0.32, 0.22),
    )
    for x_sign in (-1, 1):
        for y_sign in (-1, 1):
            box(
                f"/World/Workstation/Leg_{x_sign + 1}_{y_sign + 1}",
                (cfg.table_leg_width_m, cfg.table_leg_width_m, table_leg_height),
                (
                    cfg.table_center_x_m + x_sign * (cfg.table_depth_m / 2.0 - cfg.table_leg_inset_m),
                    y_sign * (cfg.table_width_m / 2.0 - cfg.table_leg_inset_m),
                    table_leg_height / 2.0,
                ),
                (0.12, 0.14, 0.17),
            )

    seat_path = "/World/Chair/Seat"
    box(
        seat_path,
        (cfg.chair_seat_depth_m, cfg.chair_seat_width_m, cfg.chair_seat_thickness_m),
        (cfg.chair_center_x_m, cfg.chair_center_y_m, cfg.chair_seat_top_z_m - cfg.chair_seat_thickness_m / 2.0),
        (0.07, 0.23, 0.28),
        kinematic=True,
    )
    stage.GetPrimAtPath(seat_path).SetCustomDataByKey("vskill:fixedChair", True)
    box(
        "/World/Chair/Backrest",
        (cfg.chair_back_thickness_m, cfg.chair_seat_width_m, cfg.chair_back_height_m),
        (
            cfg.chair_center_x_m - cfg.chair_seat_depth_m / 2.0,
            cfg.chair_center_y_m,
            cfg.chair_seat_top_z_m + cfg.chair_back_height_m / 2.0,
        ),
        (0.07, 0.23, 0.28),
    )
    chair_leg_height = cfg.chair_seat_top_z_m - cfg.chair_seat_thickness_m
    for x_sign in (-1, 1):
        for y_sign in (-1, 1):
            box(
                f"/World/Chair/Leg_{x_sign + 1}_{y_sign + 1}",
                (cfg.chair_leg_width_m, cfg.chair_leg_width_m, chair_leg_height),
                (
                    cfg.chair_center_x_m + x_sign * (cfg.chair_seat_depth_m / 2.0 - cfg.chair_leg_inset_m),
                    cfg.chair_center_y_m + y_sign * (cfg.chair_seat_width_m / 2.0 - cfg.chair_leg_inset_m),
                    chair_leg_height / 2.0,
                ),
                (0.10, 0.12, 0.14),
            )
    box(
        "/World/Workstation/Display",
        (cfg.display_thickness_m, cfg.display_width_m, cfg.display_height_m),
        (cfg.display_center_x_m, 0.0, cfg.display_center_z_m),
        (0.025, 0.055, 0.075),
    )
    box(
        "/World/Workstation/DisplayStand",
        (cfg.display_stand_depth_m, cfg.display_stand_width_m, cfg.display_stand_height_m),
        (cfg.display_center_x_m, 0.0, cfg.table_top_z_m + cfg.display_stand_height_m / 2.0),
        (0.09, 0.11, 0.13),
    )
    box(
        "/World/Workstation/DisplayFoot",
        (cfg.display_foot_depth_m, cfg.display_foot_width_m, cfg.display_foot_height_m),
        (cfg.display_center_x_m, 0.0, cfg.table_top_z_m + cfg.display_foot_height_m / 2.0),
        (0.09, 0.11, 0.13),
    )

    layout = standard_keyboard_layout()
    total_units = max(spec.column + spec.width_units / 2.0 for spec in layout)
    keyboard_center_x = cfg.keyboard_operator_row_x_m + KEYBOARD_OPERATOR_ROW * cfg.key_pitch_m / 2.0
    keyboard_center_y = cfg.keyboard_left_origin_y_m - total_units * cfg.key_pitch_m / 2.0
    shell_height = cfg.keyboard_shell_top_z_m - cfg.table_top_z_m
    box(
        "/World/Workstation/KeyboardShell",
        (
            (KEYBOARD_OPERATOR_ROW + 1.0) * cfg.key_pitch_m + 2.0 * cfg.keyboard_shell_margin_m,
            total_units * cfg.key_pitch_m + 2.0 * cfg.keyboard_shell_margin_m,
            shell_height,
        ),
        (keyboard_center_x, keyboard_center_y, cfg.table_top_z_m + shell_height / 2.0),
        (0.055, 0.075, 0.10),
    )

    for path in ("/World/Keyboard", "/World/Keyboard/Keys", "/World/Keyboard/Joints"):
        sim_utils.create_prim(path, "Xform")
    root_position = (keyboard_center_x, keyboard_center_y, cfg.table_top_z_m)
    root_cfg = sim_utils.CuboidCfg(
        size=(cfg.keyboard_root_size_m,) * 3,
        rigid_props=sim_utils.RigidBodyPropertiesCfg(disable_gravity=True),
        mass_props=sim_utils.MassPropertiesCfg(mass=cfg.keyboard_root_mass_kg),
    )
    root_cfg.func("/World/Keyboard/RootBody", root_cfg, translation=root_position)
    root_joint = UsdPhysics.FixedJoint.Define(stage, "/World/Keyboard/Joints/root_fixed")
    root_joint.CreateBody1Rel().SetTargets([Sdf.Path("/World/Keyboard/RootBody")])
    root_joint.CreateLocalPos0Attr().Set(Gf.Vec3f(*root_position))
    root_joint.CreateLocalPos1Attr().Set(Gf.Vec3f(0.0))

    legend_material = UsdShade.Material.Define(stage, "/World/Looks/Dex5KeyLegend")
    legend_shader = UsdShade.Shader.Define(stage, "/World/Looks/Dex5KeyLegend/Surface")
    legend_shader.CreateIdAttr("UsdPreviewSurface")
    legend_shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(0.96, 0.98, 1.0))
    legend_shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.4)
    legend_shader.CreateOutput("surface", Sdf.ValueTypeNames.Token)
    legend_material.CreateSurfaceOutput().ConnectToSource(legend_shader.ConnectableAPI(), "surface")

    key_centers: dict[str, tuple[float, float, float]] = {}
    key_body_centers: dict[str, tuple[float, float, float]] = {}
    key_paths: dict[str, str] = {}
    key_joint_names: dict[str, str] = {}
    for spec in layout:
        safe = _safe_name(spec.name)
        path = f"/World/Keyboard/Keys/{safe}"
        width = cfg.key_pitch_m * spec.width_units - cfg.key_gap_m
        depth = cfg.key_pitch_m - cfg.key_gap_m
        body_center = keyboard_key_center(
            spec,
            operator_row_x=cfg.keyboard_operator_row_x_m,
            left_origin_y=cfg.keyboard_left_origin_y_m,
            top_z=cfg.key_center_z_m,
            key_pitch=cfg.key_pitch_m,
        )
        key_cfg = sim_utils.CuboidCfg(
            size=(depth, width, cfg.key_height_m),
            rigid_props=sim_utils.RigidBodyPropertiesCfg(disable_gravity=True, max_depenetration_velocity=0.25),
            mass_props=sim_utils.MassPropertiesCfg(mass=cfg.key_mass_per_unit_kg * spec.width_units),
            collision_props=sim_utils.CollisionPropertiesCfg(contact_offset=cfg.key_contact_offset_m, rest_offset=0.0),
            physics_material=material,
            visual_material=sim_utils.PreviewSurfaceCfg(diffuse_color=(0.10, 0.16, 0.23)),
            activate_contact_sensors=True,
        )
        key_cfg.func(path, key_cfg, translation=body_center)
        points, counts, indices, _ = _legend_mesh_data(
            key_legend(spec.name), key_depth_m=depth, key_width_m=width,
            surface_z_m=cfg.key_height_m / 2.0 + cfg.legend_surface_gap_m,
        )
        legend = UsdGeom.Mesh.Define(stage, f"{path}/Legend")
        legend.CreatePointsAttr(points)
        legend.CreateFaceVertexCountsAttr(counts)
        legend.CreateFaceVertexIndicesAttr(indices)
        legend.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)
        legend.CreateDoubleSidedAttr(True)
        UsdShade.MaterialBindingAPI.Apply(legend.GetPrim()).Bind(legend_material)

        joint_name = f"{safe}_joint"
        joint = UsdPhysics.PrismaticJoint.Define(stage, f"/World/Keyboard/Joints/{joint_name}")
        joint.CreateBody0Rel().SetTargets([Sdf.Path("/World/Keyboard/RootBody")])
        joint.CreateBody1Rel().SetTargets([Sdf.Path(path)])
        joint.CreateLocalPos0Attr().Set(Gf.Vec3f(*(body_center[i] - root_position[i] for i in range(3))))
        joint.CreateLocalPos1Attr().Set(Gf.Vec3f(0.0))
        joint.CreateAxisAttr().Set(UsdPhysics.Tokens.z)
        joint.CreateLowerLimitAttr().Set(-cfg.key_travel_m)
        joint.CreateUpperLimitAttr().Set(0.0)
        drive = UsdPhysics.DriveAPI.Apply(joint.GetPrim(), "linear")
        drive.CreateTypeAttr().Set("force")
        drive.CreateTargetPositionAttr().Set(0.0)
        drive.CreateStiffnessAttr().Set(cfg.key_spring_stiffness_npm)
        drive.CreateDampingAttr().Set(cfg.key_spring_damping_nspm)
        drive.CreateMaxForceAttr().Set(cfg.key_spring_max_force_n)
        key_centers[spec.name] = (*body_center[:2], body_center[2] + cfg.key_height_m / 2.0)
        key_body_centers[spec.name] = body_center
        key_paths[spec.name] = path
        key_joint_names[spec.name] = joint_name

    keyboard_root = stage.GetPrimAtPath("/World/Keyboard")
    UsdPhysics.ArticulationRootAPI.Apply(keyboard_root)
    PhysxSchema.PhysxArticulationAPI.Apply(keyboard_root).CreateEnabledSelfCollisionsAttr().Set(False)
    keyboard_root.SetCustomDataByKey("vskill:actuation", "contact_only_zero_rest_spring")
    keyboard_cfg = ArticulationCfg(
        prim_path="/World/Keyboard", spawn=None,
        actuators={
            "passive_key_springs": ImplicitActuatorCfg(
                joint_names_expr=[".*_joint"],
                stiffness=cfg.key_spring_stiffness_npm,
                damping=cfg.key_spring_damping_nspm,
                effort_limit_sim=cfg.key_spring_max_force_n,
                velocity_limit_sim=cfg.key_velocity_limit_mps,
            )
        },
    )
    return {
        "keyboard_cfg": keyboard_cfg,
        "keyboard_path": "/World/Keyboard",
        "key_centers": key_centers,
        "key_body_centers": key_body_centers,
        "key_paths": key_paths,
        "key_joint_names": key_joint_names,
        "seat_path": seat_path,
        "chair_fixed": True,
        "config": asdict(cfg),
    }
