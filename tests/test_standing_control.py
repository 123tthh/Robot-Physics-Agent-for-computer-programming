from __future__ import annotations

from dataclasses import dataclass
import unittest

from vskill_os.standing_control import DigitalTextBuffer, StandingTypingController
from vskill_os.standing_typing import HANDS, PARK_POSITIONS, StandingTypingPlanner, keyboard_positions


@dataclass(frozen=True)
class Frame:
    tick: int
    time_s: float
    tips: dict
    travel: dict
    forces: dict
    index_forces: dict
    source: str = "isaac_physx"


class FakeDriver:
    """Test-only kinematic switch fixture; it is not physical acceptance."""
    contact_offset_m = 0.013
    def __init__(self, fault=None):
        self.tick = 0
        self.tips = dict(PARK_POSITIONS)
        self.keys = keyboard_positions()
        self.fault = fault
        self.was_pressed = set()
        self.commands = []

    def step(self, targets):
        self.commands.append(dict(targets))
        self.tick += 1
        if self.fault != "frozen":
            self.tips.update(targets)
        return self.observe()

    def observe(self):
        travel = {key: 0.0 for key in self.keys}
        forces = dict(travel)
        pairs = {key: {hand: 0.0 for hand in HANDS} for key in self.keys}
        for hand, point in self.tips.items():
            for key, pos in self.keys.items():
                if abs(point[0] - pos[0]) < 0.003 and abs(point[1] - pos[1]) < 0.003:
                    depth = pos[2] + self.contact_offset_m - point[2]
                    if depth > 0:
                        travel[key] = min(depth, 0.004)
                        forces[key] = 1.5 if self.fault != "force" else 12.0
                        pairs[key][hand] = forces[key] if self.fault != "wrong_finger" else 0.0
                        if depth >= 0.002:
                            self.was_pressed.add(key)
        if self.fault == "off_target" and self.tick == 5:
            travel["z"] = 0.003
            forces["z"] = pairs["z"]["left"] = 1.5
        if self.fault == "stuck" and self.was_pressed:
            key = sorted(self.was_pressed)[0]
            travel[key], forces[key] = 0.003, 1.5
            pairs[key]["left"] = 1.5
        return Frame(self.tick, self.tick / 120, dict(self.tips), travel, forces, pairs)


