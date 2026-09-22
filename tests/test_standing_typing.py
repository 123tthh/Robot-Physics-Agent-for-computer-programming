from __future__ import annotations

from dataclasses import replace
import itertools
import json
import math
import unittest

from vskill_os.standing_typing import (
    HANDS, PARK_POSITIONS, ObservedKeyEvent, PressObservation,
    StandingPlanningError, StandingTypingPlanner, StandingTypingVerifier,
    keyboard_positions, synthetic_replay,
)


class StandingTypingTests(unittest.TestCase):
    def setUp(self):
        self.planner = StandingTypingPlanner()

    def test_geometry_matches_all_74_physical_keycaps(self):
        from vskill_os.adapters.isaac.p1_scene import standard_keyboard_layout, keyboard_key_center
        expected = {spec.name: keyboard_key_center(spec, operator_row_x=0.72, left_origin_y=0.145, top_z=0.7602) for spec in standard_keyboard_layout()}
        self.assertEqual(len(expected), 74)
        self.assertEqual(keyboard_positions(), expected)

    def test_deterministic_index_only_plan_and_coordinated_order(self):
        plan = self.planner.plan_text("afjk Hello!\b ")
        self.assertEqual(plan.to_dict(), self.planner.plan_text(plan.text).to_dict())
        self.assertEqual(plan.plan_id, self.planner.plan_text(plan.text).plan_id)
        self.assertEqual({t.hand for s in plan.steps for t in s.targets}, set(HANDS))
        self.assertTrue(all(t.finger == "index" for s in plan.steps for t in s.targets))
        self.assertTrue(any(len(w.step_indices) == 2 for w in plan.waves))
        self.assertEqual([i for w in plan.waves for i in w.step_indices], list(range(len(plan.text))))
        self.assertNotIn("joint_position", json.dumps(plan.to_dict()))
        for wave in plan.waves:
            if len(wave.targets) == 2:
                by_hand = {t.hand: t for t in wave.targets}
                self.assertGreaterEqual(by_hand["left"].xyz_m[1] - by_hand["right"].xyz_m[1], plan.minimum_hand_separation_m)
            else:
                self.assertEqual(len(wave.parked_hands), 1)

    def test_global_dp_matches_exhaustive_hand_assignment_cost(self):
        text = "byhgn"
        candidates = [self.planner._candidates(char, None) for char in text]
        costs = []
        for path in itertools.product(*candidates):
            last = dict(PARK_POSITIONS)
            cost = 0.0
            for targets in path:
                for t in targets:
                    cost += math.dist(last[t.hand][:2], t.xyz_m[:2])
                    last[t.hand] = t.xyz_m
            costs.append(cost)
        self.assertAlmostEqual(self.planner.plan_text(text).movement_cost_m, min(costs), places=8)

    def test_shift_uses_opposite_index_and_three_key_chord_is_rejected(self):
        for step in self.planner.plan_text("Az!?").steps:
            if step.modifier:
                self.assertNotEqual(step.primary.hand, step.modifier.hand)
                self.assertTrue(step.modifier.key.endswith("shift"))
        with self.assertRaises(StandingPlanningError) as caught:
            self.planner.plan_chord(("ctrl", "alt", "t"))
        self.assertEqual(caught.exception.code, "TWO_INDEX_CAPACITY")
        with self.assertRaises(StandingPlanningError):
            self.planner.target("left", "enter")
        with self.assertRaises(StandingPlanningError):
            self.planner.plan_text("你好")

    def _evidence(self, text="afjk"):
        plan = self.planner.plan_text(text)
        observations = tuple(PressObservation(s.index, t.key, t.hand, 1.5, 0.0022, True, 0.035) for s in plan.steps for t in s.targets)
        events = tuple(ObservedKeyEvent(e.step_index, e.event_type, e.key, e.hand, (i + 1) * 0.05) for i, e in enumerate(plan.expected_events))
        return plan, observations, events

    def test_verifier_accepts_complete_physical_trace(self):
        plan, observations, events = self._evidence()
        result = StandingTypingVerifier().verify(plan, observations=observations, events=events, observed_text=plan.text)
        self.assertTrue(result.succeeded)
        self.assertTrue(result.physical_acceptance)

    def test_events_do_not_substitute_for_contact_or_release(self):
        plan, observations, events = self._evidence()
        for altered, code in ((replace(observations[0], travel_m=0.0), "CONTACT_MISSING"), (replace(observations[0], released=False), "PHYSICAL_RELEASE_MISSING"), (replace(observations[0], source="controller"), "UNTRUSTED_EVIDENCE"), (replace(observations[0], adjacent_contacts=("x",)), "ADJACENT_KEY_CONTACT"), (replace(observations[0], finger="middle"), "WRONG_FINGER"), (replace(observations[0], contact_force_n=float("nan")), "INVALID_PHYSICAL_EVIDENCE")):
            with self.subTest(code=code):
                result = StandingTypingVerifier().verify(plan, observations=(altered, *observations[1:]), events=events, observed_text=plan.text)
                self.assertIn(code, result.failure_codes)
                self.assertFalse(result.physical_acceptance)

    def test_exact_event_order_rejects_wrong_extra_missing_and_reordered(self):
        plan, observations, events = self._evidence()
        bad_streams = ((replace(events[0], key="x"), *events[1:]), (*events, events[-1]), events[:-1], (events[1], events[0], *events[2:]))
        for stream in bad_streams:
            result = StandingTypingVerifier().verify(plan, observations=observations, events=stream, observed_text=plan.text)
            self.assertIn("EVENT_MISMATCH", result.failure_codes)
        result = StandingTypingVerifier().verify(plan, observations=observations, events=events, observed_text="wrong")
        self.assertIn("TEXT_MISMATCH", result.failure_codes)

    def test_synthetic_replay_is_explicit_and_not_physical_acceptance(self):
        plan = self.planner.plan_text("hello world")
        report = synthetic_replay(plan)
        self.assertTrue(report["verification"]["succeeded"])
        self.assertFalse(report["physical_simulation_executed"])
        self.assertFalse(report["verification"]["physical_acceptance"])
        self.assertEqual(report["verification"]["evidence_mode"], "synthetic")
        observations = [PressObservation(**o) for o in report["observations"]]
        events = [ObservedKeyEvent(**e) for e in report["events"]]
        result = StandingTypingVerifier().verify(plan, observations=observations, events=events, observed_text=plan.text)
        self.assertIn("UNTRUSTED_EVIDENCE", result.failure_codes)


if __name__ == "__main__":
    unittest.main()
