from __future__ import annotations

from dataclasses import replace
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from vskill_os.learning.contracts import EpisodeRecord, KeyBinding, TaskSpec, VerificationRequest
from vskill_os.learning.evaluation import EvaluationPolicy, IndependentTypingEvaluator
from vskill_os.learning.replay import CASE_EXPECTATIONS, build_replay_case


class EvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.case = build_replay_case("correct_press")
        self.task, self.episode = self.case.task, self.case.episode
        self.evaluator = IndependentTypingEvaluator()

    def assert_failure(self, episode: EpisodeRecord, code: str, task: TaskSpec | None = None):
        result = self.evaluator.evaluate(task or self.task, episode)
        self.assertFalse(result.contract_passed, result.to_json())
        self.assertIn(code, {finding.code for finding in result.findings}, result.to_json())
        self.assertFalse(result.physical_execution_verified)
        return result

    def test_bridge_generated_positive_and_negative_records(self) -> None:
        for name, (expected, codes) in CASE_EXPECTATIONS.items():
            with self.subTest(name=name):
                case = build_replay_case(name)
                result = self.evaluator.evaluate(case.task, case.episode)
                self.assertEqual(result.contract_passed, expected, result.to_json())
                self.assertTrue(set(codes) <= {finding.code for finding in result.findings})
                self.assertFalse(result.physical_execution_verified)
                self.assertEqual(case.episode, build_replay_case(name).episode)

    def test_success_counts_complete_cycles_and_final_verification_cost(self) -> None:
        result = self.evaluator.evaluate(self.task, self.episode)
        self.assertTrue(result.contract_passed)
        self.assertEqual((result.completed_press_release_cycles, result.keydown_count), (1, 1))
        self.assertEqual(result.verification_count, 1)
        self.assertAlmostEqual(result.verification_time_s, 0.02)
        self.assertEqual(result.observed_text, "a")
        self.assertEqual(result.capability, "contract_replay")

    def test_timeout_and_shutdown_cannot_be_relabelled_as_physical_release(self) -> None:
        case = build_replay_case("timeout_release")
        self.assertEqual(case.episode.events[-1].reason, "maximum_hold")
        for reason, expected in (("maximum_hold", "ADMINISTRATIVE_RELEASE"),
                                 ("worker_shutdown", "ADMINISTRATIVE_RELEASE"),
                                 ("release_threshold", "EVENT_MISMATCH")):
            with self.subTest(reason=reason):
                episode = replace(case.episode, events=(case.episode.events[0], replace(case.episode.events[1], reason=reason)))
                self.assert_failure(episode, expected)

    def test_keyup_does_not_prove_finger_clearance(self) -> None:
        frames = tuple(replace(frame, keys=tuple(
            replace(key, in_contact=True) if key.key == "a" else key for key in frame.keys
        )) if frame.stamp.capture_time_s >= 0.04 else frame for frame in self.episode.observations)
        self.assert_failure(replace(self.episode, observations=frames), "STUCK_KEY")

    def test_repeat_needs_sustained_release_between_presses(self) -> None:
        case = build_replay_case("repeated_character")
        self.assertEqual(self.evaluator.evaluate(case.task, case.episode).completed_press_release_cycles, 2)
        frames = tuple(replace(frame, keys=tuple(
            replace(key, in_contact=True) if key.key == "a" else key for key in frame.keys
        )) if 0.05 <= frame.stamp.capture_time_s <= 0.07 else frame for frame in case.episode.observations)
        self.assert_failure(replace(case.episode, observations=frames), "INCOMPLETE_RELEASE", case.task)

    def test_contact_and_travel_must_support_events_at_the_same_sample(self) -> None:
        for alteration in ({"in_contact": False}, {"travel_m": 0.0}):
            with self.subTest(alteration=alteration):
                frames = tuple(replace(frame, keys=tuple(
                    replace(key, **alteration) if key.key == "a" else key for key in frame.keys
                )) for frame in self.episode.observations)
                self.assert_failure(replace(self.episode, observations=frames), "EVENT_MISMATCH")
        up = self.episode.events[-1]
        early_up = replace(up, sample_id=self.episode.observations[3].stamp.record_id,
                           stamp=replace(up.stamp, capture_time_s=0.03, arrival_time_s=0.03))
        self.assert_failure(replace(self.episode, events=(self.episode.events[0], early_up)), "EVENT_MISMATCH")

    def test_full_keyboard_and_joint_coverage_is_mandatory(self) -> None:
        self.assertEqual(len(self.task.monitored_keys), 74)
        for omitted in ("s", "f12", "menu"):
            with self.subTest(omitted=omitted):
                frames = tuple(replace(frame, keys=tuple(key for key in frame.keys if key.key != omitted))
                               for frame in self.episode.observations)
                self.assert_failure(replace(self.episode, observations=frames), "INVALID_EVIDENCE")
        frames = (replace(self.episode.observations[0], joints=()), *self.episode.observations[1:])
        self.assert_failure(replace(self.episode, observations=frames), "INVALID_EVIDENCE")

    def test_unreported_adjacent_actuation_safety_and_force_fail(self) -> None:
        original = self.episode.observations[2]
        for key_change, frame_change, code in (
            ({"key": "f12", "travel_m": 0.003, "in_contact": True}, {}, "UNEXPECTED_KEY"),
            ({"normal_force_n": 50.0}, {}, "EXCESS_FORCE"),
            ({}, {"safety_stopped": True}, "SAFETY_STOP"),
        ):
            with self.subTest(code=code):
                selected = key_change.get("key", "a")
                keys = tuple(replace(key, **key_change) if key.key == selected else key for key in original.keys)
                changed = replace(original, keys=keys, **frame_change)
                frames = (*self.episode.observations[:2], changed, *self.episode.observations[3:])
                self.assert_failure(replace(self.episode, observations=frames), code)

    def test_record_sequence_gaps_duplicates_and_clock_contamination_fail_closed(self) -> None:
        for stream in ("observations", "events", "text_observations", "verification_requests", "screen_observations"):
            records = getattr(self.episode, stream)
            for alteration in ({"env_id": "env-other"}, {"episode_id": "old-episode"},
                               {"clock_domain": "wall_clock"}, {"sequence": 1}):
                with self.subTest(stream=stream, alteration=alteration):
                    bad = replace(records[0], stamp=replace(records[0].stamp, **alteration))
                    self.assert_failure(replace(self.episode, **{stream: (bad, *records[1:])}), "INVALID_EVIDENCE")
        duplicate = replace(self.episode.events[1], stamp=replace(
            self.episode.events[1].stamp, record_id=self.episode.events[0].stamp.record_id))
        self.assert_failure(replace(self.episode, events=(self.episode.events[0], duplicate)), "DUPLICATE_RECORD")

    def test_sparse_or_truncated_physical_record_cannot_establish_release(self) -> None:
        frames = tuple(replace(frame, stamp=replace(frame.stamp, sequence=index))
                       for index, frame in enumerate((self.episode.observations[0], self.episode.observations[-1])))
        self.assert_failure(replace(self.episode, observations=frames), "SAMPLE_GAP")
        self.assert_failure(replace(self.episode, observations=self.episode.observations[:-1]), "INVALID_EVIDENCE")
        self.assert_failure(replace(self.episode, observations=()), "INVALID_EVIDENCE")

    def test_separate_task_prevents_target_or_coverage_redefinition(self) -> None:
        for task in (replace(self.task, target_text="s"),
                     replace(self.task, monitored_keys=tuple(key for key in self.task.monitored_keys if key != "f12"))):
            self.assert_failure(self.episode, "TASK_MISMATCH", task)

    def test_whitespace_is_an_exact_character_not_a_normalization_hint(self) -> None:
        bindings = (*self.task.character_keys, KeyBinding("space", " "))
        space_task = replace(self.task, target_text=" ", character_keys=bindings)
        self.assertEqual(space_task.target_text, " ")
        task = replace(space_task, target_text="a ")
        self.assert_failure(replace(self.episode, task_sha256=task.sha256()), "TEXT_MISMATCH", task)

    def test_no_source_string_can_upgrade_replay_to_a_physics_result(self) -> None:
        episode = replace(self.episode, manifest=replace(self.episode.manifest, evidence_scope="isaac"))
        self.assert_failure(episode, "UNSUPPORTED_SOURCE")
        frame = self.episode.observations[0]
        frame = replace(frame, stamp=replace(frame.stamp, source="isaac_keycap_oracle"))
        self.assert_failure(replace(self.episode, observations=(frame, *self.episode.observations[1:])), "UNSUPPORTED_SOURCE")

    def test_text_truth_and_screen_evidence_are_both_required(self) -> None:
        self.assert_failure(replace(self.episode, text_observations=()), "TEXT_EVIDENCE_MISSING")
        bad = replace(self.episode.text_observations[1], caused_by_event_id="unrelated-event")
        self.assert_failure(replace(self.episode, text_observations=(self.episode.text_observations[0], bad,
                                                                  self.episode.text_observations[-1])), "TEXT_EVENT_MISMATCH")
        self.assert_failure(replace(self.episode, screen_observations=()), "SCREEN_PENDING")
        screen = replace(self.episode.screen_observations[0], text="s")
        self.assert_failure(replace(self.episode, screen_observations=(screen,)), "SCREEN_MISMATCH")

    def test_final_screen_must_be_requested_readable_fresh_and_received(self) -> None:
        screen = self.episode.screen_observations[0]
        variants = (
            (replace(screen, request_id="free-background-ocr"), "INVALID_SCREEN"),
            (replace(screen, missing=True, text=None, confidence=None, frame_sha256=None), "SCREEN_UNREADABLE"),
            (replace(screen, confidence=0.5), "SCREEN_UNREADABLE"),
            (replace(screen, stamp=replace(screen.stamp, capture_time_s=0.06)), "SCREEN_STALE"),
            (replace(screen, stamp=replace(screen.stamp, arrival_time_s=0.11)), "SCREEN_PENDING"),
        )
        for observation, code in variants:
            with self.subTest(code=code):
                self.assert_failure(replace(self.episode, screen_observations=(observation,)), code)
        self.assert_failure(replace(self.episode, verification_requests=(), screen_observations=()), "FINAL_VERIFICATION_MISSING")

    def test_old_capture_is_not_refreshed_by_recent_arrival(self) -> None:
        policy = replace(EvaluationPolicy(), maximum_screen_age_s=0.015)
        screen = replace(self.episode.screen_observations[0], stamp=replace(
            self.episode.screen_observations[0].stamp, arrival_time_s=0.1))
        result = IndependentTypingEvaluator(policy).evaluate(self.task, replace(self.episode, screen_observations=(screen,)))
        self.assertFalse(result.contract_passed)
        self.assertIn("SCREEN_STALE", {finding.code for finding in result.findings})

    def test_verification_before_last_input_cannot_end_a_repeated_string(self) -> None:
        case = build_replay_case("repeated_character")
        request = case.episode.verification_requests[0]
        request = replace(request, stamp=replace(request.stamp, capture_time_s=0.07, arrival_time_s=0.07))
        self.assert_failure(replace(case.episode, verification_requests=(request,)), "SCREEN_STALE", case.task)

    def test_deadline_verification_count_and_waiting_cost_are_enforced(self) -> None:
        for task, code in ((replace(self.task, time_budget_s=0.09), "TIME_LIMIT"),
                           (replace(self.task, verification_time_budget_s=0.01), "BUDGET_EXHAUSTED")):
            self.assert_failure(replace(self.episode, task_sha256=task.sha256()), code, task)
        original = self.episode.verification_requests[0]
        intermediate = VerificationRequest(replace(original.stamp, record_id="intermediate", capture_time_s=0.0,
                                                   arrival_time_s=0.0), "intermediate")
        final = replace(original, stamp=replace(original.stamp, sequence=1))
        task = replace(self.task, max_verifications=1)
        episode = replace(self.episode, task_sha256=task.sha256(), verification_requests=(intermediate, final))
        self.assert_failure(episode, "BUDGET_EXHAUSTED", task)
        delayed = build_replay_case("delayed_feedback")
        result = self.evaluator.evaluate(delayed.task, delayed.episode)
        self.assertAlmostEqual(result.verification_time_s, 0.03)
        waited = build_replay_case("delayed_feedback_waited")
        self.assertAlmostEqual(self.evaluator.evaluate(waited.task, waited.episode).verification_time_s, 0.12)

    def test_safety_timeout_or_cancel_cannot_be_reported_as_completion(self) -> None:
        for termination in ("safety_stop", "time_limit", "cancelled"):
            self.assert_failure(replace(self.episode, termination=termination), "INCOMPLETE_EPISODE")

    def test_recovery_does_not_hide_extra_presses_or_software_drops(self) -> None:
        case = build_replay_case("backspace_recovery")
        result = self.evaluator.evaluate(case.task, case.episode)
        self.assertTrue(result.contract_passed)
        self.assertEqual(result.completed_press_release_cycles, 3)
        self.assertEqual(result.keydown_count, 3)
        case = build_replay_case("dropped_then_retried")
        result = self.evaluator.evaluate(case.task, case.episode)
        self.assertTrue(result.contract_passed)
        self.assertEqual((result.dropped_deliveries, result.keydown_count), (1, 2))
        self.assertEqual((result.event_text, result.observed_text), ("aa", "a"))


