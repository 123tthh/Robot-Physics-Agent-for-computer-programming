from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import xml.etree.ElementTree as ET

from vskill_os.simulation.dex5_assets import (
    ACTIVE_DEX5_JOINTS,
    COUPLED_DEX5_JOINTS,
    EXPECTED_BODY_JOINTS,
    EXPECTED_DEX5_JOINTS,
    Dex5AssetError,
    SEATING_FIXTURE_JOINT,
    SEATING_FIXTURE_SLIDE_JOINT,
    UNITREE_COMMIT,
    audit_dex5_hand,
    audit_pinned_assets,
    compose_mode15_g1_with_dex5,
    fetch_pinned_assets,
    validate_g1_dex5_urdf,
)


class Dex5AssetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.body = self._body(self.root / "body")
        self.left = self._hand(self.root / "left", "L")
        self.right = self._hand(self.root / "right", "R")
        self.output = self.root / "generated/dex5.urdf"

    def _compose(self, fixture: bool = False):
        return compose_mode15_g1_with_dex5(
            g1_urdf=self.body, left_hand_urdf=self.left,
            right_hand_urdf=self.right, output=self.output, seating_fixture=fixture,
        )

    def test_known_upstream_defects_are_reported_and_simulation_edits_are_auditable(self) -> None:
        before = self.right.read_bytes()
        audit = audit_dex5_hand(self.right, "right")
        self.assertEqual(len(audit["zero_effort_joints"]), 16)
        self.assertEqual(audit["zero_velocity_joints"], ["Roll_41R", "Roll_51R"])
        self.assertEqual(audit["source_mimic_count"], 0)
        report = self._compose()
        self.assertEqual((report.body_joint_count, report.dex5_coordinate_count), (29, 40))
        self.assertEqual((report.dex5_active_joint_count, report.dex5_coupled_joint_count), (32, 8))
        self.assertEqual(report.fixture_joint_count, 0)
        self.assertEqual(self.right.read_bytes(), before)
        root = ET.parse(self.output).getroot()
        self.assertIsNotNone(root.find("joint[@name='left_hand_ring_0_joint']"))
        for name in COUPLED_DEX5_JOINTS:
            mimic = root.find(f"joint[@name='{name}']/mimic")
            self.assertIsNotNone(mimic)
            assert mimic is not None
            self.assertAlmostEqual(float(mimic.attrib["multiplier"]), 0.8)
        self.assertEqual(self._compose().sha256, report.sha256)

    def test_fixture_has_two_explicit_extra_non_robot_coordinates(self) -> None:
        report = self._compose(fixture=True)
        self.assertEqual(report.fixture_joint_count, 2)
        root = ET.parse(self.output).getroot()
        lift = root.find(f"joint[@name='{SEATING_FIXTURE_JOINT}']")
        self.assertIsNotNone(lift)
        assert lift is not None
        self.assertEqual(lift.attrib["type"], "prismatic")
        self.assertEqual(lift.find("parent").attrib["link"], "seating_fixture_carriage")
        self.assertEqual(lift.find("child").attrib["link"], "pelvis")
        self.assertEqual(float(lift.find("limit").attrib["velocity"]), 0.10)
        slide = root.find(f"joint[@name='{SEATING_FIXTURE_SLIDE_JOINT}']")
        self.assertIsNotNone(slide)
        assert slide is not None
        self.assertEqual(slide.attrib["type"], "prismatic")
        self.assertEqual(slide.find("parent").attrib["link"], "seating_fixture_base")
        self.assertEqual(slide.find("child").attrib["link"], "seating_fixture_carriage")
        self.assertEqual(slide.find("axis").attrib["xyz"], "1 0 0")
        self.assertEqual(float(slide.find("limit").attrib["lower"]), -0.30)
        self.assertEqual(float(slide.find("limit").attrib["velocity"]), 0.15)
        self.assertNotIn(SEATING_FIXTURE_JOINT, EXPECTED_DEX5_JOINTS)
        self.assertNotIn(SEATING_FIXTURE_SLIDE_JOINT, EXPECTED_DEX5_JOINTS)
        self.assertEqual(len(ACTIVE_DEX5_JOINTS), 32)

    def test_half_present_support_fixture_is_rejected(self) -> None:
        self._compose(fixture=True)
        tree = ET.parse(self.output)
        slide = tree.getroot().find(f"joint[@name='{SEATING_FIXTURE_SLIDE_JOINT}']")
        assert slide is not None
        slide.set("type", "fixed")
        tree.write(self.output)
        with self.assertRaisesRegex(Dex5AssetError, "joint set mismatch"):
            validate_g1_dex5_urdf(self.output)

    def test_wrong_support_axis_is_rejected(self) -> None:
        self._compose(fixture=True)
        tree = ET.parse(self.output)
        axis = tree.getroot().find(f"joint[@name='{SEATING_FIXTURE_SLIDE_JOINT}']/axis")
        assert axis is not None
        axis.set("xyz", "0 1 0")
        tree.write(self.output)
        with self.assertRaisesRegex(Dex5AssetError, "invalid declared support fixture"):
            validate_g1_dex5_urdf(self.output)

    def test_a_three_finger_substitute_is_rejected(self) -> None:
        tree = ET.parse(self.left)
        root = tree.getroot()
        for joint in tuple(root.findall("joint")):
            if "4" in joint.attrib["name"] or "5" in joint.attrib["name"]:
                root.remove(joint)
        tree.write(self.left)
        with self.assertRaisesRegex(Dex5AssetError, "official Dex5-1 joint set"):
            self._compose()

    def test_missing_mesh_bad_coupling_and_duplicate_parent_are_rejected(self) -> None:
        self._compose()
        mesh = self.root / "right/meshes/hand.stl"
        mesh.unlink()
        with self.assertRaisesRegex(Dex5AssetError, "missing mesh"):
            validate_g1_dex5_urdf(self.output)
        mesh.write_bytes(b"fixture mesh")
        tree = ET.parse(self.output)
        mimic = tree.getroot().find("joint[@name='left_hand_index_3_joint']/mimic")
        assert mimic is not None
        mimic.set("multiplier", "1")
        tree.write(self.output)
        with self.assertRaisesRegex(Dex5AssetError, "invalid simulation coupling"):
            validate_g1_dex5_urdf(self.output)
        self._compose()
        tree = ET.parse(self.output)
        parent = tree.getroot().find("joint[@name='left_hand_index_2_joint']/parent")
        assert parent is not None
        parent.set("link", "left_hand_index_2_link")
        tree.write(self.output)
        with self.assertRaisesRegex(Dex5AssetError, "parent-child graph"):
            validate_g1_dex5_urdf(self.output)

    def test_pin_checks_reject_tampering_and_fetch_refuses_overwrite(self) -> None:
        data = b"official pinned fixture"
        artifact = self.root / "mesh.stl"
        artifact.write_bytes(b"changed")
        manifest = self._manifest("mesh.stl", data)
        with self.assertRaisesRegex(Dex5AssetError, "SHA-256/size mismatch"):
            audit_pinned_assets(manifest=manifest, asset_root=self.root)
        with mock.patch("urllib.request.urlopen") as request:
            with self.assertRaisesRegex(Dex5AssetError, "existing source differs"):
                fetch_pinned_assets(manifest=manifest, asset_root=self.root)
            request.assert_not_called()

    def test_fetch_rejects_corrupt_download_and_path_escape(self) -> None:
        manifest = self._manifest("new.stl", b"expected")
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = b"corrupt"
        with mock.patch("urllib.request.urlopen", return_value=response):
            with self.assertRaisesRegex(Dex5AssetError, "download SHA-256/size mismatch"):
                fetch_pinned_assets(manifest=manifest, asset_root=self.root)
        self.assertFalse((self.root / "new.stl").exists())
        manifest = self._manifest("../escape.stl", b"bad")
        with self.assertRaisesRegex(Dex5AssetError, "escapes asset root"):
            fetch_pinned_assets(manifest=manifest, asset_root=self.root)

    def _manifest(self, path: str, content: bytes) -> Path:
        manifest = self.root / "manifest.json"
        manifest.write_text(json.dumps({
            "repository": "https://github.com/unitreerobotics/unitree_ros", "commit": UNITREE_COMMIT,
            "license": "BSD-3-Clause", "files": [{"path": path, "sha256": hashlib.sha256(content).hexdigest(), "size_bytes": len(content)}],
        }))
        return manifest

    @staticmethod
    def _body(directory: Path) -> Path:
        directory.mkdir()
        root = ET.Element("robot", {"name": "g1_29dof_mode_15"})
        ET.SubElement(root, "link", {"name": "pelvis"})
        for name in EXPECTED_BODY_JOINTS:
            child = name.replace("_joint", "_link")
            ET.SubElement(root, "link", {"name": child})
            joint = ET.SubElement(root, "joint", {"name": name, "type": "revolute"})
            ET.SubElement(joint, "parent", {"link": "pelvis"})
            ET.SubElement(joint, "child", {"link": child})
            ET.SubElement(joint, "limit", {"lower": "-2", "upper": "2", "effort": "100", "velocity": "10"})
        for side in ("left", "right"):
            ET.SubElement(root, "link", {"name": f"{side}_rubber_hand"})
            joint = ET.SubElement(root, "joint", {"name": f"{side}_hand_palm_joint", "type": "fixed"})
            ET.SubElement(joint, "parent", {"link": f"{side}_wrist_yaw_link"})
            ET.SubElement(joint, "child", {"link": f"{side}_rubber_hand"})
        path = directory / "body.urdf"
        ET.ElementTree(root).write(path)
        return path

    @staticmethod
    def _hand(directory: Path, side: str) -> Path:
        (directory / "meshes").mkdir(parents=True)
        (directory / "meshes/hand.stl").write_bytes(b"fixture mesh")
        root = ET.Element("robot", {"name": f"Dex5-URDF-{side}"})
        base = "base_link00L" if side == "L" else "base_link00"
        ET.SubElement(root, "link", {"name": base})
        for digit in range(1, 6):
            for segment in range(1, 5):
                name = f"Link_{digit}{segment}{side}"
                link = ET.SubElement(root, "link", {"name": name})
                geometry = ET.SubElement(ET.SubElement(link, "visual"), "geometry")
                ET.SubElement(geometry, "mesh", {"filename": "meshes/hand.stl"})
                prefix = ("Yaw", "Roll", "Pitch", "Pitch")[segment - 1] if digit == 1 else ("Roll" if segment == 1 else "Pitch")
                if (side, digit, segment) == ("L", 4, 1):
                    prefix = "Link"
                joint = ET.SubElement(root, "joint", {"name": f"{prefix}_{digit}{segment}{side}", "type": "revolute"})
                ET.SubElement(joint, "parent", {"link": base if segment == 1 else f"Link_{digit}{segment-1}{side}"})
                ET.SubElement(joint, "child", {"link": name})
                ET.SubElement(joint, "limit", {
                    "lower": "0", "upper": "0.8" if segment == 4 else "1.0",
                    "effort": "0" if side == "R" and digit > 1 else "0.93",
                    "velocity": "0" if side == "R" and digit in (4, 5) and segment == 1 else "0.76794",
                })
        path = directory / "hand.urdf"
        ET.ElementTree(root).write(path)
        return path


if __name__ == "__main__":
    unittest.main()
