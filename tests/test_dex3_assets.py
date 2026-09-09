from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock
import xml.etree.ElementTree as ET

from vskill_os.simulation.dex3_assets import (
    EXPECTED_BODY_JOINTS,
    EXPECTED_DEX3_JOINTS,
    AssetValidationError,
    compose_mode15_g1_with_dex3,
    validate_g1_dex3_urdf,
    validate_g1_dex3_usd,
)


class Dex3AssetTests(unittest.TestCase):
    def test_mode15_body_and_standalone_hands_compose_into_valid_tree(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            body = self._write_body(root / "body")
            left = self._write_hand(root / "hands/left", "left")
            right = self._write_hand(root / "hands/right", "right")
            output = root / "generated/g1_mode15_dex3.urdf"

            report = compose_mode15_g1_with_dex3(
                g1_urdf=body,
                left_hand_urdf=left,
                right_hand_urdf=right,
                output=output,
            )

            self.assertEqual(report.body_joint_count, 29)
            self.assertEqual(report.dex3_joint_count, 14)
            self.assertEqual(report.movable_joint_count, 43)
            self.assertEqual(report.mesh_count, 3)
            generated = output.read_text(encoding="utf-8")
            self.assertNotIn("rubber_hand", generated)
            self.assertIn("../hands/left/meshes/left.stl", generated)
            self.assertIn("../hands/right/meshes/right.stl", generated)

    def test_missing_mesh_and_wrong_joint_set_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            body = self._write_body(root / "body")
            left = self._write_hand(root / "hands/left", "left")
            right = self._write_hand(root / "hands/right", "right")
            output = root / "generated/g1_mode15_dex3.urdf"
            compose_mode15_g1_with_dex3(
                g1_urdf=body,
                left_hand_urdf=left,
                right_hand_urdf=right,
                output=output,
            )

            (root / "hands/right/meshes/right.stl").unlink()
            with self.assertRaisesRegex(AssetValidationError, "missing mesh"):
                validate_g1_dex3_urdf(output)

            tree = ET.parse(output)
            joint = tree.getroot().find("joint[@name='left_hand_index_1_joint']")
            self.assertIsNotNone(joint)
            assert joint is not None
            joint.attrib["name"] = "unexpected_joint"
            tree.write(output, encoding="utf-8", xml_declaration=True)
            (root / "hands/right/meshes/right.stl").write_bytes(b"mesh")
            with self.assertRaisesRegex(AssetValidationError, "joint set mismatch"):
                validate_g1_dex3_urdf(output)

    def test_usd_header_and_digest_are_checked_without_openusd(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            usd = Path(directory) / "reference.usda"
            usd.write_text("#usda 1.0\n", encoding="utf-8")
            digest = hashlib.sha256(usd.read_bytes()).hexdigest()

            with mock.patch.dict("sys.modules", {"pxr": None}):
                report = validate_g1_dex3_usd(usd, expected_sha256=digest)
            self.assertEqual(report.format, "usda")
            with self.assertRaisesRegex(AssetValidationError, "SHA-256 mismatch"):
                validate_g1_dex3_usd(usd, expected_sha256="0" * 64)

    @staticmethod
    def _write_body(directory: Path) -> Path:
        mesh = directory / "meshes/body.stl"
        mesh.parent.mkdir(parents=True)
        mesh.write_bytes(b"body")
        robot = ET.Element("robot", {"name": "g1_29dof_mode_15"})
        ET.SubElement(robot, "link", {"name": "pelvis"})
        for index, joint_name in enumerate(EXPECTED_BODY_JOINTS):
            if joint_name == "left_wrist_yaw_joint":
                child = "left_wrist_yaw_link"
            elif joint_name == "right_wrist_yaw_joint":
                child = "right_wrist_yaw_link"
            else:
                child = f"body_link_{index}"
            link = ET.SubElement(robot, "link", {"name": child})
            if index == 0:
                visual = ET.SubElement(link, "visual")
                geometry = ET.SubElement(visual, "geometry")
                ET.SubElement(geometry, "mesh", {"filename": "meshes/body.stl"})
            joint = ET.SubElement(robot, "joint", {"name": joint_name, "type": "revolute"})
            ET.SubElement(joint, "parent", {"link": "pelvis"})
            ET.SubElement(joint, "child", {"link": child})
            ET.SubElement(
                joint,
                "limit",
                {"lower": "-1", "upper": "1", "effort": "1", "velocity": "1"},
            )
        for side in ("left", "right"):
            rubber = f"{side}_rubber_hand"
            ET.SubElement(robot, "link", {"name": rubber})
            joint = ET.SubElement(
                robot,
                "joint",
                {"name": f"{side}_hand_palm_joint", "type": "fixed"},
            )
            ET.SubElement(joint, "parent", {"link": f"{side}_wrist_yaw_link"})
            ET.SubElement(joint, "child", {"link": rubber})
        path = directory / "g1.urdf"
        ET.ElementTree(robot).write(path, encoding="utf-8", xml_declaration=True)
        return path

    @staticmethod
    def _write_hand(directory: Path, side: str) -> Path:
        mesh = directory / f"meshes/{side}.stl"
        mesh.parent.mkdir(parents=True)
        mesh.write_bytes(side.encode("ascii"))
        robot = ET.Element("robot", {"name": f"dex3_{side}"})
        palm = ET.SubElement(robot, "link", {"name": f"{side}_hand_palm_link"})
        visual = ET.SubElement(palm, "visual")
        geometry = ET.SubElement(visual, "geometry")
        ET.SubElement(geometry, "mesh", {"filename": f"meshes/{side}.stl"})
        parent = f"{side}_hand_palm_link"
        for index, joint_name in enumerate(
            name for name in EXPECTED_DEX3_JOINTS if name.startswith(side)
        ):
            child = f"{side}_finger_link_{index}"
            ET.SubElement(robot, "link", {"name": child})
            joint = ET.SubElement(robot, "joint", {"name": joint_name, "type": "revolute"})
            ET.SubElement(joint, "parent", {"link": parent})
            ET.SubElement(joint, "child", {"link": child})
            ET.SubElement(
                joint,
                "limit",
                {"lower": "-1", "upper": "1", "effort": "1", "velocity": "1"},
            )
            parent = child
        path = directory / f"dex3_{side}.urdf"
        ET.ElementTree(robot).write(path, encoding="utf-8", xml_declaration=True)
        return path


if __name__ == "__main__":
    unittest.main()
