"""CPU contract tests. Fabricated samples below are never physical evidence."""

from dataclasses import replace
import hashlib
import json
import math
from pathlib import Path
import tempfile
import unittest

from vskill_os.adapters.isaac.learning.config import LEFT_JOINTS, SingleHandConfig
from vskill_os.adapters.isaac.learning.verify import verify_bundle
from vskill_os.adapters.isaac.learning.runner import verify_run
from vskill_os.adapters.isaac.learning.provenance import RECORDING_FILES
from vskill_os.learning.environment import (
    ActionRejected, CleanupRecord, JointControlSpec, JointTargetAction,
    JointTargetGuard, PolicyObservation, ResetSpec,
)
from vskill_os.learning.physical import (
    AppliedAction, CoordinateLimit, PhysicsSample, PhysicsSafetyMonitor, PhysicsTrace,
)
from vskill_os.learning.physical_evaluation import check_physical_episode
from vskill_os.learning.replay import build_replay_case


class ActionGuardTests(unittest.TestCase):
    def setUp(self):
        self.spec = JointControlSpec(("finger", "arm"), (-1., -2.), (1., 2.), (0.3, 0.2), ("finger",), 0.02)
        self.action = JointTargetAction(self.spec.joint_names, (0.5, 0.), .02, 1.02, "owner", 4)
        self.guard = JointTargetGuard(self.spec)

    def apply(self, action=None, targets=(0., 0.), **kwargs):
        return self.guard.apply(targets, action or self.action, time_s=kwargs.get("time_s", 1.),
                                owner_token="owner", expected_sequence=4)

    def test_limits_are_enforced_before_execution_and_hold_masked_joints(self):
        self.assertEqual(self.apply(), (0.006, 0.))
        self.assertEqual(self.apply(targets=(.999, 1.5)), (1., 1.5))
        self.assertEqual(self.apply(replace(self.action, delta_rad=(-50., 0.))), (-.006, 0.))

    def test_owner_mapping_mask_sequence_and_deadline_reject(self):
        for changes, code in (
            ({"owner_token": "previous-episode"}, "CONTROL_OWNERSHIP"),
            ({"joint_names": ("arm", "finger")}, "JOINT_MAPPING"),
            ({"delta_rad": (0., .01)}, "UNOWNED_JOINT"),
            ({"sequence": 3}, "ACTION_SEQUENCE"),
            ({"valid_until_s": 1.01}, "ACTION_EXPIRED"),
            ({"duration_s": .04}, "ACTION_EXPIRED"),
        ):
            with self.subTest(code=code), self.assertRaisesRegex(ActionRejected, code):
                self.apply(replace(self.action, **changes))

    def test_nonfinite_or_outside_limit_state_cannot_bypass_rate_guard(self):
        for targets in ((math.nan, 0.), (math.inf, 0.), (2., 0.)):
            with self.subTest(targets=targets), self.assertRaisesRegex(ActionRejected, "JOINT_STATE"):
                self.apply(targets=targets)
        with self.assertRaisesRegex(ActionRejected, "JOINT_STATE"):
            self.apply(time_s=math.nan)
        for value in (math.nan, math.inf):
            with self.assertRaises(ValueError):
                replace(self.action, delta_rad=(value, 0.))

    def test_contract_dimensions_safe_ids_and_no_oracle_observation(self):
        self.assertEqual(len(LEFT_JOINTS), 23)
        self.assertEqual(len(set(LEFT_JOINTS)), 23)
        self.assertEqual(SingleHandConfig().enabled_joints, ("left_hand_index_1_joint",))
        for name in ("../episode", "/tmp/episode", "", "a/child"):
            with self.assertRaises(ValueError):
                ResetSpec(name)
        with self.assertRaises(ValueError):
            ResetSpec("valid-id", seed=2**63)
        self.assertFalse({"text", "text_oracle", "verifier", "success", "info"} & set(PolicyObservation.__dataclass_fields__))
        with self.assertRaises(ValueError):
            CleanupRecord("env", "ep", (), physical_release_claimed=True)

    def test_zero_duration_is_valid_only_for_an_aborted_episode(self):
        ep = build_replay_case("correct_press").episode
        for termination in ("cancelled", "safety_stop"):
            self.assertEqual(replace(ep, end_time_s=ep.start_time_s, termination=termination).end_time_s, ep.start_time_s)
        for termination in ("finish_requested", "time_limit"):
            with self.assertRaises(ValueError):
                replace(ep, end_time_s=ep.start_time_s, termination=termination)


