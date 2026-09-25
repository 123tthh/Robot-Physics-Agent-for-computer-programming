from __future__ import annotations

from dataclasses import replace
import hashlib
import math
from pathlib import Path
import random
import tempfile
import unittest

from vskill_os.control.commands import RootVelocityCommand
from vskill_os.control.standing_locomotion import (
    KinematicWalkingAdapter, LocomotionObservation, NavigationFailure,
    PolicyCheckpoint, SE2Goal, StandingLocomotionGuard,
    StandingNavigationLimits, StandingNavigationSkill, StandingNavigationVerifier,
    angle_error,
)
from vskill_os.robot.g1 import G1Joint


class StandingLocomotionTests(unittest.TestCase):
    def test_cpu_navigation_is_reproducible_arrives_and_stabilizes_without_physical_claim(self):
        goal = SE2Goal(0.4, 0.1, 0.2)
        first = StandingNavigationSkill(KinematicWalkingAdapter(seed=7)).run(goal)
        second = StandingNavigationSkill(KinematicWalkingAdapter(seed=7)).run(goal)
        self.assertTrue(first.succeeded, first.to_dict())
        self.assertTrue(first.arrived and first.stabilized and first.stop_verified)
        self.assertFalse(first.physical_walking_verified)
        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertGreater(first.verification.path_length_m, 0.35)
        self.assertFalse(first.to_dict()["joint_commands_exposed"])

    def test_commands_bound_vector_speed_acceleration_and_shortest_yaw(self):
        limits = StandingNavigationLimits()
        skill = StandingNavigationSkill(KinematicWalkingAdapter(), limits)
        obs = LocomotionObservation(0, 0, 0, 0, math.pi - 0.05)
        command = RootVelocityCommand()
        for _ in range(100):
            next_command = skill.command_for(SE2Goal(5, 5, -math.pi + 0.05), obs, command)
            self.assertLessEqual(math.hypot(next_command.vx_mps, next_command.vy_mps), limits.max_linear_mps + 1e-9)
            self.assertLessEqual(math.hypot(next_command.vx_mps - command.vx_mps, next_command.vy_mps - command.vy_mps), limits.max_linear_acceleration_mps2 * limits.period_s + 1e-9)
            self.assertLessEqual(abs(next_command.yaw_rate_rps - command.yaw_rate_rps), limits.max_yaw_acceleration_rps2 * limits.period_s + 1e-9)
            command = next_command
        self.assertGreater(command.yaw_rate_rps, 0)
        self.assertAlmostEqual(angle_error(-math.pi + 0.05, math.pi - 0.05), 0.1)

    def test_seed_does_not_touch_global_rng(self):
        state = random.getstate()
        KinematicWalkingAdapter(seed=99)
        self.assertEqual(state, random.getstate())

    def test_contact_loss_triggers_stop_and_cannot_verify_stop(self):
        class NoSupport(KinematicWalkingAdapter):
            def observe(self):
                obs = super().observe()
                return replace(obs, left_foot_contact=False, right_foot_contact=False) if self.tick >= 4 else obs
        adapter = NoSupport()
        result = StandingNavigationSkill(adapter).run(SE2Goal(0.4, 0))
        self.assertEqual(result.failure_code, "FOOT_CONTACT_LOST")
        self.assertTrue(result.stop_requested)
        self.assertEqual(adapter.stop_calls, 1)
        self.assertFalse(result.stop_verified)
        self.assertFalse(result.physical_walking_verified)

    def test_tilt_stance_slip_collision_and_controller_claims_fail(self):
        for fields, code in (({"torso_tilt_rad": 0.5}, "STANDING_UNSTABLE"),
                             ({"left_foot_slip_mps": 0.2}, "STANCE_SLIP"),
                             ({"collision": True}, "COLLISION"),
                             ({"source": "controller_claim"}, "UNTRUSTED_LOCOMOTION_EVIDENCE")):
            class Faulty(KinematicWalkingAdapter):
                def observe(self):
                    obs = super().observe()
                    return replace(obs, **fields) if self.tick >= 3 else obs
            with self.subTest(code=code):
                result = StandingNavigationSkill(Faulty()).run(SE2Goal(0.4, 0))
                self.assertEqual(result.failure_code, code)
                self.assertFalse(result.succeeded)
                self.assertTrue(result.stop_requested)

    def test_teleport_marker_and_unmarked_pose_jump_are_rejected(self):
        for marked, code in ((True, "TELEPORT_NOT_WALKING"), (False, "POSE_DISCONTINUITY")):
            class Jump(KinematicWalkingAdapter):
                def step(self, command, dt_s):
                    obs = super().step(command, dt_s)
                    return replace(obs, x_m=0.4, teleported=marked) if self.tick == 4 else obs
            result = StandingNavigationSkill(Jump()).run(SE2Goal(0.4, 0))
            self.assertEqual(result.failure_code, code)
            self.assertFalse(result.physical_walking_verified)

    def test_timeout_and_stale_samples_fail_and_request_stop(self):
        class Stationary(KinematicWalkingAdapter):
            def step(self, command, dt_s):
                return super().step(RootVelocityCommand(), dt_s)
        adapter = Stationary()
        result = StandingNavigationSkill(adapter).run(SE2Goal(0.4, 0, max_duration_s=0.6))
        self.assertEqual(result.failure_code, "NAVIGATION_TIMEOUT")
        self.assertTrue(result.stop_verified)
        self.assertEqual(adapter.stop_calls, 1)
        class Stale(KinematicWalkingAdapter):
            def step(self, command, dt_s):
                return self.observe()
        stale = StandingNavigationSkill(Stale()).run(SE2Goal(0.4, 0))
        self.assertEqual(stale.failure_code, "STALE_OBSERVATION")
        self.assertFalse(stale.stop_verified)

    def test_cancellation_after_motion_requests_and_verifies_stop(self):
        class Cancel(KinematicWalkingAdapter):
            def step(self, command, dt_s):
                result = super().step(command, dt_s)
                if self.tick == 12:
                    raise KeyboardInterrupt()
                return result
        adapter = Cancel()
        result = StandingNavigationSkill(adapter).run(SE2Goal(0.4, 0))
        self.assertEqual(result.failure_code, "CANCELLED")
        self.assertTrue(result.stop_verified)
        self.assertFalse(result.succeeded)
        self.assertEqual(adapter.stop_calls, 1)

    def test_single_final_pose_is_not_arrival_and_stabilization_evidence(self):
        goal = SE2Goal(0.4, 0)
        result = StandingNavigationVerifier().verify(goal, (LocomotionObservation(0, 0, 0.4, 0, 0),),
            limits=StandingNavigationLimits(), evidence_mode="synthetic")
        self.assertTrue(result.arrived)
        self.assertFalse(result.stabilized)
        self.assertFalse(result.accepted)

    def test_missing_physical_checkpoint_blocks_motion(self):
        class Unpinned(KinematicWalkingAdapter):
            evidence_mode = "physical"
        adapter = Unpinned()
        result = StandingNavigationSkill(adapter).run(SE2Goal(0.4, 0))
        self.assertEqual(result.failure_code, "POLICY_UNVERIFIED")
        self.assertEqual(adapter.tick, 0)
        self.assertTrue(result.stop_requested)
        self.assertFalse(result.physical_walking_verified)

    def test_checkpoint_hash_and_mode_payload_compatibility(self):
        with tempfile.TemporaryDirectory() as tmp:
            policy, robot = Path(tmp) / "policy.onnx", Path(tmp) / "robot.usd"
            policy.write_bytes(b"test-only artifact, not a model")
            robot.write_bytes(b"test-only robot")
            schema = (("projected_gravity", 3), ("joint_positions", 29), ("joint_velocities", 29), ("last_action", 29), ("velocity_command", 3))
            contract = PolicyCheckpoint(str(policy), hashlib.sha256(policy.read_bytes()).hexdigest(),
                hashlib.sha256(robot.read_bytes()).hexdigest(), tuple(j.name.lower() for j in G1Joint),
                schema, (1.0,) * 93, (0.25,) * 29, (0.0,) * 29, "6.0.1", "a" * 40)
            self.assertTrue(contract.validate(robot, runtime_version="6.0.1", period_s=0.02)["validated"])
            with self.assertRaises(NavigationFailure):
                replace(contract, mode_machine=5).validate(robot, runtime_version="6.0.1", period_s=0.02)
            with self.assertRaises(NavigationFailure):
                replace(contract, hand_payload="inspire").validate(robot, runtime_version="6.0.1", period_s=0.02)
            policy.write_bytes(b"tampered")
            with self.assertRaises(NavigationFailure) as caught:
                contract.validate(robot, runtime_version="6.0.1", period_s=0.02)
            self.assertEqual(caught.exception.code, "POLICY_ASSET_MISMATCH")

    def test_invalid_sensor_payload_fails_without_escaping_cleanup(self):
        class Malformed(KinematicWalkingAdapter):
            def observe(self):
                return None
        result = StandingNavigationSkill(Malformed()).run(SE2Goal(0.4, 0))
        self.assertEqual(result.failure_code, "INVALID_OBSERVATION")
        self.assertFalse(result.stop_verified)
        self.assertFalse(result.succeeded)


if __name__ == "__main__":
    unittest.main()
