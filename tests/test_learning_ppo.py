"""PPO contracts and synthetic tensor tests; no physical success claims."""

from dataclasses import replace
import copy
import importlib.util
import json
import math
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from vskill_os.learning.ppo_config import PPOConfig, PressRewardSpec, teacher_feature_names
from vskill_os.learning.policy_manifest import ActionSchema, ActionScale, dex5_action_names
from vskill_os.learning.environment import JointControlSpec, JointTargetAction, JointTargetGuard

TORCH = importlib.util.find_spec('torch') is not None


class PPOContractTests(unittest.TestCase):
    def test_frozen_budget_roundtrip_and_no_oracle_features(self):
        self.assertEqual(PPOConfig.from_json(PPOConfig().to_json()), PPOConfig())
        names = teacher_feature_names(dex5_action_names('left'))
        self.assertEqual(len(names), 102)
        self.assertFalse(any('text' in n or 'verifier' in n or 'success' in n for n in names))
        for changes in ({'transitions': 999999}, {'gamma': math.nan}, {'num_envs': 32},
                        {'horizon_s': 10.}, {'evaluation_seeds': (7,)}):
            with self.assertRaises(ValueError):
                replace(PPOConfig(), **changes)
        with self.assertRaises(ValueError):
            replace(PressRewardSpec(), completion=1.)

    def test_launch_config_binding_survives_json_arrays_and_rejects_changed_config(self):
        from vskill_os.adapters.isaac.learning.config import SingleHandConfig
        from vskill_os.adapters.isaac.learning.ppo_verify import _check_launch_binding
        config, physical, scale = PPOConfig(), SingleHandConfig(), ActionScale()
        sources = {'worker.py': 'f'*64}
        launch = json.loads(json.dumps(dict(version='vf-ppo-launch/1', ppo_config=config.to_dict(),
            physical_config=physical.to_dict(), action_scale=scale.to_dict(), source_hashes=sources)))
        _check_launch_binding(launch, config, physical, scale, sources)
        for field, key, value in (('ppo_config', 'seed', 8), ('physical_config', 'maximum_contact_force_n', 11.),
                                  ('action_scale', 'delta_rad', [.02]*23), ('source_hashes', 'worker.py', 'e'*64)):
            changed = copy.deepcopy(launch)
            changed[field][key] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                _check_launch_binding(changed, config, physical, scale, sources)


