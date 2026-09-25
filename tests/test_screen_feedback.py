from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from vskill_os.adapters.pygame_desktop import PixelDesktop, SafeTextEditor, replay_text_events, run_demo
from vskill_os.simulation.keyboard_bridge import KeyEvent, KeyEventType
from vskill_os.simulation.screen_feedback import (
    ScreenFeedbackVerifier, TesseractScreenObserver, assert_plan_current,
    decode_png_rgb, encode_png_rgb, plan_text_edit, publish_frame,
)

HAS_PYGAME = importlib.util.find_spec("pygame") is not None
HAS_PIXELS = HAS_PYGAME or importlib.util.find_spec("PIL") is not None
HAS_OCR = shutil.which("tesseract") is not None


class SafeEditorTests(unittest.TestCase):
    def test_append_backspace_shift_and_no_command_execution(self):
        editor = SafeTextEditor("hello")
        replay_text_events(editor, "\b\bHi!")
        self.assertEqual(editor.render_content()[0], "helHi!")
        self.assertFalse(editor.keys_down)
        for key in ("enter", "left_ctrl", "left_alt", "tab"):
            with self.assertRaises(ValueError):
                editor.handle_event(KeyEvent(KeyEventType.KEYDOWN, key, time.monotonic(), "test"))

    def test_duplicate_out_of_order_and_wrong_focus_input(self):
        editor = SafeTextEditor("robot")
        editor.focused = False
        replay_text_events(editor, "x")
        self.assertEqual(editor.render_content()[0], "robot")
        event = KeyEvent(KeyEventType.KEYDOWN, "a", time.monotonic(), "test", event_id="one")
        editor.handle_event(event)
        with self.assertRaises(ValueError):
            editor.handle_event(event)
        with self.assertRaises(ValueError):
            editor.handle_event(KeyEvent(KeyEventType.KEYUP, "a", event.monotonic_time - 1, "test"))

    def test_editor_capacity_is_bounded(self):
        editor = SafeTextEditor("a" * 48)
        with self.assertRaises(ValueError):
            replay_text_events(editor, "x")
        self.assertEqual(editor.render_content()[0], "a" * 48)
        self.assertFalse(editor.keys_down)

    def test_png_roundtrip_and_corruption_rejection(self):
        rgb = b"\xff\0\0\0\xff\0\0\0\xff\xff\xff\xff"
        png = encode_png_rgb(2, 2, rgb)
        self.assertEqual(decode_png_rgb(png), (2, 2, rgb))
        damaged = bytearray(png)
        damaged[-5] ^= 1
        for candidate in (bytes(damaged), png[:-1], png + b"extra"):
            with self.assertRaises(ValueError):
                decode_png_rgb(candidate)


