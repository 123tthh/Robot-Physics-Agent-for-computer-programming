from __future__ import annotations

from dataclasses import replace
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from vskill_os.simulation.dex5_typing import (
    DEX5_MONITORED_KEYS,
    DEFAULT_DEMO_TEXT,
    Dex5Failure,
    Dex5Finger,
    Dex5Phase,
    Dex5SafetyGuard,
    Dex5TypingPlanner,
    Dex5TypingVerifier,
    build_replay_observations,
    run_replay_demo,
)
from vskill_os.typing import HandSide


class Dex5PlanningTests(unittest.TestCase):
    def test_default_exercises_all_fingers_on_both_hands(self) -> None:
        plan = Dex5TypingPlanner().plan()
        self.assertEqual(plan.text, DEFAULT_DEMO_TEXT)
        self.assertEqual(set(plan.executors), {
            f"{hand.value}.{finger.value}" for hand in HandSide for finger in Dex5Finger
        })
        self.assertEqual([stroke.executor for stroke in plan.strokes if stroke.key == "space"], [
            "left.thumb", "right.thumb",
        ])
        self.assertEqual(len(plan.stages), 3 * len(plan.strokes))
        for stroke in plan.strokes:
            self.assertEqual(
                [stage.phase for stage in plan.stages if stage.stroke_index == stroke.index],
                [Dex5Phase.HOVER, Dex5Phase.PRESS, Dex5Phase.RELEASE],
            )

    def test_plan_is_deterministic_versioned_bounded_and_has_no_joint_commands(self) -> None:
        first = Dex5TypingPlanner().plan()
        self.assertEqual(first, Dex5TypingPlanner().plan())
        self.assertTrue(math.isfinite(first.maximum_duration_s))
        serialized = json.dumps(first.to_dict())
        self.assertNotIn("joint", serialized)
        for stage in first.stages:
            self.assertEqual(stage.version, "1.0.0")
            self.assertTrue(stage.preconditions)
            self.assertTrue(stage.postconditions)
            self.assertTrue(stage.resources)
            self.assertTrue(stage.failure_codes)
            self.assertGreater(stage.maximum_duration_s, 0)

    def test_unsupported_or_unbounded_plans_fail_before_motion(self) -> None:
        planner = Dex5TypingPlanner()
        for text in ("", "a" * 257, "HELLO", "中文", "!"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                planner.plan(text)
        for timeout in (0, -1, math.inf, math.nan):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                planner.plan(phase_timeout_s=timeout)

    def test_repair_uses_longest_prefix_and_is_independently_verified(self) -> None:
        planner = Dex5TypingPlanner()
        repair = planner.plan_repair(expected="codex", observed="codwx")
        self.assertEqual(repair.text, "\b\bex")
        self.assertEqual(repair.initial_text, "codwx")
        result = Dex5TypingVerifier().verify(repair, build_replay_observations(repair))
        self.assertTrue(result.succeeded)
        self.assertEqual(result.observed_text, "codex")
        with self.assertRaisesRegex(ValueError, "horizon"):
            planner.plan_repair(expected="a", observed="b" * 32)

    def test_direct_plan_edits_cannot_drop_verification_stages_or_support_requirements(self) -> None:
        plan = Dex5TypingPlanner().plan()
        for change in (
            {"stages": ()}, {"strokes": ()}, {"support_mode": "trust_controller"},
            {"maximum_setup_duration_s": math.inf},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                replace(plan, **change)
        bad_stroke = replace(plan.strokes[0], expected_prefix="controller says done")
        with self.assertRaisesRegex(ValueError, "display prefix"):
            replace(plan, strokes=(bad_stroke, *plan.strokes[1:]))


class Dex5VerificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plan = Dex5TypingPlanner().plan()
        self.observations = build_replay_observations(self.plan)
        self.verifier = Dex5TypingVerifier()

    def test_frame_boundary_timeout_tolerates_float_roundoff_only(self) -> None:
        plan = Dex5TypingPlanner().plan("a", phase_timeout_s=12.)
        observations = build_replay_observations(plan)
        times = (968/240, 3848/240, 3872/240, 3896/240)
        boundary = tuple(replace(o,time_s=t) for o,t in zip(observations,times,strict=True))
        self.assertTrue(self.verifier.verify(plan,boundary).succeeded)
        late = (boundary[0], *(replace(o,time_s=o.time_s+1/240) for o in boundary[1:]))
        self.assertEqual(self.verifier.verify(plan,late).failure_code,Dex5Failure.TIMEOUT)

    def test_replay_proves_contract_only_and_is_reproducible(self) -> None:
        first = run_replay_demo()
        second = run_replay_demo()
        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertTrue(first.report.succeeded)
        self.assertEqual(first.report.capability, "replay")
        self.assertFalse(first.report.dynamic_sitting_verified)
        self.assertFalse(first.report.camera_ocr_verified)
        self.assertFalse(first.report.real_robot_verified)
        self.assertEqual(first.report.keys_down_after_stop, ())
        self.assertEqual(first.report.completed_strokes, 10)
        self.assertEqual(len(first.report.executors_observed), 10)

    def test_replay_cannot_pass_as_isaac_evidence(self) -> None:
        report = self.verifier.verify(self.plan, self.observations, capability="isaac_sim_5_1")
        self.assertEqual(report.failure_code, Dex5Failure.UNSUPPORTED_SOURCE)

    def test_faults_are_categorized_and_do_not_claim_recovery(self) -> None:
        expected = {
            "no_contact": Dex5Failure.NO_CONTACT,
            "wrong_finger": Dex5Failure.WRONG_FINGER,
            "wrong_key": Dex5Failure.WRONG_KEY,
            "stuck_key": Dex5Failure.STUCK_KEY,
            "over_force": Dex5Failure.EXCESS_FORCE,
            "screen_mismatch": Dex5Failure.SCREEN_MISMATCH,
            "controller_claim": Dex5Failure.UNSUPPORTED_SOURCE,
            "timeout": Dex5Failure.TIMEOUT,
            "unsafe_posture": Dex5Failure.UNSAFE_POSTURE,
        }
        for fault, code in expected.items():
            with self.subTest(fault=fault):
                result = run_replay_demo(inject_fault=fault)
                self.assertFalse(result.report.succeeded)
                self.assertEqual(result.report.failure_code, code)
                self.assertIn("release_all_fingers", result.report.recovery_actions)

    def test_expected_text_and_controller_claim_cannot_substitute_for_contact(self) -> None:
        trace = list(self.observations)
        trace[2] = replace(trace[2], contacts=())
        report = self.verifier.verify(self.plan, trace)
        self.assertEqual(report.failure_code, Dex5Failure.NO_CONTACT)
        self.assertEqual(report.observed_text, DEFAULT_DEMO_TEXT)
        self.assertFalse(report.succeeded)

    def test_contact_travel_and_event_must_coincide(self) -> None:
        trace = list(self.observations)
        valid_press = trace[2]
        trace[2] = replace(valid_press, events=())
        trace.insert(3, replace(
            valid_press, time_s=valid_press.time_s + 0.01,
            key_travel_m=tuple((key, 0.0) for key, _ in valid_press.key_travel_m),
        ))
        report = self.verifier.verify(self.plan, trace)
        self.assertEqual(report.failure_code, Dex5Failure.EVENT_MISMATCH)

    def test_duplicate_events_cannot_count_as_success(self) -> None:
        trace = list(self.observations)
        trace[2] = replace(trace[2], events=trace[2].events * 2)
        self.assertEqual(
            self.verifier.verify(self.plan, trace).failure_code,
            Dex5Failure.EVENT_MISMATCH,
        )

    def test_all_74_keys_must_be_observed_even_when_only_one_key_is_planned(self) -> None:
        self.assertEqual(len(DEX5_MONITORED_KEYS), 74)
        plan = Dex5TypingPlanner().plan("a")
        snapshots = build_replay_observations(plan)
        for omitted in ("s", "f12", "menu"):
            with self.subTest(omitted=omitted):
                incomplete = tuple(replace(sample, key_travel_m=tuple(
                    (key, value) for key, value in sample.key_travel_m if key != omitted
                )) for sample in snapshots)
                self.assertEqual(
                    self.verifier.verify(plan, incomplete).failure_code,
                    Dex5Failure.INVALID_EVIDENCE,
                )

    def test_later_physical_release_does_not_validate_an_early_keyup(self) -> None:
        trace = list(self.observations)
        physical_release = trace[3]
        early_release = replace(
            trace[2], phase=Dex5Phase.RELEASE,
            time_s=(trace[2].time_s + physical_release.time_s) / 2,
            events=(("keyup", self.plan.strokes[0].key),), keys_down=(),
        )
        trace[3] = replace(physical_release, events=())
        trace.insert(3, early_release)
        report = self.verifier.verify(self.plan, trace)
        self.assertEqual(report.failure_code, Dex5Failure.EVENT_MISMATCH)

    def test_missing_out_of_order_and_nan_measurements_fail(self) -> None:
        empty = self.verifier.verify(self.plan, ())
        self.assertEqual(empty.failure_code, Dex5Failure.MISSING_OBSERVATION)
        self.assertEqual(
            self.verifier.verify(self.plan, self.observations[1:]).failure_code,
            Dex5Failure.PHASE_ORDER,
        )
        for invalid in (
            replace(self.observations[2], time_s=0.0),
            replace(self.observations[2], pelvis_height_m=math.nan),
            replace(self.observations[2], key_travel_m=()),
            replace(self.observations[2], fingertip_clearance_m=()),
        ):
            with self.subTest(invalid=invalid):
                trace = list(self.observations)
                trace[2] = invalid
                self.assertEqual(
                    self.verifier.verify(self.plan, trace).failure_code,
                    Dex5Failure.INVALID_EVIDENCE,
                )

    def test_missing_release_and_assisted_fixture_loss_fail(self) -> None:
        trace = list(self.observations)
        trace[-1] = replace(trace[-1], fingertip_clearance_m=tuple(
            (executor, 0.0 if executor == "right.thumb" else value)
            for executor, value in trace[-1].fingertip_clearance_m
        ))
        self.assertEqual(
            self.verifier.verify(self.plan, trace).failure_code,
            Dex5Failure.FINGER_NOT_RELEASED,
        )
        trace = list(self.observations)
        trace[1] = replace(trace[1], base_constraint_active=False)
        self.assertEqual(
            self.verifier.verify(self.plan, trace).failure_code,
            Dex5Failure.SEATED_PRECONDITION,
        )

    def test_seat_contact_mode_requires_actual_support_and_no_fixture(self) -> None:
        plan = Dex5TypingPlanner().plan("as jk", support_mode="seat_contact")
        trace = list(build_replay_observations(plan))
        self.assertTrue(self.verifier.verify(plan, trace).succeeded)
        self.assertFalse(self.verifier.verify(plan, trace).dynamic_sitting_verified)
        trace[0] = replace(trace[0], seat_support_force_n=0.0)
        self.assertEqual(
            self.verifier.verify(plan, trace).failure_code,
            Dex5Failure.SEATED_PRECONDITION,
        )

    def test_safety_guard_operates_without_planner_or_verifier(self) -> None:
        sample = replace(self.observations[2], torso_tilt_rad=1.0)
        self.assertEqual(Dex5SafetyGuard().check(sample), Dex5Failure.UNSAFE_POSTURE)

    def test_cli_writes_inspectable_trace_and_failure_exits_nonzero(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "replay.json"
            command = [sys.executable, "-m", "vskill_os.simulation.dex5_typing", "--output", str(output)]
            good = subprocess.run(command, capture_output=True, text=True, check=False)
            self.assertEqual(good.returncode, 0, good.stderr)
            data = json.loads(output.read_text())
            self.assertTrue(data["report"]["succeeded"])
            self.assertEqual(data["observations"][2]["source"], "replay_sensor")
            bad = subprocess.run(command + ["--inject-fault", "no_contact"], capture_output=True, text=True, check=False)
            self.assertEqual(bad.returncode, 1, bad.stderr)
            self.assertFalse(json.loads(output.read_text())["report"]["succeeded"])


if __name__ == "__main__":
    unittest.main()
