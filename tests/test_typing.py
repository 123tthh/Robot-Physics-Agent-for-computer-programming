from __future__ import annotations

import math
import unittest

from vskill_os.typing import (
    Finger,
    HandSide,
    KeyboardCalibration,
    TypingPlanner,
    verify_typed_text,
)


class TypingTests(unittest.TestCase):
    def test_text_plan_handles_shift_space_and_both_hands(self) -> None:
        plan = TypingPlanner().plan_text("Hi!")
        self.assertEqual(len(plan.strokes), 3)
        self.assertEqual(plan.strokes[0].modifiers, ("left_shift",))
        self.assertEqual(plan.strokes[1].hand, HandSide.RIGHT)
        self.assertEqual(plan.strokes[2].key, "1")
        self.assertEqual(plan.strokes[2].finger, Finger.MIDDLE)

    def test_chord_is_a_single_primary_key_objective(self) -> None:
        strokes = TypingPlanner().plan_chord(["ctrl", "alt", "t"])
        self.assertEqual(len(strokes), 1)
        self.assertEqual(strokes[0].key, "t")
        self.assertEqual(strokes[0].modifiers, ("left_ctrl", "left_alt"))

    def test_repair_backspaces_to_longest_correct_prefix(self) -> None:
        repair = TypingPlanner().repair(expected="codex", observed="codwx")
        self.assertEqual(repair.common_prefix_length, 3)
        self.assertEqual(repair.backspaces, 2)
        self.assertEqual(repair.replacement.text, "ex")

    def test_keyboard_calibration_rotates_key_positions(self) -> None:
        calibration = KeyboardCalibration((1.0, 2.0, 0.7), yaw_rad=math.pi / 2)
        q_pose = calibration.key_pose("q")
        self.assertGreater(q_pose.x_m, 1.0)
        self.assertGreater(q_pose.y_m, 2.0)

    def test_typing_requires_key_event_and_screen_agreement(self) -> None:
        self.assertTrue(
            verify_typed_text(
                expected="codex", key_events="codex", screen_text="$ codex"
            )
        )
        self.assertFalse(
            verify_typed_text(
                expected="codex", key_events="codwx", screen_text="$ codex"
            )
        )


if __name__ == "__main__":
    unittest.main()
