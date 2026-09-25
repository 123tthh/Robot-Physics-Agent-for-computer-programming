"""Portable adapter/scene compatibility checks before starting Isaac/Kit."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import unittest
import xml.etree.ElementTree as ET

from vskill_os.adapters.isaac.dex5_demo import EXECUTORS, HOME_KEYS, PAD_RADIUS, TIP_OFFSETS
from vskill_os.adapters.isaac.dex5_scene_geometry import (
    Dex5SceneGeometryConfig,
    standard_keyboard_layout,
)
from vskill_os.simulation.dex5_assets import ACTIVE_DEX5_JOINTS, COUPLED_DEX5_JOINTS
from vskill_os.simulation.dex5_typing import DEX5_MONITORED_KEYS, Dex5SafetyLimits, Dex5TypingPlanner


ROOT = Path(__file__).resolve().parents[1]


class Dex5DemoContractTests(unittest.TestCase):
    def test_adapter_import_and_argument_help_do_not_require_gpu_dependencies(self) -> None:
        script = """
import sys
for name in ('isaaclab', 'isaacsim', 'omni', 'pxr', 'torch', 'numpy', 'PIL'):
    sys.modules[name] = None
from vskill_os.adapters.isaac.dex5_demo import parse_args
from vskill_os.adapters.isaac.dex5_scene_geometry import Dex5SceneGeometryConfig
sys.argv = ['dex5-demo', '--help']
parse_args()
"""
        process = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True, check=False,
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertIn("--text", process.stdout)

    def test_every_planned_executor_has_a_real_keyboard_home_target(self) -> None:
        plan = Dex5TypingPlanner().plan()
        physical_keys = {spec.name for spec in standard_keyboard_layout()}
        self.assertEqual(set(DEX5_MONITORED_KEYS), physical_keys)
        self.assertEqual(set(plan.executors), set(EXECUTORS))
        self.assertEqual(set(EXECUTORS), set(HOME_KEYS))
        self.assertTrue(set(HOME_KEYS.values()) <= physical_keys)
        # Cover the whole planner alphabet, not just the ten-finger demo line.
        expanded = Dex5TypingPlanner().plan("1234567890-=qwertyuiop[]\\asdfghjkl;'zxcvbnm,./ \n\b")
        self.assertTrue({stroke.key for stroke in expanded.strokes} <= physical_keys)

    def test_pad_and_switch_dimensions_allow_contact_press_and_release(self) -> None:
        scene = Dex5SceneGeometryConfig()
        limits = Dex5SafetyLimits()
        self.assertGreater(scene.key_travel_m, limits.press_threshold_m)
        self.assertGreater(limits.press_threshold_m, limits.release_threshold_m)
        self.assertLess(2 * PAD_RADIUS, scene.key_pitch_m - scene.key_gap_m)
        depressed_bottom = scene.key_center_z_m - scene.key_height_m / 2 - scene.key_travel_m
        self.assertGreater(depressed_bottom, scene.keyboard_shell_top_z_m)
        self.assertGreater(scene.keyboard_shell_top_z_m, scene.table_top_z_m)
        space = next(spec for spec in standard_keyboard_layout() if spec.name == "space")
        half_space_width = (space.width_units * scene.key_pitch_m - scene.key_gap_m) / 2
        self.assertLess(0.035 + PAD_RADIUS, half_space_width)

    def test_scene_rejects_nonfinite_dimensions_and_key_shell_intersection(self) -> None:
        scene = Dex5SceneGeometryConfig()
        for change in (
            {"key_travel_m": math.nan},
            {"key_pitch_m": 0},
            {"key_gap_m": scene.key_pitch_m},
            {"keyboard_shell_top_z_m": scene.key_center_z_m},
            {"keyboard_shell_top_z_m": scene.table_top_z_m},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                replace(scene, **change)


class Dex5PostureSeedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.seed = json.loads((ROOT / "configs/simulation/dex5_postures.json").read_text())

    def test_seeds_are_finite_and_only_command_active_upper_limb_coordinates(self) -> None:
        def check_finite(value):
            if isinstance(value, dict):
                for nested in value.values():
                    check_finite(nested)
            elif isinstance(value, list):
                for nested in value:
                    check_finite(nested)
            elif isinstance(value, (int, float)):
                self.assertTrue(math.isfinite(value))

        check_finite(self.seed)
        self.assertEqual(self.seed["status"], "offline_kinematic_seeds_not_dynamic_acceptance")
        self.assertRegex(self.seed["urdf_sha256"], r"^[0-9a-f]{64}$")
        for finger, offset in TIP_OFFSETS.items():
            self.assertEqual(tuple(self.seed["tip_local_offsets_m"][finger]), offset)
        allowed = set(ACTIVE_DEX5_JOINTS) | {
            f"{side}_{joint}_joint"
            for side in ("left", "right")
            for joint in ("shoulder_pitch", "shoulder_roll", "shoulder_yaw", "elbow", "wrist_roll", "wrist_pitch", "wrist_yaw")
        }
        blocks = [self.seed["safe_seating"]["joint_positions"]]
        for pose in ("raised", "home"):
            for side in ("left", "right"):
                values = self.seed[pose]["hands"][side]["joint_positions"]
                self.assertEqual(set(values), {joint for joint in allowed if joint.startswith(side + "_")})
                blocks.append(values)
        self.assertEqual(set(blocks[0]), allowed)
        for values in blocks:
            self.assertTrue(set(values) <= allowed)
            self.assertFalse(set(values) & set(COUPLED_DEX5_JOINTS))

    def test_available_composite_asset_matches_seed_hash_and_joint_limits(self) -> None:
        path = ROOT / "artifacts/isaac-dex5/assets/g1_mode15_dex5.urdf"
        if not path.exists():
            self.skipTest("optional composed Dex5 asset is not downloaded")
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), self.seed["urdf_sha256"])
        joints = {joint.attrib["name"]: joint for joint in ET.parse(path).getroot().findall("joint")}
        blocks = [self.seed["safe_seating"]["joint_positions"]]
        blocks.extend(self.seed[pose]["hands"][side]["joint_positions"] for pose in ("raised", "home") for side in ("left", "right"))
        for block in blocks:
            for name, position in block.items():
                with self.subTest(joint=name):
                    limit = joints[name].find("limit")
                    self.assertIsNotNone(limit)
                    self.assertGreaterEqual(position, float(limit.attrib["lower"]))
                    self.assertLessEqual(position, float(limit.attrib["upper"]))


if __name__ == "__main__":
    unittest.main()
