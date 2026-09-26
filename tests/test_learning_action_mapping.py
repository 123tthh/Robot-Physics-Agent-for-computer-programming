"""Synthetic CPU contract tests, explicitly not physical achievement evidence."""

from dataclasses import replace
import math
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from vskill_os.adapters.isaac.learning.joint_action import JointActionMapper, JointActionSession
from vskill_os.adapters.isaac.learning.action_runner import verify_run
from vskill_os.adapters.isaac.learning.config import SingleHandConfig
from vskill_os.learning.action_evaluation import check_mapping_trace
from vskill_os.learning.contracts import JointRecord, RecordStamp
from vskill_os.learning.environment import (
    ActionRejected, EpisodeMetadata, JointControlSpec, JointTargetGuard, PolicyObservation, ResetSpec, StepResult,
)
from vskill_os.learning.physical import AppliedAction, CoordinateLimit, PhysicsSample, PhysicsTrace
from vskill_os.learning.policy_manifest import (
    ActionScale, ActionSchema, ControlClaim, MappingTrace, NormalizedJointAction, dex5_action_names,
)


NAMES = dex5_action_names("left")
ACTIVE = NAMES.index("left_hand_index_1_joint")


def schema(dt=1/60, *, active=None, scale=None):
    spec = JointControlSpec(NAMES, (-1.,)*23, (1.,)*23, (.3,)*23, active or (NAMES[ACTIVE],), dt)
    return ActionSchema.from_control(spec, scale or ActionScale())


def action(observation, lease, seq=0, value=1.0, **changes):
    return replace(NormalizedJointAction(NAMES, (value,)*23, 1/60, observation.time_s+1/60,
                                        lease.owner_token, seq), **changes)


class FakeBackend:
    """Exercises feedback, rejection, and partial-step safety without Isaac."""
    def __init__(self, config):
        self.config = config
        self.reset_calls = 0
        self.fault = None
        self.initial = (0.0,)*23

    def reset(self, spec):
        self.reset_calls += 1
        self.t, self.sequence = 0., 0
        self.owner = f"synthetic-backend-{self.reset_calls}"
        self.ep = spec.episode_id
        self.q = self.initial
        self.actions, self.samples = [], []
        self.capture()
        return self.observe(), EpisodeMetadata("synthetic", self.ep, spec.seed, self.owner,
            self.config.control, "synthetic-test", self.config.support_mode)

    def observe(self):
        return PolicyObservation("synthetic", self.ep, self.t, "a",
            tuple(JointRecord(n, q, 0., q) for n, q in zip(NAMES, self.q, strict=True)), (), (0.,)*23)

    def capture(self):
        stamp = RecordStamp(f"synthetic-{len(self.samples)}", "synthetic", self.ep, len(self.samples),
                            "synthetic_seconds", self.t, self.t, "synthetic_test_only")
        self.samples.append(PhysicsSample(stamp, self.q, (0.,)*23, self.q, (), (), (), (), (),
                                          (0., 0., .625), (1., 0., 0., 0.)))

    def step(self, a):
        if self.fault == "raise":
            raise RuntimeError("synthetic backend crash")
        applied = JointTargetGuard(self.config.control).apply(self.q, a, time_s=self.t,
            owner_token=self.owner, expected_sequence=self.sequence)
        self.actions.append(AppliedAction(self.t, a, self.q, applied, None))
        self.q = tuple(struct.unpack('f', struct.pack('f', v))[0] for v in applied)
        self.t += self.config.control.control_dt_s / (4 if self.fault in ("safety", "truncated") else 1)
        self.sequence += 1
        self.capture()
        observation = self.observe()
        if self.fault == "feedback":
            observation = replace(observation, joints=tuple(replace(j, commanded_target_rad=.1) for j in observation.joints))
        return StepResult(observation, (), self.fault == "safety", self.fault == "truncated",
                          ("EXCESS_FORCE",) if self.fault == "safety" else (), ())

    def finish(self):
        pass

    def trace(self):
        return PhysicsTrace("vf-physical/1", "isaac_physx_single_hand_v1", self.config.control.control_dt_s,
            self.config.control, self.owner, tuple(CoordinateLimit(n, "rad", -1., 1., 8.) for n in NAMES), (),
            self.initial, (0., 0., .625), 12., .03, tuple(self.samples), tuple(self.actions), (), "0"*64)