@unittest.skipUnless(HAS_PIXELS and HAS_OCR, "actual rendered pixel OCR requires pygame/Pillow and local tesseract")
class PixelFeedbackTests(unittest.TestCase):
    def setUp(self):
        self.editor = SafeTextEditor("helo world")
        self.desktop = PixelDesktop(self.editor, backend="pygame" if HAS_PYGAME else "pillow")
        self.observer = TesseractScreenObserver()

    def tearDown(self):
        self.desktop.close()

    def test_actual_pixels_to_edit_to_independent_ocr(self):
        before_frame = self.desktop.capture()
        # The perception adapter receives only bytes: privileged state access
        # can fail completely after rendering without affecting observation.
        with patch.object(self.editor, "render_content", side_effect=AssertionError("oracle read")):
            before = self.observer.observe(before_frame)
        self.assertEqual(before.text, "helo world")
        plan = plan_text_edit(before, "hello world")
        self.assertEqual(plan.common_prefix_length, 3)
        self.assertEqual(plan.backspaces, 7)
        self.assertEqual(plan.typing_text, "\b" * 7 + "lo world")
        assert_plan_current(plan, self.observer.observe(self.desktop.capture()))
        replay_text_events(self.editor, plan.typing_text)
        after_frame = self.desktop.capture()
        after = self.observer.observe(after_frame)
        result = ScreenFeedbackVerifier().verify(expected_text="hello world", before=before, after=after,
                    frame=after_frame, last_event_at_s=self.editor.last_event_at_s)
        self.assertTrue(result.passed, result)
        self.assertFalse(result.physical_acceptance)
        self.assertGreater(after.consumed_event_count, before.consumed_event_count)
        self.assertNotEqual(before.pixel_sha256, after.pixel_sha256)

    def test_stale_frame_wrong_text_and_hash_rejected(self):
        before_frame = self.desktop.capture()
        before = self.observer.observe(before_frame)
        verifier = ScreenFeedbackVerifier()
        stale = verifier.verify(expected_text="helo world", before=before, after=before, frame=before_frame)
        self.assertEqual(stale.failure_code, "STALE_SCREEN_FRAME")
        replay_text_events(self.editor, "x")
        frame = self.desktop.capture()
        after = self.observer.observe(frame)
        wrong = verifier.verify(expected_text="hello world", before=before, after=after, frame=frame)
        self.assertEqual(wrong.failure_code, "SCREEN_TEXT_MISMATCH")
        mismatch = verifier.verify(expected_text=after.text, before=before,
                                   after=replace(after, pixel_sha256="0" * 64), frame=frame)
        self.assertEqual(mismatch.failure_code, "FRAME_EVIDENCE_MISMATCH")
        expired = verifier.verify(expected_text=after.text, before=before, after=after, frame=frame,
                                  now_s=frame.captured_at_s + 20)
        self.assertEqual(expired.failure_code, "STALE_SCREEN_FRAME")

    def test_forged_text_with_correct_frame_hash_fails_independent_ocr(self):
        before = self.observer.observe(self.desktop.capture())
        replay_text_events(self.editor, "\bd")  # Actual text remains misspelled.
        frame = self.desktop.capture()
        real = self.observer.observe(frame)
        forged = replace(real, text="hello world")
        result = ScreenFeedbackVerifier().verify(expected_text="hello world", before=before,
                                                after=forged, frame=frame)
        self.assertEqual(result.failure_code, "OCR_EVIDENCE_MISMATCH")

    def test_wrong_focus_and_intervening_screen_change_fail(self):
        before = self.observer.observe(self.desktop.capture())
        plan = plan_text_edit(before, "hello world")
        self.editor.focused = False
        frame = self.desktop.capture()
        after = self.observer.observe(frame)
        self.assertFalse(after.focused)
        with self.assertRaisesRegex(ValueError, "SCREEN_CHANGED_BEFORE_EDIT"):
            assert_plan_current(plan, after)
        with self.assertRaises(ValueError):
            plan_text_edit(after, "hello world")
        result = ScreenFeedbackVerifier().verify(expected_text="helo world", before=before,
                                                after=after, frame=frame)
        self.assertEqual(result.failure_code, "WRONG_EDITOR_FOCUS_OR_MODE")

    def test_oracle_and_frame_before_input_are_rejected(self):
        initial = self.desktop.capture()
        oracle = self.editor.observe_text_oracle(initial)
        with self.assertRaises(ValueError):
            plan_text_edit(oracle, "hello world")
        before = self.observer.observe(initial)
        frame = self.desktop.capture()
        after = self.observer.observe(frame)
        result = ScreenFeedbackVerifier().verify(expected_text="helo world", before=before,
                    after=after, frame=frame, last_event_at_s=frame.captured_at_s + 1)
        self.assertEqual(result.failure_code, "FRAME_PRECEDES_INPUT")

    def test_new_frame_without_input_does_not_prove_edit_but_allows_noop(self):
        before = self.observer.observe(self.desktop.capture())
        frame = self.desktop.capture()
        after = self.observer.observe(frame)
        missing = ScreenFeedbackVerifier().verify(expected_text="hello world", before=before,
                                                  after=replace(after, text="hello world"), frame=frame)
        self.assertEqual(missing.failure_code, "MISSING_INPUT_EVIDENCE")
        noop = ScreenFeedbackVerifier().verify(expected_text="helo world", before=before,
                                               after=after, frame=frame)
        self.assertTrue(noop.passed)

    def test_png_manifest_matches_actual_pixel_digest_and_has_no_text(self):
        frame = self.desktop.capture()
        with tempfile.TemporaryDirectory() as directory:
            path = publish_frame(frame, directory)
            manifest = json.loads(path.read_text())
            encoded = (path.parent / manifest["file"]).read_bytes()
            width, height, decoded = decode_png_rgb(encoded)
            self.assertEqual((width, height), tuple(manifest["pixel_size"]))
            self.assertEqual(decoded, frame.rgb)
            self.assertEqual(hashlib.sha256(encoded).hexdigest(), manifest["sha256"])
            self.assertEqual(hashlib.sha256(decoded).hexdigest(), manifest["pixel_sha256"])
            self.assertNotIn("helo", path.read_text())
            self.assertEqual(manifest["consumed_event_count"], 0)

    @unittest.skipUnless(HAS_PYGAME, "pygame optional dependency unavailable")
    def test_pygame_cpu_cli_artifact_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            report = run_demo(output=Path(directory), backend="pygame")
            self.assertTrue(report["verification"]["passed"])
            self.assertEqual(report["backend"], "pygame")
            self.assertTrue((Path(directory) / "before/frame.json").is_file())
            self.assertTrue((Path(directory) / "after/frame.json").is_file())


if __name__ == "__main__":
    unittest.main()