@unittest.skipUnless(TORCH, 'optional PyTorch adapter; run with the Isaac/PyTorch interpreter')
class TensorPPOTests(unittest.TestCase):
    def setUp(self):
        import torch
        self.t = torch
        torch.set_num_threads(1)
        torch.manual_seed(19)
        names = dex5_action_names('left')
        self.control = JointControlSpec(names, (-1.,)*23, (1.,)*23, (.3,)*23, (names[12],), 1/60)
        self.schema = ActionSchema.from_control(self.control, ActionScale())

    def test_gae_bootstraps_timeout_terminal_state_and_breaks_reset_recursion(self):
        from vskill_os.adapters.learning_torch.ppo import generalized_advantage
        t = self.t
        r = t.tensor([[1.], [2.], [100.]])
        values = t.tensor([[3.], [4.], [999.]])
        next_values = t.tensor([[4.], [7.], [123.]])
        term = t.tensor([[False], [False], [True]])
        trunc = t.tensor([[False], [True], [False]])
        adv, ret = generalized_advantage(r, values, next_values, term, trunc, .9, .8)
        timeout_delta = 2.+.9*7.-4.
        self.assertAlmostEqual(adv[1].item(), timeout_delta, places=5)
        self.assertAlmostEqual(adv[0].item(), 1.+.9*4.-3.+.9*.8*timeout_delta, places=5)
        self.assertAlmostEqual(ret[2].item(), 100., places=5)

    def test_squashed_log_probability_is_finite_and_roundtrips_latent(self):
        from vskill_os.adapters.learning_torch.ppo import ActorCritic
        t = self.t
        model = ActorCritic(102, 1)
        obs = t.randn(64, 102)
        action, z, logp, values = model.act(obs)
        replay, _, replay_value = model.evaluate(obs, z)
        self.assertTrue(t.equal(logp, replay))
        self.assertTrue(t.equal(values, replay_value))
        self.assertTrue((action.abs() <= 1).all())
        for latent in (-100., 100.):
            result = model.evaluate(obs, t.full((64, 1), latent))[0]
            self.assertTrue(t.isfinite(result).all())

    def test_ppo_update_changes_weights_without_using_masked_policy_columns(self):
        from vskill_os.adapters.learning_torch.ppo import ActorCritic, PPOTrainer
        t = self.t
        config = replace(PPOConfig(), num_envs=16, transitions=64, rollout_steps=4, minibatch_size=32)
        model = ActorCritic(102, 1)
        trainer = PPOTrainer(model, config)
        obs = t.randn(4, 16, 102)
        _, z, logp, values = model.act(obs)
        before = model.actor[-1].weight.detach().clone()
        result = trainer.update(dict(observations=obs, latent=z, log_prob=logp, values=values,
            rewards=z.squeeze(-1), next_values=t.zeros(4, 16),
            terminated=t.zeros(4, 16, dtype=t.bool), truncated=t.zeros(4, 16, dtype=t.bool)))
        self.assertGreater(result['optimizer_steps'], 0)
        self.assertFalse(t.equal(before, model.actor[-1].weight))
        self.assertEqual(model.log_std.numel(), 1)

    def test_tensor_action_guard_matches_scalar_mask_rate_and_limits(self):
        from vskill_os.adapters.isaac.learning.training_safety import TensorActionGuard
        t = self.t
        guard = TensorActionGuard(self.schema, 'cpu')
        targets = t.rand(128, 23)*2-1
        targets[:2] = t.tensor((.999, -.999))[:, None]
        actions = t.rand(128, 23)*2-1
        _, actual = guard.apply(targets, actions)
        for i in range(128):
            delta = tuple(float(a)*s if n in self.schema.mask.active else 0.
                          for a, s, n in zip(actions[i], self.schema.step_scale_rad, self.control.joint_names))
            a = JointTargetAction(self.control.joint_names, delta, 1/60, 1/60, 'test', 0)
            expected = JointTargetGuard(self.control).apply(tuple(targets[i].tolist()), a, time_s=0., owner_token='test', expected_sequence=0)
            self.assertTrue(t.allclose(actual[i], t.tensor(expected), atol=1e-7, rtol=0.))
        with self.assertRaises(ValueError):
            guard.apply(targets, actions*100)

    def test_tensor_safety_matches_independent_scalar_monitor_for_each_failure(self):
        from vskill_os.adapters.isaac.learning.training_safety import TensorSafetyMonitor, SAFETY_CODES
        from vskill_os.adapters.isaac.learning.config import SingleHandConfig
        from vskill_os.learning.physical import CoordinateLimit, PhysicsSafetyMonitor, PhysicsSample
        from vskill_os.learning.contracts import RecordStamp
        t = self.t
        limits = (CoordinateLimit('joint0', 'rad', -1., 1., 8.), CoordinateLimit('joint1', 'rad', -1., 1., 8.))
        origins = t.tensor([[0., 0., 0.], [3., 3., 0.]])
        raw = dict(q=t.zeros(2, 2), v=t.zeros(2, 2), targets=t.zeros(2, 2), key_q=t.zeros(2, 2),
            key_targets=t.zeros(2, 2), forces=t.zeros(2, 2, 3), key_xyz=t.zeros(2, 2, 3),
            tip=t.zeros(2, 3), pelvis=origins+t.tensor([0., 0., .625]), torso=t.tensor([[1., 0., 0., 0.]]*2))
        monitor = TensorSafetyMonitor(limits, origins, SingleHandConfig())
        mutations = [('q', (0, 0), 1.05, 'JOINT_LIMIT'), ('v', (0, 1), 9., 'JOINT_SPEED'),
            ('key_q', (0, 0), -.005, 'KEY_TRAVEL_LIMIT'), ('key_targets', (0, 0), .001, 'COMMANDED_KEYCAP'),
            ('forces', (0, 0, 2), 13., 'EXCESS_FORCE'), ('pelvis', (0, 2), .67, 'UNSAFE_SUPPORT'),
            ('torso', (0, 1), 1., 'UNSAFE_POSTURE')]
        for field, index, value, code in mutations:
            changed = {k: v.clone() for k, v in raw.items()}
            changed[field][index] = value
            flags = monitor.check(changed)
            self.assertFalse(flags[1].any(), 'neighbor row must remain unaffected')
            def flat(name): return tuple(changed[name][0].flatten().tolist())
            sample = PhysicsSample(RecordStamp('test', 'env0', 'episode0', 0, 'synthetic', 0., 0., 'synthetic'),
                flat('q'), flat('v'), flat('targets'), flat('key_q'), flat('key_targets'), flat('forces'),
                flat('key_xyz'), flat('tip'), flat('pelvis'), flat('torso'))
            scalar = PhysicsSafetyMonitor(limits, maximum_force_n=12., support_origin_xyz_m=(0., 0., .625), support_tolerance_m=.03)
            expected = set(scalar.check(sample))
            actual = {name for name, hit in zip(SAFETY_CODES, flags[0].tolist()) if hit}
            self.assertEqual(actual, expected)
            self.assertIn(code, actual)
        raw['q'][0, 0] = math.nan
        self.assertTrue(monitor.check(raw)[0, 0])

    def test_progress_needs_press_up_and_sustained_clearance_once(self):
        from vskill_os.adapters.isaac.learning.rewards import PressProgress
        t = self.t
        progress = PressProgress(2, 2, 0, 'cpu')
        travel, force = t.zeros(2, 2), t.zeros(2, 2, 3)
        for _ in range(4):
            pulse, _ = progress.advance(travel, force, 1/240)
            self.assertFalse(pulse.any())
        travel[:, 0] = .0025; force[:, 0, 2] = 1.
        pulse, _ = progress.advance(travel, force, 1/240)
        self.assertFalse(pulse.any())
        travel[:, 0] = .0005
        for _ in range(4):
            pulse, _ = progress.advance(travel, force, 1/240)
            self.assertFalse(pulse.any(), 'KeyUp without contact clearance must not complete')
        force.zero_()
        pulses = []
        for _ in range(8):
            pulse, _ = progress.advance(travel, force, 1/240)
            pulses.append(pulse.clone())
        self.assertEqual(t.stack(pulses).sum().item(), 2)
        progress.reset(t.tensor([0]))
        self.assertFalse(progress.pressed_seen[0])
        self.assertTrue(progress.pressed_seen[1])

    def test_wrong_key_and_repeat_cannot_earn_completion(self):
        from vskill_os.adapters.isaac.learning.rewards import PressProgress
        t = self.t
        progress = PressProgress(1, 2, 0, 'cpu')
        travel = t.tensor([[0., .0025]])
        force = t.ones(1, 2, 3)
        pulse, mistake = progress.advance(travel, force, 1/240)
        self.assertFalse(pulse.any()); self.assertTrue(mistake.all())
        travel.zero_(); force.zero_()
        for _ in range(4): progress.advance(travel, force, 1/240)
        travel[:, 0] = .0025; force[:, 0, 2] = 1.
        progress.advance(travel, force, 1/240)
        travel.zero_(); force.zero_()
        for _ in range(4):
            pulse, mistake = progress.advance(travel, force, 1/240)
            self.assertFalse(pulse.any())

    def test_shaping_cannot_reward_stationary_hold_or_admin_release(self):
        from vskill_os.adapters.isaac.learning.rewards import reward_terms
        t = self.t
        zeros = t.zeros(1, dtype=t.bool)
        terms = reward_terms(PressRewardSpec(), t.tensor([1.]), t.tensor([1.]), zeros, zeros, zeros,
            t.zeros(1, 23), t.zeros(1, 23), t.zeros(1, 74, 3), t.tensor([1/60]), .99)
        self.assertEqual(terms[0, 0].item(), 0.)
        self.assertLess(terms.sum().item(), 0.)

    def test_privileged_features_are_finite_and_fixed_scaling_has_no_fit(self):
        from vskill_os.adapters.isaac.learning.observations import teacher_observation, observation_manifest
        t = self.t
        q, force = t.zeros(16, 23), t.zeros(16, 3)
        result = teacher_observation(q, q, q, q, force, force, t.zeros(16), force,
            t.zeros(16, dtype=t.bool), t.zeros(16), t.zeros(16), self.control)
        self.assertEqual(result.shape, (16, 102))
        self.assertTrue(t.isfinite(result).all())
        self.assertFalse(observation_manifest(self.control)['text_buffer'])
        self.assertIn('no fitted statistics', observation_manifest(self.control)['normalization'])

    def test_candidate_loader_rejects_changed_privileges_head_provenance_and_weights(self):
        from vskill_os.adapters.isaac.learning.observations import observation_manifest
        from vskill_os.adapters.learning_torch.ppo import ActorCritic
        from vskill_os.adapters.learning_torch.checkpoint import load_candidate
        t = self.t
        model = ActorCritic(102, 1)
        payload = dict(version='vf-ppo-checkpoint/1', policy_kind='diagnostic_candidate',
            config=PPOConfig().to_dict(), action_schema=self.schema.to_dict(),
            observations=observation_manifest(self.control), active_indices=[12], seed=7,
            training_transitions=0, training_demonstrations=False, ik_initialization=False,
            model=model.state_dict())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'candidate.pt'
            t.save(payload, path)
            candidate, _, _, _ = load_candidate(path, expected_schema=self.schema)
            obs = t.zeros(1, 102)
            self.assertTrue(t.equal(model.act(obs, deterministic=True)[0], candidate.act(obs, deterministic=True)[0]))
            for mutation in ('privilege', 'head', 'demonstration', 'ik', 'nonfinite'):
                changed = copy.deepcopy(payload)
                if mutation == 'privilege':
                    changed['observations']['text_buffer'] = True
                elif mutation == 'head':
                    changed['active_indices'] = [13]
                elif mutation == 'demonstration':
                    changed['training_demonstrations'] = True
                elif mutation == 'ik':
                    changed['ik_initialization'] = True
                else:
                    changed['model']['log_std'].fill_(math.nan)
                t.save(changed, path)
                with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                    load_candidate(path, expected_schema=self.schema)

    def test_batch_timeout_preserves_terminal_observation_before_local_reset(self):
        from vskill_os.adapters.isaac.learning.ppo_env import SingleKeyTrainingEnv
        from vskill_os.adapters.isaac.learning.rewards import PressProgress
        from vskill_os.adapters.isaac.learning.training_safety import TensorActionGuard, SAFETY_CODES
        t = self.t

        class ProbeEnv(SingleKeyTrainingEnv):
            """Synthetic clock probe; no substitute for the physics verifier."""
            def raw(self):
                return dict(forces=t.zeros(2, 2, 3), key_q=t.zeros(2, 2))

            def observation(self, raw=None):
                return self.progress.elapsed[:, None].clone()

            def _potential(self, raw):
                return t.zeros(2)

        env = ProbeEnv.__new__(ProbeEnv)
        reset_calls = []
        def reset_rows(rows):
            reset_calls.append(rows)
            env.scene.targets[rows] = 0.
        env.scene = SimpleNamespace(targets=t.zeros(2, 23), ids=list(range(23)),
            config=SimpleNamespace(decimation=4, physics_dt_s=1/240),
            advance=lambda: None, reset_rows=reset_rows)
        env.schema, env.config, env.reward_spec = self.schema, PPOConfig(), PressRewardSpec()
        env.count, env.device, env.active_indices = 2, 'cpu', [12]
        env.guard = TensorActionGuard(self.schema, 'cpu')
        env.safety = SimpleNamespace(check=lambda raw: t.zeros(2, len(SAFETY_CODES), dtype=t.bool))
        env.progress = PressProgress(2, 2, 0, 'cpu')
        env.progress.ticks[0] = 719
        env.progress.elapsed[0] = 719/240
        env.previous_action = t.zeros(2, 23)
        env.returns = t.zeros(2)
        env.generations = t.zeros(2, dtype=t.long)
        env.physics_steps = env.reset_count = 0
        env.fault_counts = t.zeros(len(SAFETY_CODES), dtype=t.long)
        result = env.step(t.ones(2, 1)*.5)
        self.assertEqual(reset_calls, [[0]])
        self.assertEqual(result['truncated'].tolist(), [True, False])
        self.assertFalse(result['terminated'].any())
        self.assertAlmostEqual(result['terminal_observation'][0, 0].item(), 3., places=6)
        self.assertAlmostEqual(result['observation'][0, 0].item(), 3/240, places=6)
        self.assertAlmostEqual(result['observation'][1, 0].item(), 4/240, places=6)
        self.assertEqual(env.generations.tolist(), [1, 0])
        self.assertEqual(result['episodes'].shape, (1, 6))


if __name__ == '__main__':
    unittest.main()
