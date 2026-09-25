from __future__ import annotations

import importlib.util
import json
import shutil
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from vskill_os.adapters.pygame_desktop import PixelDesktop, SafeTextEditor
from vskill_os.control.standing_locomotion import KinematicWalkingAdapter, SE2Goal, StandingNavigationSkill
from vskill_os.simulation.keyboard_bridge import KeyEvent, KeyEventType
from vskill_os.simulation.screen_feedback import TesseractScreenObserver
from vskill_os.standing_typing import StandingTypingPlanner
from vskill_os.workstation_mission import (
    ForwardingTranscriptSink, SyntheticTypingExecutor, WorkstationMission,
)

HAS_PYGAME = importlib.util.find_spec("pygame") is not None
HAS_PIXELS = HAS_PYGAME or importlib.util.find_spec("PIL") is not None
HAS_OCR = shutil.which("tesseract") is not None


def navigation_success():
    return StandingNavigationSkill(KinematicWalkingAdapter(seed=7)).run(SE2Goal(0.05, 0, 0))


class MissionContractTests(unittest.TestCase):
    def test_each_rejected_navigation_postcondition_prevents_observation_and_input(self):
        accepted = navigation_success()
        self.assertTrue(accepted.succeeded)
        for flag in ("succeeded", "arrived", "stabilized", "stop_verified"):
            with self.subTest(flag=flag), tempfile.TemporaryDirectory() as directory:
                editor = SafeTextEditor("helo world")
                desktop = SimpleNamespace(editor=editor, capture=Mock(side_effect=AssertionError("capture before navigation gate")))
                executor = SimpleNamespace(evidence_mode="synthetic_events", run=Mock())
                navigation = SimpleNamespace(run=lambda goal: replace(accepted, **{flag: False}))
                report = WorkstationMission(navigation, desktop, Mock(), executor,
                                            output=Path(directory)).run(SE2Goal(0.05, 0), "hello world")
                self.assertFalse(report["succeeded"])
                self.assertEqual(report["failure_code"], "NAVIGATION_REJECTED")
                executor.run.assert_not_called()
                desktop.capture.assert_not_called()
                self.assertEqual(editor.consumed_event_count, 0)

    def test_physical_typing_executor_rejects_successful_synthetic_navigation(self):
        accepted = navigation_success()
        self.assertFalse(accepted.physical_walking_verified)
        editor = SafeTextEditor("helo world")
        desktop = SimpleNamespace(editor=editor, capture=Mock())
        executor = SimpleNamespace(evidence_mode="physical", run=Mock())
        with tempfile.TemporaryDirectory() as directory:
            mission = WorkstationMission(SimpleNamespace(run=lambda goal: accepted), desktop,
                        Mock(), executor, output=Path(directory))
            report = mission.run(SE2Goal(0.05, 0), "hello world")
        self.assertEqual(report["failure_code"], "PHYSICAL_NAVIGATION_REQUIRED")
        executor.run.assert_not_called()
        desktop.capture.assert_not_called()
        self.assertEqual(editor.consumed_event_count, 0)

    def test_forwarding_sink_keeps_empty_skill_transcript_separate_from_existing_document(self):
        editor = SafeTextEditor("helo world")
        sink = ForwardingTranscriptSink(editor)
        self.assertEqual(sink.snapshot(), "")
        plan = StandingTypingPlanner().plan_text("\b" * 7 + "lo world")
        for item in plan.expected_events:
            sink.consume(KeyEvent(KeyEventType(item.event_type), item.key, time.monotonic(), "test_sensor_fixture"))
        self.assertEqual(sink.snapshot(), "lo world")
        self.assertEqual(editor.render_content()[0], "hello world")
        self.assertEqual(editor.consumed_event_count, len(plan.expected_events))
        self.assertFalse(editor.keys_down)

    def test_synthetic_shift_chords_reserve_opposite_hands_and_release_in_order(self):
        editor = SafeTextEditor()
        plan = StandingTypingPlanner().plan_text("Aa!")
        result = SyntheticTypingExecutor().run(plan, editor)
        self.assertTrue(result.succeeded)
        self.assertFalse(result.physical_verified)
        self.assertEqual(editor.render_content()[0], "Aa!")
        for step in plan.steps:
            if not step.modifier:
                continue
            self.assertNotEqual(step.primary.hand, step.modifier.hand)
            events = [event for event in result.details["events"] if event["step_index"] == step.index]
            self.assertEqual([(event["event_type"], event["key"], event["hand"]) for event in events], [
                ("keydown", step.modifier.key, step.modifier.hand),
                ("keydown", step.primary.key, step.primary.hand),
                ("keyup", step.primary.key, step.primary.hand),
                ("keyup", step.modifier.key, step.modifier.hand),
            ])
        self.assertFalse(editor.keys_down)