class StandingControlTests(unittest.TestCase):
    def test_downward_tip_geometry_uses_bounded_calibrated_depth(self):
        driver=FakeDriver()
        driver.contact_offset_m=.005
        result=StandingTypingController(driver,tip_contact_offset_m=.005,
                    maximum_press_depth_m=.006).run(StandingTypingPlanner().plan_text('ak'))
        self.assertTrue(result.succeeded,result.to_dict())
        self.assertEqual(result.observed_text,'ak')
        self.assertTrue(all(p[2]>=.7602+.005-.006 for c in driver.commands for p in c.values()))

    def test_central_clock_parallel_prepose_ordered_commits_and_text(self):
        plan = StandingTypingPlanner().plan_text("afjk Hi!\b")
        driver = FakeDriver()
        result = StandingTypingController(driver).run(plan)
        self.assertTrue(result.succeeded, result.to_dict())
        self.assertEqual(result.observed_text, "afjk Hi")
        self.assertEqual(result.observed_text_source, "keyboard_event_buffer")
        self.assertTrue(result.cleanup.physical_clear)
        self.assertTrue(all(set(command) == set(HANDS) for command in driver.commands))
        self.assertIn("coordinated_prepose", {p["phase"] for p in result.phase_trace})
        self.assertEqual(len(result.events), len(plan.expected_events))
        self.assertFalse(result.to_dict()["text_evidence_is_ocr"])

    def test_unplanned_key_on_other_side_is_sampled_and_fails(self):
        result = StandingTypingController(FakeDriver("off_target")).run(StandingTypingPlanner().plan_text("jk"))
        self.assertEqual(result.failure_code, "UNEXPECTED_KEY_EVENT")
        self.assertTrue(any(e.key == "z" for e in result.events))
        self.assertTrue(result.cleanup.attempted)
        self.assertTrue(result.cleanup.physical_clear)

    def test_pair_contact_is_required_even_when_key_travels(self):
        result = StandingTypingController(FakeDriver("wrong_finger")).run(StandingTypingPlanner().plan_text("a"))
        self.assertFalse(result.succeeded)
        self.assertEqual(result.failure_code, "UNEXPECTED_KEY_EVENT")
        self.assertIn("unknown", {e.hand for e in result.events})

    def test_force_limit_and_bounded_unreachable_cleanup(self):
        result = StandingTypingController(FakeDriver("force")).run(StandingTypingPlanner().plan_text("a"))
        self.assertEqual(result.failure_code, "FORCE_LIMIT")
        self.assertTrue(result.cleanup.physical_clear)
        frozen = StandingTypingController(FakeDriver("frozen"), maximum_phase_steps=5).run(StandingTypingPlanner().plan_text("a"))
        self.assertEqual(frozen.failure_code, "MOTION_TIMEOUT")
        self.assertLessEqual(frozen.physics_steps, 20)

    def test_stuck_key_cannot_be_cleared_by_logical_cleanup(self):
        result = StandingTypingController(FakeDriver("stuck"), maximum_phase_steps=500).run(StandingTypingPlanner().plan_text("a"))
        self.assertEqual(result.failure_code, "STUCK_KEY")
        self.assertFalse(result.cleanup.physical_clear)
        self.assertIn("a", result.cleanup.depressed_keys)
        self.assertIn("a", result.cleanup.keys_in_contact)
        self.assertNotIn("cleanup_park", {item["phase"] for item in result.phase_trace})
        # A bridge timeout is not accepted as physical release evidence.
        self.assertFalse(result.verification.physical_acceptance)

    def test_independent_text_sink_mismatch_fails(self):
        class WrongSink(DigitalTextBuffer):
            def snapshot(self):
                return "wrong" if self.text else ""
        result = StandingTypingController(FakeDriver(), text_sink=WrongSink()).run(StandingTypingPlanner().plan_text("a"))
        self.assertEqual(result.failure_code, "TEXT_MISMATCH")
        self.assertFalse(result.succeeded)

    def test_incomplete_keyboard_sensor_coverage_fails_closed(self):
        class PartialDriver(FakeDriver):
            def observe(self):
                frame = super().observe()
                frame.travel.pop("escape")
                return frame
        result = StandingTypingController(PartialDriver()).run(StandingTypingPlanner().plan_text("a"))
        self.assertEqual(result.failure_code, "INCOMPLETE_SENSOR_COVERAGE")
        self.assertFalse(result.cleanup.physical_clear)

    def test_synthetic_frame_cannot_claim_physical_acceptance(self):
        class SyntheticDriver(FakeDriver):
            def observe(self):
                f = super().observe()
                return Frame(f.tick, f.time_s, f.tips, f.travel, f.forces, f.index_forces, "synthetic_replay")
        result = StandingTypingController(SyntheticDriver()).run(StandingTypingPlanner().plan_text("a"))
        self.assertEqual(result.failure_code, "UNTRUSTED_FRAME")
        self.assertFalse(result.verification.physical_acceptance)
        self.assertFalse(result.cleanup.physical_clear)
        self.assertFalse(result.succeeded)

    def test_nonactuating_off_target_contact_is_not_ignored(self):
        class BrushDriver(FakeDriver):
            def observe(self):
                f = super().observe()
                if f.tick == 5:
                    f.forces["z"] = 0.5
                    f.travel["z"] = 0.001
                return f
        result = StandingTypingController(BrushDriver()).run(StandingTypingPlanner().plan_text("jk"))
        self.assertEqual(result.failure_code, "ADJACENT_KEY_CONTACT")
        self.assertTrue(result.cleanup.physical_clear)

    def test_cancellation_releases_real_contact_and_returns_failure(self):
        class InterruptedDriver(FakeDriver):
            def __init__(self):
                super().__init__()
                self.pressed_steps = 0
                self.interrupted = False

            def step(self, targets):
                frame = super().step(targets)
                if self.was_pressed and not self.interrupted:
                    self.pressed_steps += 1
                    if self.pressed_steps == 3:
                        self.interrupted = True
                        raise KeyboardInterrupt()
                return frame
        result = StandingTypingController(InterruptedDriver()).run(StandingTypingPlanner().plan_text("a"))
        self.assertEqual(result.failure_code, "CANCELLED")
        self.assertFalse(result.succeeded)
        self.assertTrue(result.cleanup.attempted)
        self.assertTrue(result.cleanup.physical_clear)
        self.assertEqual([(e.event_type, e.key) for e in result.events], [("keydown", "a"), ("keyup", "a")])

    def test_malformed_initial_tip_does_not_escape_or_claim_cleanup(self):
        class MalformedDriver(FakeDriver):
            def observe(self):
                frame = super().observe()
                frame.tips["left"] = (0.1, 0.2)
                return frame
        result = StandingTypingController(MalformedDriver()).run(StandingTypingPlanner().plan_text("a"))
        self.assertEqual(result.failure_code, "INVALID_PHYSICAL_FRAME")
        self.assertFalse(result.cleanup.physical_clear)
        self.assertFalse(result.succeeded)

    def test_sensor_trust_loss_cannot_reuse_old_clear_cleanup_frame(self):
        class LostSensorDriver(FakeDriver):
            def observe(self):
                f = super().observe()
                return Frame(f.tick, f.time_s, f.tips, f.travel, f.forces, f.index_forces,
                    "synthetic_replay" if self.tick >= 3 else "isaac_physx")
        result = StandingTypingController(LostSensorDriver()).run(StandingTypingPlanner().plan_text("a"))
        self.assertEqual(result.failure_code, "UNTRUSTED_FRAME")
        self.assertFalse(result.cleanup.physical_clear)
        self.assertIsNotNone(result.cleanup.error)


if __name__ == "__main__":
    unittest.main()
