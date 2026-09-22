from __future__ import annotations

import json
import math
import random
import tempfile
import types
import unittest
from dataclasses import asdict, replace
from pathlib import Path
from unittest.mock import patch

from vskill_os.training import (
    CartesianResidual, ContactEvidence, DomainRandomization, KeyEvent,
    Observation, ResidualPolicy, TrainingConfig, evaluate, goal_from_text,
    load_checkpoint, run_episode, train,
)
from vskill_os.training.curriculum import STAGES, episode_seed, sample_goal
from vskill_os.training.environment import SyntheticTypingEnvironment, make_environment
from vskill_os.training.verifier import TrainingVerifier


class StandingTrainingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = TrainingConfig(population=4, elite_count=2,
                                     episodes_per_candidate=2, curriculum_evaluation_episodes=2)
        self.randomization = DomainRandomization()

    def observation(self, **changes) -> Observation:
        default = Observation(0, ((0.72, 0.12, 0.79), (0.72, -0.12, 0.79)),
                              ((0.72, 0.12, 0.79), (0.72, -0.12, 0.79)),
                              (), (), (), "")
        return replace(default, **changes)

    def test_goal_reuses_cooperative_planner_with_opposite_index_shift(self) -> None:
        goal = goal_from_text("Afj!")
        for stroke in goal.strokes:
            if stroke.shift_hand:
                self.assertNotEqual(stroke.primary_hand, stroke.shift_hand)
                self.assertEqual(stroke.shift_key, f"{stroke.shift_hand}_shift")
        pair = goal_from_text("ak").strokes
        self.assertEqual(pair[0].wave, pair[1].wave)
        self.assertNotEqual(pair[0].primary_hand, pair[1].primary_hand)
        with self.assertRaises(ValueError):
            goal_from_text("hello\n")

    def test_every_curriculum_stage_runs_with_sensor_verifier(self) -> None:
        for stage in STAGES:
            goal = sample_goal(stage, 43)
            env = SyntheticTypingEnvironment(self.config)
            result = run_episode(env, ResidualPolicy(), goal=goal, seed=43,
                                 config=self.config, randomization=self.randomization, split="evaluation")
            self.assertTrue(result["success"], result)
            self.assertEqual(result["observed_text"], goal.text)
            self.assertFalse(result["physical_acceptance"])
            self.assertEqual(result["evidence_kind"], "synthetic_non_physics")

    def test_policy_bounds_norm_and_rejects_invalid_commands(self) -> None:
        action = ResidualPolicy((1e8,) * 12).action(self.observation(), self.config)
        for vector in (action.left_xyz_m, action.right_xyz_m):
            self.assertLessEqual(math.dist(vector, (0, 0, 0)), self.config.residual_limit_m)
        for vector in ((float("nan"), 0, 0), (0.1, 0, 0), (0.003, 0.003, 0.003)):
            with self.assertRaises(ValueError):
                CartesianResidual(left_xyz_m=vector).validated(self.config.residual_limit_m)
        with self.assertRaises(ValueError):
            ResidualPolicy((0.0,) * 11)

    def test_screen_text_alone_cannot_complete_a_goal(self) -> None:
        verifier = TrainingVerifier(goal_from_text("f"), self.config)
        verifier.update(self.observation())
        result = verifier.update(self.observation(tick=1, screen_text="f"))
        self.assertFalse(result.complete)
        self.assertEqual(result.failure_code, "DISPLAY_TEXT_MISMATCH")

    def test_keydown_requires_correct_index_contact_and_travel(self) -> None:
        for contact in ((), (ContactEvidence("f", "left", "thumb", 1, 0.003),),
                        (ContactEvidence("f", "left", "index", 1, 0.0005),),
                        (ContactEvidence("f", "right", "index", 1, 0.003),)):
            verifier = TrainingVerifier(goal_from_text("f"), self.config)
            verifier.update(self.observation())
            result = verifier.update(self.observation(tick=1, contacts=contact,
                                    key_events=(KeyEvent("f", "keydown"),),
                                    pressed_keys=("f",), screen_text="f"))
            self.assertFalse(result.complete)
            self.assertIsNotNone(result.failure_code)

    def test_keyup_must_have_physical_contact_release(self) -> None:
        verifier = TrainingVerifier(goal_from_text("f"), self.config)
        verifier.update(self.observation())
        contact = (ContactEvidence("f", "left", "index", 1, 0.003),)
        accepted = verifier.update(self.observation(tick=1, contacts=contact,
                                   key_events=(KeyEvent("f", "keydown"),),
                                   pressed_keys=("f",), screen_text="f"))
        self.assertEqual(accepted.accepted_events, 1)
        result = verifier.update(self.observation(tick=2, contacts=contact,
                                 key_events=(KeyEvent("f", "keyup"),), screen_text="f"))
        self.assertEqual(result.failure_code, "KEYUP_WITHOUT_PHYSICAL_RELEASE")

    def test_shift_requires_ordered_modifiers_and_release(self) -> None:
        goal = goal_from_text("A")
        verifier = TrainingVerifier(goal, self.config)
        verifier.update(self.observation())
        primary = goal.strokes[0]
        result = verifier.update(self.observation(tick=1, contacts=(
            ContactEvidence(primary.key, primary.primary_hand, "index", 1, 0.003),),
            key_events=(KeyEvent(primary.key, "keydown"),),
            pressed_keys=(primary.key,), screen_text="A"))
        self.assertEqual(result.failure_code, "KEY_EVENT_ORDER_MISMATCH")

    def test_stale_observations_and_safety_faults_terminate(self) -> None:
        faults = (
            ({"standing": False}, "STANDING_STABILITY_LOST"),
            ({"foot_slip_m": 0.1}, "FOOT_SLIP"),
            ({"forbidden_collision": True}, "FORBIDDEN_COLLISION"),
            ({"contacts": (ContactEvidence("f", "left", "index", 20, 0.003),)}, "CONTACT_FORCE_LIMIT"),
            ({"base_tilt_rad": float("nan")}, "INVALID_OBSERVATION"),
        )
        for changes, expected in faults:
            verifier = TrainingVerifier(goal_from_text("f"), self.config)
            result = verifier.update(self.observation(**changes))
            self.assertEqual(result.failure_code, expected)
        verifier = TrainingVerifier(goal_from_text("f"), self.config)
        verifier.update(self.observation())
        self.assertEqual(verifier.update(self.observation()).failure_code,
                         "STALE_OR_SKIPPED_OBSERVATION")

    def test_time_limit_is_truncation_not_success(self) -> None:
        config = replace(self.config, max_steps=1)
        row = run_episode(SyntheticTypingEnvironment(config), ResidualPolicy(),
                          goal=goal_from_text("hello"), seed=42, config=config,
                          randomization=self.randomization, split="evaluation")
        self.assertFalse(row["success"])
        self.assertFalse(row["terminated"])
        self.assertTrue(row["truncated"])
        self.assertEqual(row["failure_code"], "TIME_LIMIT")
        self.assertTrue(row["cleanup"]["attempted"])
        self.assertTrue(row["cleanup"]["released"])
        self.assertFalse(row["cleanup"]["contacts"])
        self.assertFalse(row["cleanup"]["pressed_keys"])

    def test_fault_and_exception_trigger_actual_bounded_release(self) -> None:
        class FaultEnvironment(SyntheticTypingEnvironment):
            release_calls = 0
            raise_error = False

            def step(self, action):
                result = super().step(action)
                if self.raise_error:
                    raise ValueError("original sensor fault")
                return replace(result, standing=False)

            def emergency_release(self):
                self.release_calls += 1
                return super().emergency_release()

        env = FaultEnvironment(self.config)
        row = run_episode(env, ResidualPolicy(), goal=goal_from_text("f"), seed=1,
                          config=self.config, randomization=self.randomization, split="evaluation")
        self.assertEqual(row["failure_code"], "STANDING_STABILITY_LOST")
        self.assertEqual(env.release_calls, 1)
        self.assertTrue(row["cleanup"]["released"])
        env.raise_error = True
        with self.assertRaisesRegex(ValueError, "original sensor fault"):
            run_episode(env, ResidualPolicy(), goal=goal_from_text("f"), seed=1,
                        config=self.config, randomization=self.randomization, split="evaluation")
        self.assertEqual(env.release_calls, 2)

    def test_failed_cleanup_stops_training_and_preserves_original_exception(self) -> None:
        class BadCleanup(SyntheticTypingEnvironment):
            def emergency_release(self):
                raise RuntimeError("release actuator unavailable")

        config = replace(self.config, max_steps=1)
        env = BadCleanup(config)
        with self.assertRaisesRegex(RuntimeError, "release actuator unavailable"):
            run_episode(env, ResidualPolicy(), goal=goal_from_text("f"), seed=1,
                        config=config, randomization=self.randomization, split="evaluation")
        with patch.object(env, "step", side_effect=ValueError("sensor disconnected")):
            with self.assertRaisesRegex(ValueError, "sensor disconnected") as raised:
                run_episode(env, ResidualPolicy(), goal=goal_from_text("f"), seed=1,
                            config=config, randomization=self.randomization, split="evaluation")
        self.assertIn("release actuator unavailable", raised.exception.__notes__[0])

    def test_seed_namespaces_are_disjoint_and_do_not_change_global_rng(self) -> None:
        groups = [{episode_seed(42, split, index) for index in range(20)}
                  for split in ("train", "validation", "evaluation")]
        self.assertEqual(len(set.union(*groups)), 60)
        previous = random.getstate()
        env = SyntheticTypingEnvironment(self.config)
        first = env.reset(goal=goal_from_text("Hi!"), seed=42, randomization=self.randomization)
        keys = dict(env.keys)
        second = env.reset(goal=goal_from_text("Hi!"), seed=42, randomization=self.randomization)
        self.assertEqual(first, second)
        self.assertEqual(random.getstate(), previous)
        env.reset(goal=goal_from_text("Hi!"), seed=43, randomization=self.randomization)
        self.assertNotEqual(keys, env.keys)

    def test_checkpoint_resume_is_identical_to_uninterrupted_training(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            uninterrupted = train(self.config, self.randomization, output=root / "full", iterations=2)
            train(self.config, self.randomization, output=root / "resume", iterations=1)
            resumed = train(self.config, self.randomization, output=root / "resume", iterations=1,
                            resume=root / "resume/checkpoint.json")
            for field in ("parameters", "std", "rng_state", "iteration", "stage", "last_validation"):
                self.assertEqual(uninterrupted[field], resumed[field], field)
            self.assertNotEqual(resumed["parameters"], [0.0] * 12)
            loaded = load_checkpoint(root / "resume/checkpoint.json")
            with self.assertRaisesRegex(ValueError, "output already contains"):
                train(self.config, self.randomization, output=root / "resume", iterations=1)
            report = evaluate(loaded, episodes_per_stage=2)
            self.assertEqual(report["summary"]["episodes"], 8)
            self.assertEqual(set(report["by_stage"]), set(STAGES))
            training_rows = [json.loads(line) for line in (root / "resume/episodes.jsonl").read_text().splitlines()]
            used_seeds = {row["seed"] for row in training_rows}
            self.assertFalse(used_seeds & {row["seed"] for row in report["episodes"]})
            with self.assertRaises(ValueError):
                train(replace(self.config, seed=43), self.randomization, output=root / "bad", iterations=1,
                      resume=root / "resume/checkpoint.json")

    def test_external_adapter_hook_receives_config_and_closes(self) -> None:
        module = types.ModuleType("training_test_adapter")
        instances = []

        def factory(*, config):
            env = SyntheticTypingEnvironment(config)
            env.evidence_kind = "physics_sensor"  # self-classification alone is NOT physical acceptance
            instances.append(env)
            return env

        module.factory = factory
        config = replace(self.config, adapter="training_test_adapter:factory")
        with patch.dict("sys.modules", {module.__name__: module}), tempfile.TemporaryDirectory() as directory:
            result = train(config, self.randomization, output=Path(directory), iterations=1)
            self.assertEqual(result["config"], asdict(config))
            self.assertEqual(result["evidence_kind"], "physics_sensor")
            self.assertFalse(instances[-1]._has_reset)
            report = evaluate(result, episodes_per_stage=1)
            self.assertEqual(report["summary"]["physical_acceptance_episodes"], 0)

    def test_invalid_configuration_and_adapter_fail_fast(self) -> None:
        for changes in ({"population": 1}, {"max_steps": 0}, {"residual_limit_m": float("nan")},
                        {"seed": -1}, {"elite_count": 20}, {"version": "future"},
                        {"residual_limit_m": 0.005}, {"residual_limit_m": 0}):
            with self.assertRaises(ValueError):
                replace(self.config, **changes)
        with self.assertRaises(ValueError):
            DomainRandomization(tracking_gain_min=2.0)
        with self.assertRaises(ValueError):
            make_environment(replace(self.config, adapter="missing_factory_separator"))


if __name__ == "__main__":
    unittest.main()