@unittest.skipUnless(HAS_PIXELS and HAS_OCR, "pixel mission tests require pygame/Pillow and tesseract")
class MissionPixelTests(unittest.TestCase):
    def setUp(self):
        self.editor = SafeTextEditor("helo world")
        self.desktop = PixelDesktop(self.editor, backend="pygame" if HAS_PYGAME else "pillow")
        self.observer = TesseractScreenObserver()
        self.directory = tempfile.TemporaryDirectory()
        self.output = Path(self.directory.name)

    def tearDown(self):
        self.desktop.close()
        self.directory.cleanup()

    def mission(self, *, observer=None, executor=None):
        return WorkstationMission(StandingNavigationSkill(KinematicWalkingAdapter(seed=7)),
                    self.desktop, observer or self.observer, executor or SyntheticTypingExecutor(), output=self.output)

    def test_full_actual_pixel_roundtrip_reports_synthetic_execution_honestly(self):
        report = self.mission().run(SE2Goal(0.15, 0.05, 0.1), "hello world")
        self.assertTrue(report["succeeded"], report)
        self.assertEqual(report["edit"]["backspaces"], 7)
        self.assertTrue(report["screen_verification"]["passed"])
        self.assertEqual(report["screen_verification"]["observed_text"], "hello world")
        self.assertEqual(report["screen_verification"]["evidence_kind"], "pixel_ocr")
        self.assertFalse(report["physical_end_to_end_verified"])
        self.assertFalse(report["robot_camera_verified"])
        self.assertFalse(report["typing_execution"]["physical_verified"])
        self.assertFalse(report["keys_down_at_end"])
        persisted = json.loads((self.output / "report.json").read_text())
        self.assertTrue(persisted["succeeded"])
        self.assertTrue((self.output / "after/frame.json").is_file())

    def test_screen_change_between_observation_and_edit_rejects_stale_plan_without_input(self):
        original_observer = self.observer
        editor = self.editor

        class ChangingFocusObserver:
            calls = 0

            def observe(self, frame):
                observation = original_observer.observe(frame)
                self.calls += 1
                if self.calls == 1:
                    editor.focused = False
                return observation

        executor = SimpleNamespace(evidence_mode="synthetic_events", run=Mock())
        report = self.mission(observer=ChangingFocusObserver(), executor=executor).run(SE2Goal(0.05, 0), "hello world")
        self.assertEqual(report["failure_code"], "STALE_EDIT_PLAN")
        self.assertFalse(report["succeeded"])
        executor.run.assert_not_called()
        self.assertEqual(self.editor.consumed_event_count, 0)

    def test_executor_completion_with_wrong_event_watermark_cannot_pass(self):
        class WrongWatermarkExecutor(SyntheticTypingExecutor):
            def run(self, plan, editor):
                result = super().run(plan, editor)
                return replace(result, event_count=result.event_count + 1)

        report = self.mission(executor=WrongWatermarkExecutor()).run(SE2Goal(0.05, 0), "hello world")
        self.assertFalse(report["succeeded"])
        self.assertEqual(report["failure_code"], "MISSION_EXCEPTION")
        self.assertIn("event delivery watermark", report["error"])
        self.assertNotIn("screen_verification", report)

    def test_observer_returning_cached_old_frame_cannot_authorize_edit(self):
        original_observer = self.observer

        class CachedObserver:
            cached = None

            def observe(self, frame):
                if self.cached is None:
                    self.cached = original_observer.observe(frame)
                return self.cached

        executor = SimpleNamespace(evidence_mode="synthetic_events", run=Mock())
        report = self.mission(observer=CachedObserver(), executor=executor).run(SE2Goal(0.05, 0), "hello world")
        self.assertEqual(report["failure_code"], "STALE_EDIT_PLAN")
        self.assertIn("latest captured frame", report["error"])
        executor.run.assert_not_called()
        self.assertEqual(self.editor.consumed_event_count, 0)


if __name__ == "__main__":
    unittest.main()
