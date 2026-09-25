from __future__ import annotations

from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
import xml.etree.ElementTree as ET

from vskill_os.simulation.dex5_assets import COUPLED_DEX5_JOINTS, Dex5AssetError
from vskill_os.simulation.dex5_preflight import (
    EXPECTED_DEX5_FIXTURE_JOINTS,
    validate_dex5_mimic_stage,
    validate_dex5_runtime_joint_names,
)


class Dex5PreflightTests(unittest.TestCase):
    def test_runtime_requires_exact_body_both_five_finger_hands_and_two_support_axes(self) -> None:
        report = validate_dex5_runtime_joint_names(EXPECTED_DEX5_FIXTURE_JOINTS)
        self.assertEqual(report["generalized_joint_count"], 71)
        for names in (
            EXPECTED_DEX5_FIXTURE_JOINTS[:-1],
            (*EXPECTED_DEX5_FIXTURE_JOINTS, "unexpected"),
            (*EXPECTED_DEX5_FIXTURE_JOINTS, EXPECTED_DEX5_FIXTURE_JOINTS[0]),
        ):
            with self.subTest(names=names[-2:]):
                with self.assertRaisesRegex(Dex5AssetError, "runtime joint set mismatch"):
                    validate_dex5_runtime_joint_names(names)

    def test_mimic_gate_checks_each_reference_ratio_and_robot_scope(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            urdf = self._urdf(Path(directory))
            prims = [self._prim(name) for name in COUPLED_DEX5_JOINTS]
            # A different robot's unrelated mimic must not satisfy or pollute
            # the target robot's exact coupling set.
            prims.append(self._prim("other_mimic", path="/World/Other/joints/other_mimic"))
            stage = SimpleNamespace(Traverse=lambda: prims)
            self.assertEqual(validate_dex5_mimic_stage(stage, urdf=urdf)["mimic_joint_count"], 8)
            for bad in (
                self._prim(COUPLED_DEX5_JOINTS[0], gearing=-1.0),
                self._prim(COUPLED_DEX5_JOINTS[0], reference="/World/Other/joints/left_hand_index_2_joint"),
                self._prim(COUPLED_DEX5_JOINTS[0], offset=0.1),
            ):
                prims[0] = bad
                with self.assertRaisesRegex(Dex5AssetError, "differs from checked URDF"):
                    validate_dex5_mimic_stage(stage, urdf=urdf)

    def test_stage_global_count_cannot_hide_missing_or_duplicate_mimic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            urdf = self._urdf(Path(directory))
            prims = [self._prim(name) for name in COUPLED_DEX5_JOINTS[1:]]
            stage = SimpleNamespace(Traverse=lambda: prims)
            with self.assertRaisesRegex(Dex5AssetError, "missing PhysX mimic joints"):
                validate_dex5_mimic_stage(stage, urdf=urdf)
            prims.append(prims[0])
            with self.assertRaisesRegex(Dex5AssetError, "unexpected or duplicate"):
                validate_dex5_mimic_stage(stage, urdf=urdf)

    def test_hard_mimic_gate_accepts_zero_frequency_and_damping_for_all_eight(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            urdf = self._urdf(Path(directory))
            prims = [self._prim(name, natural_frequency=0.0, damping_ratio=0.0) for name in COUPLED_DEX5_JOINTS]
            stage = SimpleNamespace(Traverse=lambda: prims)
            report = validate_dex5_mimic_stage(stage, urdf=urdf, require_hard_coupling=True)
            self.assertTrue(report["hard_coupling_verified"])
            self.assertEqual(report["mimic_joint_count"], 8)
            for coupling in report["mimic_joints"].values():
                self.assertEqual(coupling["naturalFrequency"], 0.0)
                self.assertEqual(coupling["dampingRatio"], 0.0)

    def test_hard_mimic_gate_rejects_soft_missing_and_nonfinite_properties_on_any_joint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            urdf = self._urdf(Path(directory))
            prims = [self._prim(name, natural_frequency=0.0, damping_ratio=0.0) for name in COUPLED_DEX5_JOINTS]
            stage = SimpleNamespace(Traverse=lambda: prims)
            for index, name in enumerate(COUPLED_DEX5_JOINTS):
                for frequency, damping in ((25.0, 0.0), (0.0, 0.005), (None, 0.0), (0.0, None), (float("nan"), 0.0), (0.0, float("inf"))):
                    with self.subTest(joint=name, frequency=frequency, damping=damping):
                        prims[index] = self._prim(name, natural_frequency=frequency, damping_ratio=damping)
                        with self.assertRaisesRegex(Dex5AssetError, "hard PhysX coupling requires"):
                            validate_dex5_mimic_stage(stage, urdf=urdf, require_hard_coupling=True)
                        # The opt-in parameter preserves the original geometric
                        # coupling audit for models using soft mimic constraints.
                        report = validate_dex5_mimic_stage(stage, urdf=urdf)
                        self.assertFalse(report["hard_coupling_verified"])
                prims[index] = self._prim(name, natural_frequency=0.0, damping_ratio=0.0)

    @staticmethod
    def _urdf(directory: Path) -> Path:
        root = ET.Element("robot", {"name": "mimic_test"})
        for name in COUPLED_DEX5_JOINTS:
            joint = ET.SubElement(root, "joint", {"name": name})
            ET.SubElement(joint, "mimic", {"joint": name.replace("_3_joint", "_2_joint"), "multiplier": "0.8", "offset": "0"})
        path = directory / "mimics.urdf"
        ET.ElementTree(root).write(path)
        return path

    @staticmethod
    def _prim(name, *, path=None, gearing=-0.8, offset=0.0, reference=None,
              natural_frequency=25.0, damping_ratio=0.005):
        path = path or "/World/G1/joints/" + name
        reference = reference or "/World/G1/joints/" + name.replace("_3_joint", "_2_joint")
        values = {"physxMimicJoint:rotZ:gearing": gearing, "physxMimicJoint:rotZ:offset": offset,
                  "physxMimicJoint:rotZ:naturalFrequency": natural_frequency,
                  "physxMimicJoint:rotZ:dampingRatio": damping_ratio}
        return SimpleNamespace(
            GetName=lambda: name, GetPath=lambda: path,
            GetAttribute=lambda key: SimpleNamespace(Get=lambda: values.get(key)),
            GetRelationship=lambda _: SimpleNamespace(GetTargets=lambda: (reference,)),
        )


if __name__ == "__main__":
    unittest.main()