class SerializationAndCliTests(unittest.TestCase):
    def test_json_round_trip_preserves_records_and_hashes(self) -> None:
        case = build_replay_case("correct_press")
        for record in (case.task, case.episode, EvaluationPolicy()):
            with self.subTest(record=type(record).__name__):
                loaded = type(record).from_json(record.to_json())
                self.assertEqual(loaded, record)
                self.assertEqual(loaded.sha256(), record.sha256())

    def test_unknown_schema_fields_duplicate_json_and_wrong_types_are_rejected(self) -> None:
        case = build_replay_case("correct_press")
        for mutation in (
            lambda data: data.update(schema_version="vf-evidence/999"),
            lambda data: data.update(controller_says_success=True),
            lambda data: data["observations"][0]["stamp"].update(sequence=True),
            lambda data: data["observations"][0]["keys"][0].update(in_contact="false"),
            lambda data: data["observations"][0]["keys"][0].update(travel_m=math.nan),
        ):
            data = json.loads(case.episode.to_json())
            mutation(data)
            with self.assertRaises(ValueError):
                EpisodeRecord.from_dict(data)
        for text in ('{"schema_version":"vf-evidence/1","schema_version":"vf-evidence/1"}', '{"x":NaN}', '[]'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                EpisodeRecord.from_json(text)

    def test_invalid_numeric_limits_and_missing_frame_mask_fail_at_construction(self) -> None:
        case = build_replay_case("correct_press")
        for change in ({"time_budget_s": math.inf}, {"max_verifications": False}, {"time_budget_s": 0.0}):
            with self.assertRaises(ValueError):
                replace(case.task, **change)
        with self.assertRaises(ValueError):
            replace(case.episode.screen_observations[0], missing=True)
        for change in ({"release_dwell_s": 0.0}, {"release_threshold_m": 0.003}, {"maximum_contact_force_n": math.nan}):
            with self.assertRaises(ValueError):
                replace(EvaluationPolicy(), **change)

    def test_command_line_export_independent_evaluate_and_exit_codes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            command = [sys.executable, "-m", "vskill_os.learning"]
            demo = subprocess.run([*command, "demo", "--output", directory], capture_output=True, text=True, timeout=30)
            self.assertEqual(demo.returncode, 0, demo.stderr)
            self.assertTrue(json.loads(demo.stdout)["all_expectations_met"])
            for name, expected_code in (("correct_press", 0), ("timeout_release", 1)):
                task, episode = root / name / "task.json", root / name / "episode.json"
                process = subprocess.run([*command, "evaluate", "--task", str(task), "--episode", str(episode),
                                          "--policy", str(root / "policy.json")], capture_output=True, text=True, timeout=30)
                self.assertEqual(process.returncode, expected_code, process.stderr)
                self.assertFalse(json.loads(process.stdout)["physical_execution_verified"])
            invalid = root / "invalid.json"
            invalid.write_text('{"schema_version":"unknown"}', encoding="utf-8")
            process = subprocess.run([*command, "evaluate", "--task", str(task), "--episode", str(invalid)],
                                     capture_output=True, text=True, timeout=30)
            self.assertEqual(process.returncode, 2)
            self.assertFalse(json.loads(process.stderr)["contract_passed"])
            self.assertNotIn("Traceback", process.stderr)


if __name__ == "__main__":
    unittest.main()
