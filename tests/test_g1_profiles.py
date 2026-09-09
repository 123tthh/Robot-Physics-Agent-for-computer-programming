from __future__ import annotations

import unittest
from dataclasses import replace

from vskill_os.robot.g1 import (
    G1DeploymentConfig,
    G1Joint,
    G1PolicyManifest,
    G1_5010_PROFILES,
)
from vskill_os.robot.seated_policy import SeatedLocomotionPolicyManifest
from vskill_os.simulation.app import project_root


class G1ProfileTests(unittest.TestCase):
    def test_all_published_29dof_5010_modes_are_explicit(self) -> None:
        self.assertEqual(set(G1_5010_PROFILES), {13, 14, 15, 16, 18})
        self.assertEqual(len(G1Joint), 29)
        self.assertEqual(G1Joint.RIGHT_WRIST_YAW, 28)
        self.assertTrue(G1_5010_PROFILES[14].waist_locked)
        self.assertFalse(G1_5010_PROFILES[15].waist_locked)
        self.assertNotEqual(
            G1_5010_PROFILES[13].hip_pitch_gear_ratio,
            G1_5010_PROFILES[15].hip_pitch_gear_ratio,
        )

    def test_unpinned_hardware_profile_is_rejected(self) -> None:
        config = G1DeploymentConfig(selected_mode_machine=None)
        with self.assertRaisesRegex(ValueError, "selected_mode_machine is unset"):
            config.hardware_profile(observed_mode_machine=15)

    def test_hardware_observation_must_match_pinned_profile(self) -> None:
        config = G1DeploymentConfig(selected_mode_machine=15)
        with self.assertRaisesRegex(ValueError, "mismatch"):
            config.hardware_profile(observed_mode_machine=13)
        profile = config.hardware_profile(
            observed_mode_machine=15,
            observed_body_dof=29,
            observed_wrist_motor="5010",
        )
        self.assertIn("mode15", profile.compatibility_key)

    def test_policy_manifest_is_bound_to_exact_profile(self) -> None:
        order = [joint.name.lower() for joint in G1Joint]
        manifest = G1PolicyManifest.from_dict(
            {
                "robot": "unitree_g1",
                "mode_machine": 15,
                "body_dof": 29,
                "wrist_motor": "5010",
                "waist_locked": False,
                "hip_gear_ratio": [22.5, 22.5],
                "joint_order": order,
                "action_type": "root_velocity",
                "policy_hz": 50,
                "low_level_hz": 500,
                "simulator": "isaac_lab",
                "simulator_version": "6.0",
                "training_commit": "abc123",
            }
        )
        manifest.validate_for(G1_5010_PROFILES[15])
        with self.assertRaisesRegex(ValueError, "mode_machine differs"):
            manifest.validate_for(G1_5010_PROFILES[13])

    def test_seated_policy_manifest_pins_asymmetric_observation_schema(self) -> None:
        manifest = SeatedLocomotionPolicyManifest.from_json(
            project_root()
            / "configs/robots/g1_29dof_mode15_seated_policy.example.json"
        )
        manifest.validate_for(G1_5010_PROFILES[15])

        self.assertEqual(manifest.actor_dimension, 96)
        self.assertEqual(manifest.critic_dimension, 217)
        self.assertEqual(manifest.training_risk_flags, ())

        leaking = replace(
            manifest,
            actor_observations=manifest.actor_observations
            + (("chair_relative_pose", 9),),
        )
        with self.assertRaisesRegex(ValueError, "proprioception"):
            leaking.validate_for(G1_5010_PROFILES[15])

        risky = replace(
            manifest,
            symmetry_regularization=False,
            foot_slip_regularization=True,
            command_curriculum=False,
        )
        self.assertEqual(
            risky.training_risk_flags,
            ("FOOT_SLIP_ONLY_STATIONARY_LOCAL_OPTIMUM",),
        )


if __name__ == "__main__":
    unittest.main()