class ActionSchemaTests(unittest.TestCase):
    def test_named23_order_mimic_exclusion_and_roundtrip(self):
        self.assertEqual(len(NAMES), 23)
        self.assertEqual(len(set(NAMES)), 23)
        self.assertFalse(any(n.endswith("_3_joint") and "thumb" not in n for n in NAMES))
        self.assertEqual(ActionSchema.from_json(schema().to_json()), schema())
        with self.assertRaises(ValueError):
            replace(schema(), control=replace(schema().control, joint_names=tuple(reversed(NAMES))))
        self.assertEqual(len(schema(active=NAMES).mask.active), 23)
        self.assertEqual(len(dex5_action_names("right")), 23)

    def test_invalid_scale_and_nonfinite_input(self):
        for change in ({"reference_dt_s": 0.}, {"delta_rad": (1.,)*22}, {"delta_rad": (math.inf,)*23},
                       {"initial_tracking_tolerance_rad": .1}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                replace(ActionScale(), **change)
        for v in (math.nan, math.inf, True):
            with self.subTest(v=v), self.assertRaises(ValueError):
                NormalizedJointAction(NAMES, (v,)*23, .1, .1, "owner", 0)

    def test_ownership_conflict_rejected_before_reset(self):
        config = schema()
        backend = FakeBackend(config)
        session = JointActionSession(backend, config)
        for claims in ((ControlClaim("joint_policy", config.mask.active), ControlClaim("frozen_body_pd", NAMES)),
                       (ControlClaim("joint_policy", config.mask.active),),
                       (ControlClaim("joint_policy", NAMES), ControlClaim("frozen_body_pd", ()))):
            with self.subTest(claims=claims), self.assertRaisesRegex(ActionRejected, "CONTROL_OWNERSHIP"):
                session.reset(ResetSpec("test"), claims)
        self.assertEqual(backend.reset_calls, 0)


class MapperTests(unittest.TestCase):
    def setUp(self):
        self.config = schema()
        self.backend = FakeBackend(self.config)
        self.session = JointActionSession(self.backend, self.config)
        self.port, self.obs, self.lease = self.session.reset(ResetSpec("test"), self.config.claims())

    def test_mask_and_rate_limit_rebase_every_step_on_actual_float32_target(self):
        for seq in range(20):
            before = self.obs
            self.obs = self.port.step(action(self.obs, self.lease, seq)).observation
            for i, joint in enumerate(self.obs.joints):
                if i != ACTIVE:
                    self.assertEqual(joint.commanded_target_rad, 0.)
            self.assertAlmostEqual(self.obs.joints[ACTIVE].commanded_target_rad - before.joints[ACTIVE].commanded_target_rad,
                                   .3/60, places=7)
            event = self.session.events[-1]
            self.assertEqual(event.proposed_targets_rad[ACTIVE], before.joints[ACTIVE].commanded_target_rad + .01)
        self.assertFalse(hasattr(self.port, "resume"))
        self.assertFalse(hasattr(self.port, "schema"))
        self.assertFalse(hasattr(self.port, "backend"))

    def test_extreme_position_clipping_and_immediate_reverse_no_windup(self):
        mapper = JointActionMapper(self.config)
        for boundary, sign in ((1., 1.), (-1., -1.)):
            joints = tuple(replace(j, commanded_target_rad=boundary if i == ACTIVE else 0.,
                                   position_rad=boundary if i == ACTIVE else 0.) for i, j in enumerate(self.obs.joints))
            obs = replace(self.obs, joints=joints)
            for value, expected in ((sign, boundary), (-sign, boundary-sign*.3/60)):
                result = mapper.map(obs, action(obs, self.lease, value=value), lease_token=self.lease.owner_token,
                    sequence=0, backend_token="private", backend_sequence=0)
                self.assertAlmostEqual(result.filtered_targets_rad[ACTIVE], expected)

    def test_all23_columns_and_per_joint_scaling(self):
        config = schema(active=NAMES, scale=replace(ActionScale(), delta_rad=tuple(.0001*(i+1) for i in range(23))))
        mapper = JointActionMapper(config)
        for i in range(23):
            values = tuple(1. if j == i else 0. for j in range(23))
            result = mapper.map(self.obs, action(self.obs, self.lease, values=values), lease_token=self.lease.owner_token,
                sequence=0, backend_token="private", backend_sequence=0)
            self.assertEqual(result.filtered_targets_rad, tuple(config.scale.delta_rad[i] if j == i else 0. for j in range(23)))

    def test_dt_scales_both_proposal_and_independent_rate_limit(self):
        for dt in (1/30, 1/60, 1/120):
            config = schema(dt)
            mapped = JointActionMapper(config).map(self.obs, action(self.obs, self.lease, duration_s=dt, valid_until_s=dt),
                lease_token=self.lease.owner_token, sequence=0, backend_token="private", backend_sequence=0)
            self.assertAlmostEqual(mapped.proposed_targets_rad[ACTIVE]/dt, .6)
            self.assertAlmostEqual(mapped.filtered_targets_rad[ACTIVE]/dt, .3)

    def test_invalid_inputs_do_not_advance_or_consume_sequence(self):
        valid = action(self.obs, self.lease)
        for change, code in (({"owner_token": "old"}, "CONTROL_OWNERSHIP"),
            ({"joint_names": tuple(reversed(NAMES))}, "JOINT_MAPPING"), ({"sequence": 1}, "ACTION_SEQUENCE"),
            ({"values": (1.001,)*23}, "ACTION_RANGE"), ({"duration_s": 1/30}, "ACTION_EXPIRED"),
            ({"valid_until_s": 0.}, "ACTION_EXPIRED")):
            with self.subTest(code=code), self.assertRaisesRegex(ActionRejected, code):
                self.port.step(replace(valid, **change))
            self.assertEqual(self.backend.sequence, 0)
            self.assertEqual(self.backend.t, 0.)
            self.assertEqual(self.session.sequence, 0)
        self.port.step(valid)
        with self.assertRaisesRegex(ActionRejected, "ACTION_SEQUENCE"):
            self.port.step(valid)

    def test_pause_preserves_target_and_resume_rotates_lease_without_jump(self):
        self.port.step(action(self.obs, self.lease))
        targets = self.backend.q
        self.session.pause()
        with self.assertRaisesRegex(ActionRejected, "CONTROL_STOPPED"):
            self.port.step(action(self.obs, self.lease))
        with self.assertRaisesRegex(ActionRejected, "STALE_OBSERVATION"):
            self.session.resume(self.config.claims())
        self.session.hold_step()
        new_port, new_obs, new_lease = self.session.resume(self.config.claims())
        self.assertNotEqual(new_lease.owner_token, self.lease.owner_token)
        self.assertEqual(self.backend.q, targets)
        with self.assertRaisesRegex(ActionRejected, "CONTROL_OWNERSHIP"):
            self.port.step(action(new_obs, new_lease))  # even an old port holding the NEW token is stale
        with self.assertRaisesRegex(ActionRejected, "CONTROL_OWNERSHIP"):
            new_port.step(action(new_obs, self.lease))
        new_port.step(action(new_obs, new_lease, value=-1.))
        self.assertAlmostEqual(self.backend.q[ACTIVE], 0., places=7)

    def test_independent_safety_during_hold_latches_until_reset(self):
        self.session.pause()
        self.backend.fault = "safety"
        step = self.session.hold_step()
        self.assertTrue(step.terminated)
        self.assertEqual(step.failure_codes, ("EXCESS_FORCE",))
        self.assertEqual(self.session.state, "latched")
        for operation in (lambda: self.session.resume(self.config.claims()), self.session.hold_step,
                          lambda: self.port.step(action(self.obs, self.lease)), self.session.finish):
            with self.assertRaisesRegex(ActionRejected, "RESET_REQUIRED"):
                operation()
        self.backend.fault = None
        port, obs, lease = self.session.reset(ResetSpec("after_safety"), self.config.claims())
        self.assertNotEqual(lease.owner_token, self.lease.owner_token)
        port.step(action(obs, lease))

    def test_backend_failure_or_bad_feedback_requires_reset(self):
        for fault in ("raise", "feedback"):
            with self.subTest(fault=fault):
                self.setUp()
                self.backend.fault = fault
                with self.assertRaises((RuntimeError, ActionRejected)):
                    self.port.step(action(self.obs, self.lease))
                self.assertEqual(self.session.state, "latched")
                with self.assertRaisesRegex(ActionRejected, "RESET_REQUIRED"):
                    self.session.resume(self.config.claims())

    def test_reset_checks_measured_tracking_and_metadata_before_granting_a_port(self):
        for fault in ('tracking', 'schema'):
            self.setUp()
            self.session.finish()
            backend_reset = self.backend.reset
            def bad_reset(spec):
                obs, meta = backend_reset(spec)
                if fault == 'tracking':
                    obs = replace(obs, joints=tuple(replace(j, position_rad=.06) for j in obs.joints))
                else:
                    meta = replace(meta, support_mode='unsupported')
                return obs, meta
            with patch.object(self.backend, 'reset', side_effect=bad_reset):
                with self.assertRaisesRegex(ActionRejected, 'UNSAFE_INITIAL_TRACKING|BACKEND_SCHEMA'):
                    self.session.reset(ResetSpec('bad_reset'), self.config.claims())
            self.assertEqual(self.session.state, 'latched')
            self.assertEqual(self.backend.sequence, 0)

    def test_resume_ownership_conflict_keeps_session_paused(self):
        self.session.pause()
        self.session.hold_step()
        before = self.backend.observe()
        with self.assertRaisesRegex(ActionRejected, 'CONTROL_OWNERSHIP'):
            self.session.resume((ControlClaim('joint_policy', NAMES), ControlClaim('frozen_body_pd', NAMES)))
        self.assertEqual(self.session.state, 'paused')
        self.assertEqual(before, self.backend.observe())

    def test_time_limit_is_truncated_terminal_and_old_ports_fail_after_reset(self):
        self.backend.fault = "truncated"
        step = self.port.step(action(self.obs, self.lease))
        self.assertTrue(step.truncated)
        self.assertFalse(step.terminated)
        self.backend.fault = None
        _, obs, lease = self.session.reset(ResetSpec("next"), self.config.claims())
        with self.assertRaisesRegex(ActionRejected, "CONTROL_OWNERSHIP"):
            self.port.step(action(obs, lease))


class MappingEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.config = schema()
        backend = FakeBackend(self.config)
        session = JointActionSession(backend, self.config)
        port, obs, lease = session.reset(ResetSpec("replay"), self.config.claims())
        obs = port.step(action(obs, lease)).observation
        session.pause()
        try: port.step(action(obs, lease, 1))
        except ActionRejected: pass
        session.hold_step()
        port, obs, lease = session.resume(self.config.claims())
        port.step(action(obs, lease, value=-1.))
        session.finish()
        self.physical = backend.trace()
        self.mapping = session.trace(self.physical.episode_sha256)

    def check(self, mapping=None, physical=None):
        return check_mapping_trace(mapping or self.mapping, physical or self.physical, self.config)

    def test_roundtrip_replays_against_raw_data(self):
        restored = MappingTrace.from_json(self.mapping.to_json())
        result = self.check(restored)
        self.assertTrue(result['mapping_passed'], result)
        self.assertEqual((result['policy_steps'], result['hold_steps'], result['rejected_inputs']), (2, 1, 1))
        self.assertEqual(result['masked_nonzero_values'], 44)

    def test_tampered_proposal_target_identity_order_and_coverage_rejected(self):
        e = self.mapping.events[1]
        for change in ({"proposed_targets_rad": (0.,)*23}, {"filtered_targets_rad": (.1,)*23},
                       {"backend_action_index": 1}, {"policy_sequence": 4}, {"lease_token": "fabricated"},
                       {"after_time_s": 0.}):
            with self.subTest(change=change):
                changed = replace(self.mapping, events=(self.mapping.events[0], replace(e, **change), *self.mapping.events[2:]))
                self.assertFalse(self.check(changed)['mapping_passed'])
        for changed in (replace(self.mapping, events=self.mapping.events[:-1]),
                        replace(self.mapping, episode_id="different"),
                        replace(self.mapping, physical_episode_sha256="1"*64)):
            self.assertFalse(self.check(changed)['mapping_passed'])

    def test_unlogged_low_level_action_and_faked_rejection_are_rejected(self):
        bad = replace(self.physical, actions=(*self.physical.actions, self.physical.actions[-1]))
        self.assertFalse(self.check(physical=bad)['mapping_passed'])
        events = list(self.mapping.events)
        i = next(i for i, e in enumerate(events) if e.kind == "rejected")
        events[i] = replace(events[i], failure_codes=("ACTION_RANGE",))
        self.assertFalse(self.check(replace(self.mapping, events=tuple(events)))['mapping_passed'])

    def test_joint_feedback_and_control_schema_tampering_cannot_pass_replay(self):
        events = list(self.mapping.events)
        events[1] = replace(events[1], joints=tuple(replace(j, commanded_target_rad=.2) for j in events[1].joints))
        self.assertFalse(self.check(replace(self.mapping, events=tuple(events)))['mapping_passed'])
        self.assertFalse(self.check(replace(self.mapping, action_schema=schema(1/30)))['mapping_passed'])
        self.assertFalse(self.check(replace(self.mapping, control_claims=(ControlClaim('joint_policy', NAMES),)))['mapping_passed'])

    def test_worker_exit_zero_is_not_completion_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory/'d2').mkdir()
            (directory/'d2/process.json').write_text('{"returncode": 0, "timed_out": false, "interrupted": false}')
            result = verify_run(directory, SingleHandConfig(), ActionScale())
            self.assertFalse(result['accepted'])
            (directory/'d2/failure.json').write_text('{"error":"synthetic failure"}')
            result = verify_run(directory, SingleHandConfig(), ActionScale())
            self.assertFalse(result['accepted'])
            self.assertIn('worker failed', result['findings'][0])


if __name__ == "__main__":
    unittest.main()
