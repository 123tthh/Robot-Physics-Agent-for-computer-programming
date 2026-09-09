"""Isaac Sim P1 acceptance scene for G1 mode-15/5010 with dual Dex3.

This is an optional adapter entry point.  Only standard-library and V-Skill OS
modules are imported before :class:`isaaclab.app.AppLauncher` starts Kit.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile
import time
import traceback
from typing import Any, Iterable

from vskill_os.simulation.desktop import PtyDesktopWorker, ScreenFrame
from vskill_os.simulation.dex3_assets import (
    EXPECTED_BODY_JOINTS,
    EXPECTED_DEX3_JOINTS,
)
from vskill_os.simulation.keyboard_bridge import (
    KeyEvent,
    KeyEventType,
    KeycapConfig,
    KeycapSample,
    KeyboardEventBridge,
)
from vskill_os.simulation.navigation_assets import NavigationRoom
from vskill_os.simulation.workstation_assets import WorkstationAssetChain


@dataclass(frozen=True, slots=True)
class KeySpec:
    name: str
    row: float
    column: float
    width_units: float = 1.0


KEY_PITCH_M = 0.01905
KEYBOARD_OPERATOR_ROW = 5.25


_KEY_LEGEND_ALIASES = {
    "escape": "ESC",
    "backspace": "BKSP",
    "tab": "TAB",
    "delete": "DEL",
    "capslock": "CAPS",
    "enter": "ENT",
    "left_shift": "SHIFT",
    "right_shift": "SHIFT",
    "left_ctrl": "CTRL",
    "right_ctrl": "CTRL",
    "left_super": "WIN",
    "left_alt": "ALT",
    "right_alt": "ALT",
    "space": "SPACE",
    "menu": "MENU",
}


def standard_keyboard_layout() -> tuple[KeySpec, ...]:
    """Return a deterministic 74-key ANSI-like layout in key-pitch units."""

    rows: tuple[tuple[float, tuple[tuple[str, float], ...]], ...] = (
        (
            0.0,
            (
                ("escape", 1.0),
                *((f"f{index}", 1.0) for index in range(1, 13)),
            ),
        ),
        (
            1.25,
            tuple((key, 1.0) for key in ("`", "1", "2", "3", "4", "5", "6", "7", "8", "9", "0", "-", "="))
            + (("backspace", 2.0),),
        ),
        (
            2.25,
            (("tab", 1.5),)
            + tuple((key, 1.0) for key in "qwertyuiop[]\\")
            + (("delete", 1.5),),
        ),
        (
            3.25,
            (("capslock", 1.75),)
            + tuple((key, 1.0) for key in "asdfghjkl;'")
            + (("enter", 2.25),),
        ),
        (
            4.25,
            (("left_shift", 2.25),)
            + tuple((key, 1.0) for key in "zxcvbnm,./")
            + (("right_shift", 2.75),),
        ),
        (
            5.25,
            (
                ("left_ctrl", 1.25),
                ("left_super", 1.25),
                ("left_alt", 1.25),
                ("space", 6.25),
                ("right_alt", 1.25),
                ("menu", 1.25),
                ("right_ctrl", 1.25),
            ),
        ),
    )
    result: list[KeySpec] = []
    for row, keys in rows:
        column = 0.0
        for name, width in keys:
            result.append(KeySpec(name=name, row=row, column=column + width / 2.0, width_units=width))
            column += width
    return tuple(result)


def key_legend(key: str) -> str:
    """Return the concise ASCII legend rendered on a physical keycap."""

    return _KEY_LEGEND_ALIASES.get(key, key.upper())


def keyboard_key_center(
    spec: KeySpec,
    *,
    operator_row_x: float,
    left_origin_y: float,
    top_z: float,
    key_pitch: float = KEY_PITCH_M,
) -> tuple[float, float, float]:
    """Place the spacebar row nearest the robot on the negative-X operator side."""

    return (
        operator_row_x + (KEYBOARD_OPERATOR_ROW - spec.row) * key_pitch,
        left_origin_y - spec.column * key_pitch,
        top_z,
    )


def _legend_mesh_data(
    legend: str,
    *,
    key_depth_m: float,
    key_width_m: float,
    surface_z_m: float,
) -> tuple[list[tuple[float, float, float]], list[int], list[int], int]:
    """Rasterize a label into one non-colliding quad mesh on the key surface."""

    from PIL import Image, ImageDraw, ImageFont

    font = ImageFont.load_default()
    bounds = font.getbbox(legend)
    padding_px = 2
    width_px = max(1, bounds[2] - bounds[0]) + 2 * padding_px
    height_px = max(1, bounds[3] - bounds[1]) + 2 * padding_px
    bitmap = Image.new("1", (width_px, height_px), 0)
    draw = ImageDraw.Draw(bitmap)
    draw.text(
        (padding_px - bounds[0], padding_px - bounds[1]),
        legend,
        font=font,
        fill=1,
    )
    lit = [
        (row, column)
        for row in range(height_px)
        for column in range(width_px)
        if bitmap.getpixel((column, row))
    ]
    if not lit:
        # Some bitmap fonts expose a zero-area glyph for rare punctuation.
        # A centered dot is preferable to a physically blank keycap.
        lit = [(height_px // 2, width_px // 2)]

    scale = min(
        (key_depth_m - 0.0030) / height_px,
        (key_width_m - 0.0030) / width_px,
    )
    pixel_half = scale * 0.41
    points: list[tuple[float, float, float]] = []
    counts: list[int] = []
    indices: list[int] = []
    for row, column in lit:
        # From the robot/operator at -X: top is +X and the first glyph column is +Y.
        center_x = (height_px / 2.0 - row - 0.5) * scale
        center_y = (width_px / 2.0 - column - 0.5) * scale
        offset = len(points)
        points.extend(
            (
                (center_x - pixel_half, center_y - pixel_half, surface_z_m),
                (center_x + pixel_half, center_y - pixel_half, surface_z_m),
                (center_x + pixel_half, center_y + pixel_half, surface_z_m),
                (center_x - pixel_half, center_y + pixel_half, surface_z_m),
            )
        )
        counts.append(4)
        indices.extend((offset, offset + 1, offset + 2, offset + 3))
    return points, counts, indices, len(lit)


def _safe_name(key: str) -> str:
    aliases = {
        "`": "grave",
        "-": "minus",
        "=": "equal",
        "[": "left_bracket",
        "]": "right_bracket",
        "\\": "backslash",
        ";": "semicolon",
        "'": "apostrophe",
        ",": "comma",
        ".": "period",
        "/": "slash",
    }
    result = aliases.get(key, key).replace(" ", "_")
    return f"key_{result}" if result[:1].isdigit() else result


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the P1 Isaac/PhysX acceptance scene")
    root = Path(__file__).resolve().parents[4]
    parser.add_argument(
        "--robot-usd",
        type=Path,
        default=root / "artifacts/isaac-p1/assets/g1_mode15_dex3_p1/g1_mode15_dex3_p1.usd",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=root / "configs/simulation/g1_29dof_5010_p1.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=root / "reports/isaac_p1_acceptance.json",
    )
    parser.add_argument(
        "--scene-usd",
        type=Path,
        default=root / "artifacts/isaac-p1/scenes/p1_g1_dex3_keyboard.usda",
    )
    parser.add_argument(
        "--render-dir",
        type=Path,
        default=root / "reports/isaac_p1_renders",
    )
    parser.add_argument(
        "--asset-chain",
        type=Path,
        default=root / "configs/simulation/p0_p1_workstation_asset_chain.json",
    )
    parser.add_argument(
        "--navigation-room",
        type=Path,
        default=root / "configs/simulation/p1_navigation_room.json",
    )
    parser.add_argument("--workspace", type=Path, default=None)
    parser.add_argument("--physics-dt", type=float, default=1.0 / 120.0)
    parser.add_argument("--headless", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--device", default="cuda:0")
    return parser.parse_args()


def _emit_events(
    desktop: PtyDesktopWorker,
    events: Iterable[KeyEvent],
    event_log: list[KeyEvent],
) -> None:
    for event in events:
        event_log.append(event)
        desktop.handle_event(event)


def main() -> int:
    args = _parse_args()
    robot_usd = args.robot_usd.resolve()
    manifest_path = args.manifest.resolve()
    if not robot_usd.is_file():
        raise FileNotFoundError(f"converted G1+Dex3 USD is missing: {robot_usd}")
    if not manifest_path.is_file():
        raise FileNotFoundError(f"P1 manifest is missing: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    project_root = Path(__file__).resolve().parents[4]
    asset_chain_path = args.asset_chain.resolve()
    if not asset_chain_path.is_file():
        raise FileNotFoundError(f"P0/P1 workstation asset chain is missing: {asset_chain_path}")
    asset_chain = WorkstationAssetChain.from_json(asset_chain_path)
    navigation_room_path = args.navigation_room.resolve()
    if not navigation_room_path.is_file():
        raise FileNotFoundError(f"P1 navigation-room config is missing: {navigation_room_path}")
    navigation_room = NavigationRoom.from_json(navigation_room_path)
    runtime_asset = manifest["robot"]["runtime_asset"]
    if _sha256(robot_usd) != runtime_asset["usd_sha256"]:
        raise RuntimeError("runtime USD hash differs from the pinned P1 manifest")
    urdf_path = project_root / runtime_asset["urdf"]
    if not urdf_path.is_file() or _sha256(urdf_path) != runtime_asset["urdf_sha256"]:
        raise RuntimeError("runtime URDF is missing or differs from the pinned P1 manifest")
    isaac_path = Path(os.environ.get("ISAAC_PATH", "")).resolve()
    version_file = isaac_path / "VERSION"
    if not version_file.is_file():
        raise RuntimeError("ISAAC_PATH does not resolve to an Isaac Sim installation with VERSION")
    isaac_sim_build = version_file.read_text(encoding="utf-8").strip()
    isaac_sim_version = isaac_sim_build.split("-", 1)[0]
    if isaac_sim_version != manifest["simulator"]["isaac_sim_version"]:
        raise RuntimeError("installed Isaac Sim version differs from the pinned P1 manifest")
    isaaclab_path = Path(os.environ.get("ISAACLAB_PATH", "")).resolve()
    isaaclab_commit = subprocess.run(
        ["git", "-C", str(isaaclab_path), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if isaaclab_commit != manifest["simulator"]["isaac_lab_commit"]:
        raise RuntimeError("installed Isaac Lab commit differs from the pinned P1 manifest")

    # Isaac modules must be imported only after Kit has launched.
    from isaaclab.app import AppLauncher

    simulation_app = AppLauncher(headless=args.headless, enable_cameras=True).app
    temporary: tempfile.TemporaryDirectory[str] | None = None
    desktop: PtyDesktopWorker | None = None
    try:
        import torch
        from pxr import Gf, PhysxSchema, Sdf, Usd, UsdGeom, UsdPhysics, UsdShade

        import isaaclab
        import isaaclab.sim as sim_utils
        from isaaclab.actuators import ImplicitActuatorCfg
        from isaaclab.assets import Articulation, ArticulationCfg
        from isaaclab.sensors import Camera, CameraCfg, ContactSensor, ContactSensorCfg
        from vskill_os.adapters.isaac.navigation_scene import author_navigation_environment
        from vskill_os.adapters.isaac.workstation_scene import author_workstation_asset_chain

        if args.physics_dt <= 0.0 or not math.isfinite(args.physics_dt):
            raise ValueError("physics dt must be finite and positive")
        sim_utils.create_new_stage()
        sim = sim_utils.SimulationContext(
            sim_utils.SimulationCfg(
                dt=args.physics_dt,
                render_interval=12,
                device=args.device,
            )
        )
        stage = sim_utils.get_current_stage()

        static_material = sim_utils.RigidBodyMaterialCfg(
            static_friction=0.8,
            dynamic_friction=0.7,
            restitution=0.0,
        )
        light_cfg = sim_utils.DomeLightCfg(intensity=2200.0, color=(0.82, 0.84, 0.90))
        light_cfg.func("/World/Lighting/Dome", light_cfg)
        distant_cfg = sim_utils.DistantLightCfg(intensity=1800.0, angle=1.0)
        distant_cfg.func("/World/Lighting/Key", distant_cfg, rotation=(35.0, -25.0, 25.0))

        workstation_scene = author_workstation_asset_chain(
            stage=stage,
            sim_utils=sim_utils,
            chain=asset_chain,
        )
        world_prim = stage.GetPrimAtPath("/World")
        if not world_prim.IsValid():
            raise RuntimeError("P1 scene authoring did not produce /World")
        stage.SetDefaultPrim(world_prim)

        robot_cfg = ArticulationCfg(
            prim_path="/World/G1",
            spawn=sim_utils.UsdFileCfg(
                usd_path=str(robot_usd),
                activate_contact_sensors=True,
                rigid_props=sim_utils.RigidBodyPropertiesCfg(disable_gravity=False),
                articulation_props=sim_utils.ArticulationRootPropertiesCfg(
                    enabled_self_collisions=False,
                    solver_position_iteration_count=8,
                    solver_velocity_iteration_count=2,
                ),
            ),
            # Unitree uses +X as forward.  Keep the heading explicit so the
            # robot faces the workstation instead of relying on a default.
            init_state=ArticulationCfg.InitialStateCfg(
                pos=(0.0, 0.0, 0.79),
                rot=(1.0, 0.0, 0.0, 0.0),
            ),
            actuators={
                "g1_and_dex3": ImplicitActuatorCfg(
                    joint_names_expr=[".*"],
                    effort_limit_sim=300.0,
                    velocity_limit_sim=30.0,
                    stiffness=100.0,
                    damping=10.0,
                )
            },
        )
        robot = Articulation(robot_cfg)
        chair = Articulation(
            ArticulationCfg(
                prim_path="/World/MobileChair",
                spawn=None,
                # The chair is deliberately passive: contacts and external
                # forces move it, while no actuator commands any caster joint.
                actuators={},
            )
        )

        layout = standard_keyboard_layout()
        key_pitch = KEY_PITCH_M
        keyboard_operator_row_x = 0.72
        keyboard_left_origin_y = 0.145
        keyboard_top_z = 0.743
        shell_top_z = keyboard_top_z + 0.010
        key_up_z = shell_top_z + 0.0072
        key_height = 0.010
        key_depth = key_pitch - 0.0020
        keyboard_center_x = keyboard_operator_row_x + KEYBOARD_OPERATOR_ROW * key_pitch / 2.0

        sim_utils.create_prim("/World/Keyboard", "Xform")
        sim_utils.create_prim("/World/Keyboard/Keys", "Xform")
        sim_utils.create_prim("/World/Keyboard/Joints", "Xform")
        base_cfg = sim_utils.CuboidCfg(
            size=(0.001, 0.001, 0.001),
            rigid_props=sim_utils.RigidBodyPropertiesCfg(disable_gravity=True),
            mass_props=sim_utils.MassPropertiesCfg(mass=2.0),
        )
        base_position = (keyboard_center_x, 0.0, keyboard_top_z)
        base_cfg.func("/World/Keyboard/RootBody", base_cfg, translation=base_position)
        base_fixed = UsdPhysics.FixedJoint.Define(stage, "/World/Keyboard/Joints/root_fixed")
        base_fixed.CreateBody1Rel().SetTargets([Sdf.Path("/World/Keyboard/RootBody")])
        base_fixed.CreateLocalPos0Attr().Set(Gf.Vec3f(*base_position))
        base_fixed.CreateLocalPos1Attr().Set(Gf.Vec3f(0.0))

        shell_cfg = sim_utils.CuboidCfg(
            size=(0.150, 0.305, 0.020),
            collision_props=sim_utils.CollisionPropertiesCfg(),
            physics_material=static_material,
            visual_material=sim_utils.PreviewSurfaceCfg(diffuse_color=(0.055, 0.060, 0.070), metallic=0.1),
        )
        shell_cfg.func(
            "/World/Workstation/KeyboardShell",
            shell_cfg,
            translation=(keyboard_center_x, 0.0, keyboard_top_z),
        )

        if not stage.GetPrimAtPath("/World/Looks").IsValid():
            sim_utils.create_prim("/World/Looks", "Scope")
        legend_material = UsdShade.Material.Define(stage, "/World/Looks/KeyboardLegend")
        legend_shader = UsdShade.Shader.Define(
            stage, "/World/Looks/KeyboardLegend/PreviewSurface"
        )
        legend_shader.CreateIdAttr("UsdPreviewSurface")
        legend_shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(
            Gf.Vec3f(0.96, 0.98, 1.0)
        )
        legend_shader.CreateInput("emissiveColor", Sdf.ValueTypeNames.Color3f).Set(
            Gf.Vec3f(0.15, 0.17, 0.20)
        )
        legend_shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.35)
        legend_shader.CreateOutput("surface", Sdf.ValueTypeNames.Token)
        legend_material.CreateSurfaceOutput().ConnectToSource(
            legend_shader.ConnectableAPI(), "surface"
        )

        key_names: dict[str, str] = {}
        key_cfg_cache: dict[float, Any] = {}
        legend_pixel_count = 0
        for spec in layout:
            safe = _safe_name(spec.name)
            prim_path = f"/World/Keyboard/Keys/{safe}"
            width = max(0.010, key_pitch * spec.width_units - 0.0020)
            key_cfg = key_cfg_cache.get(width)
            if key_cfg is None:
                key_cfg = sim_utils.CuboidCfg(
                    size=(key_depth, width, key_height),
                    rigid_props=sim_utils.RigidBodyPropertiesCfg(
                        disable_gravity=True,
                        max_depenetration_velocity=0.5,
                    ),
                    mass_props=sim_utils.MassPropertiesCfg(mass=0.006 * spec.width_units),
                    collision_props=sim_utils.CollisionPropertiesCfg(contact_offset=0.0005, rest_offset=0.0),
                    physics_material=sim_utils.RigidBodyMaterialCfg(
                        static_friction=0.65,
                        dynamic_friction=0.55,
                        restitution=0.0,
                    ),
                    visual_material=sim_utils.PreviewSurfaceCfg(
                        diffuse_color=(0.16, 0.18, 0.21) if spec.name not in {"left_ctrl", "left_alt", "t"} else (0.10, 0.36, 0.65)
                    ),
                    activate_contact_sensors=True,
                )
                key_cfg_cache[width] = key_cfg
            center = keyboard_key_center(
                spec,
                operator_row_x=keyboard_operator_row_x,
                left_origin_y=keyboard_left_origin_y,
                top_z=key_up_z,
                key_pitch=key_pitch,
            )
            key_cfg.func(prim_path, key_cfg, translation=center)
            legend = key_legend(spec.name)
            points, face_counts, face_indices, pixel_count = _legend_mesh_data(
                legend,
                key_depth_m=key_depth,
                key_width_m=width,
                surface_z_m=key_height / 2.0 + 0.00008,
            )
            legend_mesh = UsdGeom.Mesh.Define(stage, f"{prim_path}/Legend")
            legend_mesh.CreatePointsAttr(points)
            legend_mesh.CreateFaceVertexCountsAttr(face_counts)
            legend_mesh.CreateFaceVertexIndicesAttr(face_indices)
            legend_mesh.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)
            legend_mesh.CreateDoubleSidedAttr(True)
            UsdShade.MaterialBindingAPI.Apply(legend_mesh.GetPrim()).Bind(legend_material)
            legend_mesh.GetPrim().SetCustomDataByKey("vskill:label", legend)
            legend_pixel_count += pixel_count
            joint = UsdPhysics.PrismaticJoint.Define(
                stage, f"/World/Keyboard/Joints/{safe}_joint"
            )
            joint.CreateBody0Rel().SetTargets([Sdf.Path("/World/Keyboard/RootBody")])
            joint.CreateBody1Rel().SetTargets([Sdf.Path(prim_path)])
            relative = tuple(center[index] - base_position[index] for index in range(3))
            joint.CreateLocalPos0Attr().Set(Gf.Vec3f(*relative))
            joint.CreateLocalPos1Attr().Set(Gf.Vec3f(0.0))
            joint.CreateAxisAttr().Set(UsdPhysics.Tokens.z)
            joint.CreateLowerLimitAttr().Set(-0.0040)
            joint.CreateUpperLimitAttr().Set(0.0)
            drive = UsdPhysics.DriveAPI.Apply(joint.GetPrim(), "linear")
            drive.CreateTypeAttr().Set("force")
            drive.CreateTargetPositionAttr().Set(0.0)
            drive.CreateStiffnessAttr().Set(4200.0)
            drive.CreateDampingAttr().Set(35.0)
            drive.CreateMaxForceAttr().Set(20.0)
            key_names[spec.name] = safe

        UsdPhysics.ArticulationRootAPI.Apply(stage.GetPrimAtPath("/World/Keyboard"))
        articulation_api = PhysxSchema.PhysxArticulationAPI.Apply(
            stage.GetPrimAtPath("/World/Keyboard")
        )
        articulation_api.CreateEnabledSelfCollisionsAttr().Set(False)
        keyboard_cfg = ArticulationCfg(
            prim_path="/World/Keyboard",
            spawn=None,
            actuators={
                "key_switches": ImplicitActuatorCfg(
                    joint_names_expr=[".*_joint"],
                    effort_limit_sim=20.0,
                    velocity_limit_sim=1.0,
                    stiffness=4200.0,
                    damping=35.0,
                )
            },
        )
        keyboard = Articulation(keyboard_cfg)

        contact_sensor = ContactSensor(
            ContactSensorCfg(
                prim_path="/World/Keyboard/Keys/.*",
                update_period=0.0,
                history_length=2,
                force_threshold=0.01,
                track_air_time=True,
            )
        )
        camera_cfg = sim_utils.PinholeCameraCfg(
            focal_length=24.0,
            focus_distance=1.0,
            horizontal_aperture=20.955,
            clipping_range=(0.05, 20.0),
        )
        head_camera = Camera(
            CameraCfg(
                prim_path="/World/Sensors/HeadCamera",
                update_period=0.0,
                height=720,
                width=1280,
                data_types=["rgb"],
                spawn=camera_cfg,
                update_latest_camera_pose=True,
            )
        )
        keyboard_camera = Camera(
            CameraCfg(
                prim_path="/World/Sensors/KeyboardCamera",
                update_period=0.0,
                height=720,
                width=1280,
                data_types=["rgb"],
                spawn=camera_cfg,
                update_latest_camera_pose=True,
            )
        )
        overview_camera = Camera(
            CameraCfg(
                prim_path="/World/Sensors/OverviewCamera",
                update_period=0.0,
                height=540,
                width=960,
                data_types=["rgb"],
                spawn=camera_cfg,
                update_latest_camera_pose=True,
            )
        )
        navigation_scene = author_navigation_environment(
            stage=stage,
            sim_utils=sim_utils,
            room=navigation_room,
            asset_chain=asset_chain,
        )

        sim.reset()
        head_camera.set_world_poses_from_view(
            torch.tensor([[0.16, 0.0, 1.58]], device=head_camera.device),
            torch.tensor([[0.78, 0.0, 0.76]], device=head_camera.device),
        )
        keyboard_camera.set_world_poses_from_view(
            torch.tensor([[0.47, 0.0, 1.18]], device=keyboard_camera.device),
            torch.tensor([[keyboard_center_x, 0.0, 0.75]], device=keyboard_camera.device),
        )
        overview_camera.set_world_poses_from_view(
            torch.tensor([[-3.40, -2.60, 4.40]], device=overview_camera.device),
            torch.tensor([[0.30, 0.00, 0.55]], device=overview_camera.device),
        )
        args.scene_usd.parent.mkdir(parents=True, exist_ok=True)
        stage.GetRootLayer().Export(str(args.scene_usd.resolve()))

        robot_joint_set = set(robot.joint_names)
        expected_body = set(EXPECTED_BODY_JOINTS)
        expected_dex3 = set(EXPECTED_DEX3_JOINTS)
        if robot_joint_set != expected_body | expected_dex3:
            raise RuntimeError(
                "runtime joint mismatch: "
                f"missing={sorted((expected_body | expected_dex3) - robot_joint_set)}, "
                f"extra={sorted(robot_joint_set - (expected_body | expected_dex3))}"
            )
        if len(keyboard.joint_names) != len(layout):
            raise RuntimeError(
                f"keyboard articulation has {len(keyboard.joint_names)} joints, expected {len(layout)}"
            )

        # Prove that both Dex3 hands are active simulation joints, not only meshes.
        dex3_ids, _ = robot.find_joints(list(EXPECTED_DEX3_JOINTS), preserve_order=True)
        dex3_before = robot.data.joint_pos[0, dex3_ids].clone()
        robot_targets = robot.data.default_joint_pos.clone()
        limits = robot.data.joint_pos_limits[0, dex3_ids]
        dex3_target = torch.minimum(
            torch.maximum(dex3_before + 0.08, limits[:, 0] + 0.01),
            limits[:, 1] - 0.01,
        )
        robot_targets[0, dex3_ids] = dex3_target
        for _ in range(48):
            robot.set_joint_position_target(robot_targets)
            robot.write_data_to_sim()
            sim.step(render=False)
            robot.update(args.physics_dt)
        dex3_after = robot.data.joint_pos[0, dex3_ids].clone()
        dex3_motion = torch.abs(dex3_after - dex3_before)
        body_masses = robot.data.default_mass
        body_inertias = robot.data.default_inertia
        inertia_diagonal = body_inertias[..., (0, 4, 8)]
        robot_collision_shapes = [
            prim
            for prim in Usd.PrimRange.Stage(stage, Usd.TraverseInstanceProxies())
            if prim.GetPath().pathString.startswith("/World/G1/")
            and prim.HasAPI(UsdPhysics.CollisionAPI)
        ]

        workspace = args.workspace
        if workspace is None:
            temporary = tempfile.TemporaryDirectory(prefix="vskill-isaac-p1-")
            workspace = Path(temporary.name)
        desktop = PtyDesktopWorker(workspace)
        desktop.start()
        keyboard_cfg_manifest = manifest["keyboard"]
        bridge = KeyboardEventBridge(
            KeycapConfig(
                press_threshold_m=float(keyboard_cfg_manifest["press_threshold_m"]),
                release_threshold_m=float(keyboard_cfg_manifest["release_threshold_m"]),
                debounce_seconds=float(keyboard_cfg_manifest["debounce_seconds"]),
                maximum_hold_seconds=float(keyboard_cfg_manifest["maximum_hold_seconds"]),
            )
        )
        event_log: list[KeyEvent] = []
        sample_count = 0
        physical_time = time.monotonic()
        key_joint_ids = {
            key: keyboard.find_joints([f"{safe}_joint"], preserve_order=True)[0][0]
            for key, safe in key_names.items()
        }
        contact_indices = {name: index for index, name in enumerate(contact_sensor.body_names)}
        key_targets = keyboard.data.default_joint_pos.clone()
        max_displacement = {key: 0.0 for key in key_names}
        max_contact_force = {key: 0.0 for key in key_names}

        def step_key(key: str, target: float, *, require_contact: bool) -> tuple[KeyEvent, ...]:
            nonlocal physical_time, sample_count
            joint_id = key_joint_ids[key]
            safe = key_names[key]
            key_targets[0, joint_id] = target
            emitted: list[KeyEvent] = []
            achieved = False
            for _ in range(90):
                keyboard.set_joint_position_target(key_targets)
                keyboard.write_data_to_sim()
                robot.set_joint_position_target(robot_targets)
                robot.write_data_to_sim()
                sim.step(render=False)
                keyboard.update(args.physics_dt)
                robot.update(args.physics_dt)
                contact_sensor.update(args.physics_dt, force_recompute=True)
                physical_time += args.physics_dt
                displacement = abs(float(keyboard.data.joint_pos[0, joint_id].item()))
                force = float(
                    torch.linalg.vector_norm(
                        contact_sensor.data.net_forces_w[0, contact_indices[safe]]
                    ).item()
                )
                in_contact = force > 0.01
                max_displacement[key] = max(max_displacement[key], displacement)
                max_contact_force[key] = max(max_contact_force[key], force)
                sample = KeycapSample(
                    key=key,
                    displacement_m=displacement,
                    in_contact=in_contact,
                    monotonic_time=physical_time,
                    source="isaac_physx_keycap_contact",
                    sample_id=f"isaac-{sample_count}",
                )
                sample_count += 1
                events = bridge.process(sample, now=physical_time)
                emitted.extend(events)
                _emit_events(desktop, events, event_log)
                if require_contact:
                    achieved = in_contact and displacement >= bridge.config.press_threshold_m
                else:
                    achieved = (not in_contact) and displacement <= bridge.config.release_threshold_m
                if achieved:
                    # One extra sample exercises held/released stability.
                    if require_contact and bridge.phase(key).value in {"actuated", "held"}:
                        break
                    if not require_contact and bridge.phase(key).value == "released":
                        break
            if not achieved:
                raise RuntimeError(
                    f"key {key!r} failed to reach target={target}; "
                    f"max_displacement={max_displacement[key]:.6f}, "
                    f"max_contact_force={max_contact_force[key]:.6f}"
                )
            return tuple(emitted)

        def key_down(key: str) -> None:
            step_key(key, -0.0037, require_contact=True)

        def key_up(key: str) -> None:
            step_key(key, 0.0, require_contact=False)

        def tap(key: str) -> None:
            key_down(key)
            key_up(key)

        key_down("left_ctrl")
        key_down("left_alt")
        tap("t")
        key_up("left_alt")
        key_up("left_ctrl")
        if not desktop.visible or not desktop.focused:
            raise RuntimeError("physical Ctrl+Alt+T did not focus the PTY desktop")

        for character in "printf p1-ready\n":
            tap({" ": "space", "\n": "enter"}.get(character, character))

        final_frame = ScreenFrame(0, time.monotonic(), False, False, "")
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            final_frame = desktop.snapshot()
            if final_frame.text.count("p1-ready") >= 2:
                break
            time.sleep(0.01)
        output_observed = final_frame.text.count("p1-ready") >= 2

        # RTX camera health is independent evidence from the physics oracle.
        for _ in range(8):
            sim.step(render=True)
            head_camera.update(args.physics_dt, force_recompute=True)
            keyboard_camera.update(args.physics_dt, force_recompute=True)
            overview_camera.update(args.physics_dt, force_recompute=True)

        args.render_dir.resolve().mkdir(parents=True, exist_ok=True)

        def camera_health(camera: Any, artifact_name: str) -> dict[str, Any]:
            from PIL import Image

            image = camera.data.output["rgb"]
            rgb = image[0, ..., :3].detach().cpu()
            if rgb.is_floating_point():
                if float(rgb.max().item()) <= 1.0:
                    rgb = rgb * 255.0
                rgb = rgb.clamp(0.0, 255.0).to(torch.uint8)
            else:
                rgb = rgb.to(torch.uint8)
            artifact = args.render_dir.resolve() / artifact_name
            Image.fromarray(rgb.numpy()).save(artifact)
            return {
                "initialized": bool(camera.is_initialized),
                "shape": list(image.shape),
                "finite": bool(torch.isfinite(image.float()).all().item()),
                "mean": float(image.float().mean().item()),
                "std": float(image.float().std().item()),
                "source": "isaac_rtx_rgb",
                "artifact": str(artifact),
                "artifact_sha256": _sha256(artifact),
            }

        releases = bridge.all_keys_up(
            monotonic_time=physical_time + args.physics_dt,
            reason="isaac_p1_shutdown",
        )
        _emit_events(desktop, releases, event_log)
        counts = Counter(event.event_type.value for event in event_log)
        balanced = counts[KeyEventType.KEYDOWN.value] == counts[KeyEventType.KEYUP.value]
        contacted_keys = sorted(key for key, force in max_contact_force.items() if force > 0.01)
        head_health = camera_health(head_camera, "head_camera.png")
        keyboard_health = camera_health(keyboard_camera, "keyboard_camera.png")
        overview_health = camera_health(overview_camera, "overview_camera.png")

        # Exercise the same free-root/contact dynamics that let a robot push
        # the chair.  This is an independent mobility gate, not a claim that a
        # learned sit trajectory has already contacted the chair correctly.
        mobility_cfg = asset_chain.chair["mobility_probe"]
        frame_body_ids, _ = chair.find_bodies("Frame")
        rolling_joint_ids = [
            index
            for index, name in enumerate(chair.joint_names)
            if name.endswith("_Rolling")
        ]
        chair_before_xy = chair.data.root_pos_w[0, :2].clone()
        wheel_before = chair.data.joint_pos[0, rolling_joint_ids].clone()
        forces = torch.zeros(
            chair.num_instances,
            len(frame_body_ids),
            3,
            device=chair.device,
        )
        torques = torch.zeros_like(forces)
        forces[..., 0] = float(mobility_cfg["force_n"])
        chair.permanent_wrench_composer.set_forces_and_torques(
            forces=forces,
            torques=torques,
            body_ids=frame_body_ids,
            is_global=True,
        )
        force_steps = max(
            1,
            math.ceil(float(mobility_cfg["force_duration_s"]) / args.physics_dt),
        )
        for _ in range(force_steps):
            chair.write_data_to_sim()
            sim.step(render=False)
            chair.update(args.physics_dt)
        chair.permanent_wrench_composer.set_forces_and_torques(
            forces=torch.zeros_like(forces),
            torques=torch.zeros_like(torques),
            body_ids=frame_body_ids,
            is_global=True,
        )
        coast_steps = max(
            1,
            math.ceil(float(mobility_cfg["coast_duration_s"]) / args.physics_dt),
        )
        for _ in range(coast_steps):
            chair.write_data_to_sim()
            sim.step(render=False)
            chair.update(args.physics_dt)
        chair_after_xy = chair.data.root_pos_w[0, :2].clone()
        wheel_after = chair.data.joint_pos[0, rolling_joint_ids].clone()
        chair_displacement_m = float(
            torch.linalg.vector_norm(chair_after_xy - chair_before_xy).item()
        )
        wheel_motion = torch.abs(wheel_after - wheel_before)
        max_wheel_rotation_rad = float(wheel_motion.max().item())
        mean_wheel_rotation_rad = float(wheel_motion.mean().item())
        rolling_equivalent_distance_m = (
            mean_wheel_rotation_rad * float(asset_chain.chair["wheel_radius_m"])
        )
        rolling_contribution_ratio = rolling_equivalent_distance_m / max(
            chair_displacement_m, 1.0e-9
        )
        sliding_dominant = rolling_contribution_ratio <= float(
            mobility_cfg["maximum_rolling_contribution_ratio"]
        )
        chair_mobility_passed = (
            chair_displacement_m
            >= float(mobility_cfg["minimum_displacement_m"])
            and sliding_dominant
        )
        front_row_x = min(
            center[0]
            for spec in layout
            if spec.row == KEYBOARD_OPERATOR_ROW
            for center in (
                keyboard_key_center(
                    spec,
                    operator_row_x=keyboard_operator_row_x,
                    left_origin_y=keyboard_left_origin_y,
                    top_z=key_up_z,
                ),
            )
        )
        function_row_x = min(
            center[0]
            for spec in layout
            if spec.row == 0.0
            for center in (
                keyboard_key_center(
                    spec,
                    operator_row_x=keyboard_operator_row_x,
                    left_origin_y=keyboard_left_origin_y,
                    top_z=key_up_z,
                ),
            )
        )
        keyboard_operator_side_valid = front_row_x < function_row_x
        escape_y = next(
            keyboard_key_center(
                spec,
                operator_row_x=keyboard_operator_row_x,
                left_origin_y=keyboard_left_origin_y,
                top_z=key_up_z,
            )[1]
            for spec in layout
            if spec.name == "escape"
        )
        f12_y = next(
            keyboard_key_center(
                spec,
                operator_row_x=keyboard_operator_row_x,
                left_origin_y=keyboard_left_origin_y,
                top_z=key_up_z,
            )[1]
            for spec in layout
            if spec.name == "f12"
        )
        keyboard_left_right_valid = escape_y > f12_y
        robot_faces_keyboard = keyboard_center_x > 0.0
        required_prim_paths = tuple(asset_chain.validation["required_prim_paths"])
        required_prims_present = all(
            stage.GetPrimAtPath(path).IsValid() for path in required_prim_paths
        )
        navigation_required_paths = tuple(
            navigation_room.validation["required_prim_paths"]
        )
        navigation_required_prims_present = all(
            stage.GetPrimAtPath(path).IsValid() for path in navigation_required_paths
        )
        navigation_station_ids = sorted(
            str(stage.GetPrimAtPath(path).GetCustomDataByKey("vskill:workstationId"))
            for path in navigation_scene["station_paths"]
        )
        chair_swivel_joint_count = len(workstation_scene["chair_swivel_joint_paths"])
        chair_wheel_joint_count = len(workstation_scene["chair_wheel_joint_paths"])
        stage_marker_count = len(workstation_scene["stage_marker_paths"])
        succeeded = all(
            (
                robot.num_joints == 43,
                len(dex3_ids) == 14,
                bool(torch.all(dex3_motion > 1.0e-4).item()),
                bool(torch.isfinite(body_masses).all().item()),
                bool(torch.all(body_masses > 0.0).item()),
                bool(torch.isfinite(body_inertias).all().item()),
                bool(torch.all(inertia_diagonal > 0.0).item()),
                len(robot_collision_shapes) > 0,
                len(keyboard.joint_names) == len(layout),
                set(("left_ctrl", "left_alt", "t", "enter")).issubset(contacted_keys),
                output_observed,
                balanced,
                not bridge.keys_down,
                head_health["initialized"],
                head_health["finite"],
                head_health["std"] > 0.0,
                keyboard_health["initialized"],
                keyboard_health["finite"],
                keyboard_health["std"] > 0.0,
                overview_health["initialized"],
                overview_health["finite"],
                overview_health["std"] > 0.0,
                legend_pixel_count > len(layout),
                keyboard_operator_side_valid,
                keyboard_left_right_valid,
                robot_faces_keyboard,
                required_prims_present,
                stage_marker_count == len(asset_chain.stages),
                chair_swivel_joint_count == 5,
                chair_wheel_joint_count == 5,
                workstation_scene["chair_rigid_body_count"] == 11,
                len(frame_body_ids) == 1,
                len(rolling_joint_ids) == 5,
                chair_mobility_passed,
                navigation_required_prims_present,
                len(navigation_scene["wall_paths"])
                == int(navigation_room.room["wall_collider_count"]),
                navigation_station_ids
                == sorted(station.id for station in navigation_room.workstations),
                len(navigation_scene["spawn_paths"])
                == len(navigation_room.spawn_points),
                len(navigation_scene["anchor_paths"])
                == navigation_room.summary()["anchor_count"],
            )
        )
        gpu_name = torch.cuda.get_device_name(0) if torch.cuda.is_available() else "none"
        report = {
            "schema_version": 1,
            "phase": "P1",
            "status": "passed" if succeeded else "failed",
            "generated_at_unix": time.time(),
            "runtime": {
                "isaac_sim": isaac_sim_version,
                "isaac_sim_build": isaac_sim_build,
                "isaac_sim_path": str(isaac_path),
                "isaac_lab_release": manifest["simulator"]["isaac_lab_version"],
                "isaac_lab": getattr(isaaclab, "__version__", "unknown"),
                "isaac_lab_path": str(isaaclab_path),
                "isaac_lab_commit": isaaclab_commit,
                "device": args.device,
                "cuda_available": bool(torch.cuda.is_available()),
                "gpu": gpu_name,
                "physics_dt": args.physics_dt,
            },
            "robot": {
                "asset": str(robot_usd),
                "asset_sha256": _sha256(robot_usd),
                "fixed_base": True,
                "mode_machine": 15,
                "wrist_motor": "5010",
                "joint_count": robot.num_joints,
                "rigid_body_count": robot.num_bodies,
                "body_joint_count": len(expected_body & robot_joint_set),
                "dex3_joint_count": len(expected_dex3 & robot_joint_set),
                "collision_shape_count": len(robot_collision_shapes),
                "positive_mass_count": int((body_masses > 0.0).sum().item()),
                "minimum_mass_kg": float(body_masses.min().item()),
                "maximum_mass_kg": float(body_masses.max().item()),
                "inertia_finite": bool(torch.isfinite(body_inertias).all().item()),
                "inertia_positive_diagonal": bool(torch.all(inertia_diagonal > 0.0).item()),
                "dex3_joint_names": sorted(expected_dex3 & robot_joint_set),
                "dex3_min_motion_rad": float(dex3_motion.min().item()),
                "dex3_max_motion_rad": float(dex3_motion.max().item()),
                "heading_axis_world": [1.0, 0.0, 0.0],
                "heading_yaw_rad": 0.0,
                "faces_keyboard": robot_faces_keyboard,
            },
            "scene": {
                "usd": str(args.scene_usd.resolve()),
                "usd_sha256": _sha256(args.scene_usd.resolve()),
                "keyboard_key_count": len(layout),
                "keyboard_prismatic_joint_count": len(keyboard.joint_names),
                "contact_sensor_body_count": len(contact_sensor.body_names),
                "keyboard_legend_count": len(layout),
                "keyboard_legend_lit_pixel_count": legend_pixel_count,
                "keyboard_operator_side": "negative_x",
                "keyboard_operator_row_x": front_row_x,
                "keyboard_function_row_x": function_row_x,
                "keyboard_layout_operator_side_valid": keyboard_operator_side_valid,
                "keyboard_escape_y": escape_y,
                "keyboard_f12_y": f12_y,
                "keyboard_left_right_valid": keyboard_left_right_valid,
                "head_camera": head_health,
                "keyboard_camera": keyboard_health,
                "overview_camera": overview_health,
            },
            "workstation_asset_chain": {
                **asset_chain.summary(),
                "config": str(asset_chain_path),
                "property_assignment": asset_chain.validation["property_assignment"],
                "required_prim_paths": list(required_prim_paths),
                "required_prims_present": required_prims_present,
                "stage_marker_paths": workstation_scene["stage_marker_paths"],
                "chair_prim": workstation_scene["chair_prim"],
                "chair_rigid_body_count": workstation_scene["chair_rigid_body_count"],
                "chair_swivel_joint_count": chair_swivel_joint_count,
                "chair_wheel_joint_count": chair_wheel_joint_count,
                "chair_contact_paths": workstation_scene["chair_contact_paths"],
                "chair_initial_center_m": workstation_scene["chair_initial_center_m"],
                "chair_working_center_m": workstation_scene["chair_working_center_m"],
                "chair_seat_top_height_m": workstation_scene[
                    "chair_seat_top_height_m"
                ],
                "chair_world_fixed": workstation_scene["chair_world_fixed"],
                "chair_mobility_probe": {
                    "source": "isaac_physx_external_force_on_free_chair_root",
                    "force_n": float(mobility_cfg["force_n"]),
                    "force_duration_s": force_steps * args.physics_dt,
                    "coast_duration_s": coast_steps * args.physics_dt,
                    "displacement_m": chair_displacement_m,
                    "minimum_displacement_m": float(
                        mobility_cfg["minimum_displacement_m"]
                    ),
                    "max_wheel_rotation_rad": max_wheel_rotation_rad,
                    "mean_wheel_rotation_rad": mean_wheel_rotation_rad,
                    "rolling_equivalent_distance_m": rolling_equivalent_distance_m,
                    "rolling_contribution_ratio": rolling_contribution_ratio,
                    "maximum_rolling_contribution_ratio": float(
                        mobility_cfg["maximum_rolling_contribution_ratio"]
                    ),
                    "sliding_dominant": sliding_dominant,
                    "passed": chair_mobility_passed,
                    "interpretation": "free passive chair responds primarily by sliding under contact-equivalent force; robot sit-contact trajectory is not claimed",
                },
                "desk_leg_count": workstation_scene["desk_leg_count"],
                "display_has_stand": stage.GetPrimAtPath(
                    workstation_scene["display_stand_prim"]
                ).IsValid(),
                "motion_policy_status": "not_claimed_asset_readiness_only",
            },
            "navigation_environment": {
                **navigation_room.summary(),
                "config": str(navigation_room_path),
                "room_prim": navigation_scene["room_prim"],
                "wall_paths": navigation_scene["wall_paths"],
                "station_paths": navigation_scene["station_paths"],
                "anchor_paths": navigation_scene["anchor_paths"],
                "spawn_paths": navigation_scene["spawn_paths"],
                "obstacle_paths": navigation_scene["obstacle_paths"],
                "required_prim_paths": list(navigation_required_paths),
                "required_prims_present": navigation_required_prims_present,
                "policy_status": "not_claimed_asset_readiness_only",
            },
            "keyboard_bridge": {
                "physics_sample_source": "isaac_physx_keycap_contact",
                "event_source": "keyboard_event_bridge",
                "desktop_source": final_frame.source,
                "sample_count": sample_count,
                "contacted_keys": contacted_keys,
                "max_displacement_m": {
                    key: value for key, value in max_displacement.items() if value > 0.0
                },
                "max_contact_force_n": {
                    key: value for key, value in max_contact_force.items() if value > 0.0
                },
                "event_counts": dict(counts),
                "terminal_visible": final_frame.visible,
                "terminal_focused": final_frame.focused,
                "pty_output_observed": output_observed,
                "keys_down_after_shutdown": list(bridge.keys_down),
            },
            "scope": {
                "included": [
                    "G1 29DoF mode-15/5010 fixed-base articulation",
                    "dual Dex3-1 meshes, collisions, inertias, and 14 actuated joints",
                    "74-key procedural keyboard with independent physical travel",
                    "74 high-contrast mesh legends fixed to moving keycaps",
                    "operator-side keyboard row orientation and explicit +X robot heading",
                    "six-stage entry-to-seated-work asset chain with visible stage markers",
                    "passive five-caster office chair with five swivel and five rolling joints",
                    "free chair root and measured sliding-dominant PhysX mobility gate",
                    "four-wall room shell with doorway and three uniquely identified workstations",
                    "multiple robot spawn, approach, chair-alignment, and work-pose anchors",
                    "static navigation obstacle with collision geometry",
                    "seat, backrest, and caster contact surfaces plus complete desk supports",
                    "PhysX contact reporting",
                    "head, keyboard, and overview RTX RGB cameras with saved render evidence",
                    "physical keycap to KEYDOWN/KEYUP to PTY path",
                ],
                "deferred_to_p2": [
                    "Dex3 fingertip trajectory control for pressing keys",
                    "camera-only key pose estimation and OCR verification",
                    "walking, robot-to-chair sit-contact trajectory, loaded-chair stability, and sim-to-real validation",
                ],
            },
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if succeeded else 2
    except BaseException:
        # Kit may terminate logging during close; emit the original Python
        # failure first so headless acceptance failures remain diagnosable.
        traceback.print_exc()
        raise
    finally:
        if desktop is not None:
            desktop.stop()
        if temporary is not None:
            temporary.cleanup()
        simulation_app.close()


if __name__ == "__main__":
    raise SystemExit(main())
