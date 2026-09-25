"""P4 contract/training regression cases. Authored but not run in this change."""

from dataclasses import replace
import tempfile
from pathlib import Path
import unittest

from vskill_os.chair.demo import run_demo
from vskill_os.chair.model import ChairCommand, Observation, flatten
from vskill_os.chair.proxy import ProxyExecutor, ProxyObserver, ProxyWorld
from vskill_os.chair.runner import Decision, Transition, generalized_advantages
from vskill_os.chair.skill import ChairRepositionSkill, INVALIDATED
from vskill_os.chair.model import Trace
from vskill_os.chair.verification import ChairVerifier
from vskill_os.chair.training import BackendFrame, ChairTrainingEnv, TrainingConfig, experiment_matrix
from vskill_os.control.commands import RootVelocityCommand
from vskill_os.domain import SkillStatus, StateSnapshot
from vskill_os.robot.seated_policy import EXPECTED_ACTOR_OBSERVATIONS, EXPECTED_CRITIC_ONLY_OBSERVATIONS


class ObservationTests(unittest.TestCase):
    def test_actor_does_not_receive_privileged_values(self):
        actor = {name: (0.0,) * size for name, size in EXPECTED_ACTOR_OBSERVATIONS}
        clean = dict(actor, torso_angular_velocity=(1.0, 2.0, 3.0))
        privileged = {name: (7.0,) * size for name, size in EXPECTED_CRITIC_ONLY_OBSERVATIONS}
        obs = Observation.pack(actor, clean, privileged)
        self.assertEqual(len(obs.actor), 96)
        self.assertEqual(len(obs.critic), 217)
        self.assertNotIn(7.0, obs.actor)
        self.assertEqual(obs.critic[:3], (1.0, 2.0, 3.0))
        with self.assertRaises(ValueError):
            flatten(dict(actor, chair_pose=(0.0, 0.0, 0.0)), EXPECTED_ACTOR_OBSERVATIONS)

    def test_invalid_commands_and_nonfinite_observations(self):
        for velocity, duration in ((RootVelocityCommand(), 2), (RootVelocityCommand(.4, .4), 2),
                                   (RootVelocityCommand(.1), 5.1), (RootVelocityCommand(.1), float("nan"))):
            with self.assertRaises(ValueError):
                ChairCommand(velocity, duration)
        with self.assertRaises(ValueError):
            Observation((float("nan"),) * 96, (0.0,) * 217)

    def test_full_factorial_plan_is_not_a_result(self):
        matrix = experiment_matrix(TrainingConfig())
        self.assertEqual(len(matrix), 32)
        self.assertEqual(len({(c["seed"], c["symmetry"], c["foot_slip"], c["curriculum"]) for c in matrix}), 32)


class DemoTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_invalidates_old_keyboard_geometry(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = await run_demo(Path(tmp) / "run")
            self.assertEqual(report["result"]["status"], "succeeded")
            self.assertTrue(report["verdict"]["accepted"])
            self.assertNotIn("keyboard_calibrated", report["final_facts"])
            self.assertIn("work_pose_ready", report["final_facts"])
            self.assertFalse(report["physical_success_verified"])
            self.assertTrue(report["safe_hold_requested"])

    async def test_faults_cannot_pass_and_clean_retry_can(self):
        with tempfile.TemporaryDirectory() as tmp:
            for fault in ("stationary", "wrong_way", "chair_stuck", "seat_loss", "stale", "work_pose"):
                with self.subTest(fault=fault):
                    report = await run_demo(Path(tmp) / fault, fault)
                    self.assertEqual(report["result"]["status"], "failed")
                    self.assertNotIn("work_pose_ready", report["final_facts"])
                    self.assertNotIn("seated_reposition_verified", report["final_facts"])
                    self.assertTrue(report["safe_hold_requested"])
            # A new episode starts from explicitly supplied synthetic safe facts;
            # this is not an automatic physical recovery controller.
            retry = await run_demo(Path(tmp) / "retry")
            self.assertEqual(retry["result"]["status"], "succeeded")

    async def test_cancellation_requests_safe_hold_and_invalidates(self):
        import asyncio

        class CanceledExecutor(ProxyExecutor):
            async def advance(self, command, dt):
                raise asyncio.CancelledError()

        world = ProxyWorld()
        skill = ChairRepositionSkill(CanceledExecutor(world), ProxyObserver(world))
        state = StateSnapshot(facts=set(skill.contract.preconditions))
        result = await skill.execute(state, {"vx_mps": .1})
        self.assertEqual(result.failure_code, "SKILL_CANCELED")
        self.assertTrue(world.stopped)
        self.assertEqual(result.invalidated_states, INVALIDATED)

    async def test_stop_failure_revokes_robot_safe(self):
        class BrokenStop(ProxyExecutor):
            def stop(self):
                raise OSError("hold controller unavailable")

        world = ProxyWorld()
        skill = ChairRepositionSkill(BrokenStop(world), ProxyObserver(world))
        result = await skill.execute(StateSnapshot(facts=set(skill.contract.preconditions)), {"vx_mps": .1})
        self.assertEqual(result.status, SkillStatus.FAILED)
        self.assertEqual(result.failure_code, "STOP_FAILED")
        self.assertIn("robot_safe", result.invalidated_states)

    async def test_raw_trace_rejects_wrong_source_and_short_settle(self):
        world = ProxyWorld()
        observer = ProxyObserver(world)
        skill = ChairRepositionSkill(ProxyExecutor(world), observer)
        result = await skill.execute(StateSnapshot(facts=set(skill.contract.preconditions)), {"vx_mps": .1})
        trace = Trace.from_dict(result.evidence[0].value)
        checker = ChairVerifier(observer_source=observer.source)
        self.assertTrue(checker.verify(trace).accepted)
        bad = replace(trace, samples=(replace(trace.samples[0], source="controller_claim"),) + trace.samples[1:])
        self.assertIn("UNTRUSTED_EVIDENCE", checker.verify(bad).failures)
        short = replace(trace, samples=trace.samples[:trace.motion_end_index + 2])
        self.assertIn("NOT_SETTLED", checker.verify(short).failures)


class GAETests(unittest.TestCase):
    def test_truncation_bootstraps_without_leaking_next_episode(self):
        obs = Observation((0.0,) * 96, (0.0,) * 217)
        decision = Decision((0.0,) * 29, 0.0, 2.0)
        truncated = Transition(obs, decision, 1.0, 3.0, False, True)
        terminal = Transition(obs, decision, 100.0, 1000.0, True, False)
        advantages, returns = generalized_advantages((truncated, terminal), gamma=.9, lam=1)
        self.assertAlmostEqual(advantages[0], 1.7)
        self.assertAlmostEqual(returns[0], 3.7)
        self.assertAlmostEqual(returns[1], 100.0)


class TrainingTests(unittest.TestCase):
    def backend(self):
        from vskill_os.chair.model import ChairSample

        class Backend:
            evidence_level = "simulation_oracle"

            def reset(self, spec, command):
                self.spec = spec
                self.sample = ChairSample("test", "test_oracle", 0.0, (0., 0., 0.),
                                          (0., 0., 0.), .99, 0., .01, .02, .02,
                                          (True, True), True, True)
                return self.read(command)

            def read(self, command):
                actor = {name: (0.0,) * size for name, size in EXPECTED_ACTOR_OBSERVATIONS}
                actor["velocity_command"] = (command.vx_mps, command.vy_mps, command.yaw_rate_rps)
                privileged = {name: (0.0,) * size for name, size in EXPECTED_CRITIC_ONLY_OBSERVATIONS}
                return BackendFrame(Observation.pack(actor, actor, privileged), self.sample,
                                    (0.,) * 29, (0.,) * 29, (0., 0.))

            def step(self, action, command):
                self.sample = replace(self.sample, time_s=self.sample.time_s + .02)
                return self.read(command)

            def close(self):
                pass

        return Backend()

    def test_timeout_is_truncation_and_requires_reset(self):
        env = ChairTrainingEnv(self.backend(), TrainingConfig(episode_s=.04, command_hold_s=.02))
        env.reset(seed=12)
        first = env.step((0.,) * 29)
        self.assertFalse(first.truncated)
        last = env.step((0.,) * 29)
        self.assertTrue(last.truncated)
        self.assertFalse(last.terminated)
        with self.assertRaises(RuntimeError):
            env.step((0.,) * 29)

    def test_physical_failure_is_termination_and_curriculum_needs_evidence(self):
        backend = self.backend()
        env = ChairTrainingEnv(backend, TrainingConfig())
        env.reset(seed=12)
        backend.sample = replace(backend.sample, contact_loss_s=1.0)
        self.assertTrue(env.step((0.,) * 29).terminated)
        env.record_curriculum_evaluation(episodes=20, success_rate=.99, stationary_rate=.8)
        self.assertEqual(env.level, 0)
        env.record_curriculum_evaluation(episodes=20, success_rate=.99, stationary_rate=.0)
        self.assertEqual(env.level, 1)


if __name__ == "__main__":
    unittest.main()