class RawConsistencyTests(unittest.TestCase):
    def setUp(self):
        # Deliberately use a synthetic single-coordinate fixture to unit-test
        # arithmetic only. The bundle verifier separately requires OEM 69-DOF
        # assets, 23 controls, exact source files and raw Isaac recordings.
        replay = build_replay_case("correct_press")
        self.task = replay.task
        ep = replay.episode
        def stream(records, source):
            return tuple(replace(record, stamp=replace(record.stamp, clock_domain="isaac_sim_seconds", source=source))
                         for record in records)
        self.episode = replace(ep, clock_domain="isaac_sim_seconds",
            manifest=replace(ep.manifest, backend="isaac_sim_5_1", evidence_scope="isaac_physx_single_hand_v1"),
            observations=stream(ep.observations, "isaac.physx"), events=stream(ep.events, "isaac.keyboard_bridge"),
            text_observations=stream(ep.text_observations, "isaac.text_oracle"), screen_observations=(), verification_requests=())
        self.spec = JointControlSpec(self.task.joint_names, (-1.,), (1.,), (.3,), self.task.joint_names, .01)
        self.limits = (CoordinateLimit(self.task.joint_names[0], "rad", -1., 1., 8.),)
        self.centers = (0., 0., .675) * len(self.task.monitored_keys)
        samples = tuple(PhysicsSample(
            frame.stamp, tuple(j.position_rad for j in frame.joints), tuple(j.velocity_rad_s for j in frame.joints),
            tuple(j.commanded_target_rad for j in frame.joints), tuple(-k.travel_m for k in frame.keys),
            (0.,)*len(frame.keys), tuple(v for k in frame.keys for v in (0., 0., k.normal_force_n)),
            tuple(v for k in frame.keys for v in (0., 0., .675-k.travel_m)),
            (0., 0., .7), (0., 0., .625), (1., 0., 0., 0.),
        ) for frame in self.episode.observations)
        actions = tuple(AppliedAction(frame.stamp.capture_time_s,
            JointTargetAction(self.spec.joint_names, (0.,), .01, frame.stamp.capture_time_s+.01, "unit-owner", i),
            (0.,), (0.,), None) for i, frame in enumerate(self.episode.observations[:-1]))
        self.trace = PhysicsTrace("vf-physical/1", "isaac_physx_single_hand_v1", .01, self.spec, "unit-owner",
            self.limits, self.task.monitored_keys, (0.,), (0., 0., .625), 12., .03,
            samples, actions, (), self.episode.sha256())

    def check(self, trace=None, episode=None, **kwargs):
        episode = episode or self.episode
        trace = replace(trace or self.trace, episode_sha256=episode.sha256())
        return check_physical_episode(self.task, episode, trace, control=self.spec, limits=self.limits,
                                      key_body_centers_xyz_m=self.centers, physics_dt_s=.01, **kwargs)

    def assert_rejected(self, expected, **kwargs):
        result = self.check(**kwargs)
        self.assertFalse(result.consistent)
        self.assertIn(expected, {f.code for f in result.findings}, result.to_json())

    def test_consistent_fixture_and_serialization(self):
        result = self.check()
        self.assertTrue(result.consistent, result.to_json())
        self.assertEqual(result.completed_press_release_cycles, 1)
        self.assertEqual(PhysicsTrace.from_json(self.trace.to_json()), self.trace)

    def test_changed_derived_key_and_world_pose_are_rejected(self):
        frame = self.episode.observations[2]
        changed = replace(frame, keys=tuple(replace(k, travel_m=0.) for k in frame.keys))
        self.assert_rejected("RAW_DERIVATION_MISMATCH", episode=replace(self.episode,
            observations=(*self.episode.observations[:2], changed, *self.episode.observations[3:])))
        raw = self.trace.samples[0]
        shifted = replace(raw, key_body_positions_xyz_m=tuple(v-.1 for v in raw.key_body_positions_xyz_m))
        self.assert_rejected("KEYBOARD_POSE_MISMATCH", trace=replace(self.trace, samples=(shifted, *self.trace.samples[1:])))

    def test_missing_raw_vectors_fail_closed_without_index_error(self):
        for field in ("joint_velocities", "joint_targets", "key_force_vectors_n", "key_body_positions_xyz_m"):
            with self.subTest(field=field):
                raw = replace(self.trace.samples[0], **{field: ()})
                self.assert_rejected("INVALID_PHYSICS_SAMPLE", trace=replace(self.trace, samples=(raw, *self.trace.samples[1:])))

    def test_unlogged_command_and_tampered_guard_output_reject(self):
        raw = replace(self.trace.samples[1], joint_targets=(.4,))
        self.assert_rejected("ACTION_TARGET_MISMATCH", trace=replace(self.trace, samples=(self.trace.samples[0], raw, *self.trace.samples[2:])))
        action = replace(self.trace.actions[0], applied_targets_rad=(.4,))
        self.assert_rejected("ACTION_TARGET_MISMATCH", trace=replace(self.trace, actions=(action, *self.trace.actions[1:])))
        action = replace(self.trace.actions[0], previous_targets_rad=())
        self.assert_rejected("ACTION_SEQUENCE", trace=replace(self.trace, actions=(action, *self.trace.actions[1:])))

    def test_undeclared_initial_target_is_rejected(self):
        raw = replace(self.trace.samples[0], joint_targets=(.2,))
        self.assert_rejected("INITIALIZATION_SOURCE", trace=replace(self.trace, samples=(raw, *self.trace.samples[1:])))

    def test_admin_keyup_and_early_text_do_not_pass(self):
        events = (*self.episode.events[:-1], replace(self.episode.events[-1], reason="maximum_hold"))
        self.assert_rejected("ADMINISTRATIVE_RELEASE", episode=replace(self.episode, events=events))
        text = self.episode.text_observations[1]
        text = replace(text, stamp=replace(text.stamp, capture_time_s=0., arrival_time_s=0.))
        self.assert_rejected("TEXT_ORACLE_MISMATCH", episode=replace(self.episode,
            text_observations=(self.episode.text_observations[0], text, self.episode.text_observations[-1])))

    def test_safety_independent_of_derived_stop_flag(self):
        monitor = PhysicsSafetyMonitor(self.limits, maximum_force_n=12., support_origin_xyz_m=(0., 0., .625), support_tolerance_m=.03)
        sample = self.trace.samples[0]
        for change, code in (
            ({"joint_positions": (2.,)}, "JOINT_LIMIT"),
            ({"joint_velocities": (9.,)}, "JOINT_SPEED"),
            ({"pelvis_xyz_m": (0., 0., .7)}, "UNSAFE_SUPPORT"),
            ({"torso_quaternion_wxyz": (.7, .7, 0., 0.)}, "UNSAFE_POSTURE"),
            ({"key_joint_targets_m": (.001,)*len(sample.key_joint_targets_m)}, "COMMANDED_KEYCAP"),
            ({"key_force_vectors_n": (20.,)*len(sample.key_force_vectors_n)}, "EXCESS_FORCE"),
        ):
            with self.subTest(code=code):
                changed = replace(sample, **change)
                self.assertIn(code, monitor.check(changed))
                self.assert_rejected(code, trace=replace(self.trace, samples=(changed, *self.trace.samples[1:])))

    def test_relabeled_fixture_without_physics_bundle_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.episode.write(Path(tmp)/"episode.json")
            self.trace.write(Path(tmp)/"trace.json")
            result = verify_bundle(Path(tmp))
            self.assertFalse(result["physical_primitive_passed"])
            self.assertEqual(result["findings"][0]["code"], "INVALID_BUNDLE")

    def test_worker_zero_exit_does_not_hide_missing_or_failed_collection(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            for process in ({"returncode": 0, "timed_out": False}, {"returncode": 0, "timed_out": True},
                            {"returncode": 1, "timed_out": False}):
                result = verify_run(path, config=SingleHandConfig(), process=process)
                self.assertFalse(result["accepted"])
            (path/"failure.json").write_text('{"error": "initialization failed"}')
            result = verify_run(path, config=SingleHandConfig(), process={"returncode": 0, "timed_out": False})
            self.assertFalse(result["accepted"])
            self.assertIn("failure.json", result["errors"][0])

    def test_modified_bundle_file_fails_its_manifest_before_source_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            for name in RECORDING_FILES:
                (path/name).write_text("original")
            digest = hashlib.sha256(b"original").hexdigest()
            (path/"manifest.json").write_text(json.dumps({"schema_version": "vf-physics-bundle/1",
                "files": {name: digest for name in RECORDING_FILES}}))
            (path/"task.json").write_text("changed")
            result = verify_bundle(path)
            self.assertFalse(result["physical_primitive_passed"])
            self.assertIn("ARTIFACT_HASH: task.json", result["findings"][0]["detail"])


if __name__ == "__main__":
    unittest.main()
