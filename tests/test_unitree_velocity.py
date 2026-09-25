import copy
import json
import math
from pathlib import Path
import unittest

from vskill_os.adapters.unitree_velocity import (
    DEFAULT_MANIFEST, BodyPolicyState, VelocityObservationCodec, projected_gravity,
)
from vskill_os.control.commands import RootVelocityCommand


class UnitreeVelocityTests(unittest.TestCase):
    def setUp(self):
        self.cfg = json.loads(DEFAULT_MANIFEST.read_text())['contract']
        self.codec = VelocityObservationCodec(self.cfg)

    def state(self,time=0,gyro=(1.,2.,3.),q=None):
        return BodyPolicyState(time,gyro,(0.,0.,-1.),q or self.codec.default_sdk_pose(),(0.,)*29)

    def test_reset_and_term_major_history_match_upstream_not_frame_stacking(self):
        first=self.codec.observe(self.state(),RootVelocityCommand(.1,0,0))
        self.assertEqual(len(first),480)
        for i in range(0,15,3):
            self.assertEqual(first[i:i+3],(.2,.4,.6000000000000001))
        self.assertEqual(first[15:30],(0.,0.,-1.)*5)
        second=self.codec.observe(self.state(.02,(4.,5.,6.)),RootVelocityCommand(.2,0,0))
        self.assertEqual(second[:12],first[:12])
        self.assertEqual(second[12:15],(.8,1.,1.2000000000000002))
        self.assertEqual(second[30:42],(.1,0,0)*4)
        self.assertEqual(second[42:45],(.2,0,0))

    def test_sdk_mapping_is_nonidentity_and_roundtrips_actions(self):
        values=tuple(float(i) for i in range(29))
        sdk=self.codec.to_sdk_order(values)
        self.assertEqual(sdk[6],1.)
        self.assertEqual(sdk[12],2.)
        targets=self.codec.accept_action(values)
        for i,sdk_id in enumerate(self.cfg['joint_ids_map']):
            self.assertEqual(targets[sdk_id],values[i]*.25+self.cfg['default_joint_pos'][i])

    def test_last_action_history_keeps_raw_network_values(self):
        self.codec.observe(self.state(),RootVelocityCommand())
        self.codec.accept_action((2.,)*29)
        obs=self.codec.observe(self.state(.02),RootVelocityCommand())
        self.assertEqual(obs[-29:],(2.,)*29)
        self.assertEqual(obs[-145:-29],(0.,)*116)
        self.codec.reset()
        self.assertEqual(self.codec.observe(self.state(),RootVelocityCommand())[-145:],(0.,)*145)

    def test_body_positions_are_reordered_before_subtracting_default(self):
        q=list(self.codec.default_sdk_pose()); q[6]+=.5
        obs=self.codec.observe(self.state(q=tuple(q)),RootVelocityCommand())
        self.assertEqual(obs[45+1],.5)
        self.assertEqual(sum(obs[45:190]),2.5)

    def test_rate_command_and_nonfinite_inputs_are_rejected(self):
        self.codec.observe(self.state(),RootVelocityCommand())
        for timestamp in (0,.01,.04):
            with self.assertRaises(ValueError):self.codec.observe(self.state(timestamp),RootVelocityCommand())
        self.codec.reset()
        with self.assertRaises(ValueError):self.codec.observe(self.state(),RootVelocityCommand(0,0,.21))
        with self.assertRaises(ValueError):self.codec.observe(self.state(gyro=(math.nan,0,0)),RootVelocityCommand())
        with self.assertRaises(ValueError):self.codec.accept_action((math.inf,)*29)
        with self.assertRaises(ValueError):self.codec.accept_action((0.,)*43)

    def test_wrong_contracts_fail_before_inference(self):
        for mutate in (lambda c:c['joint_ids_map'].__setitem__(0,6),
                       lambda c:c.__setitem__('step_dt',1/120),
                       lambda c:c['observations']['last_action'].__setitem__('history_length',1),
                       lambda c:c['actions']['JointPositionAction'].__setitem__('joint_ids',list(range(12)))):
            cfg=copy.deepcopy(self.cfg);mutate(cfg)
            with self.assertRaises(ValueError):VelocityObservationCodec(cfg)

    def test_gravity_uses_inverse_base_rotation(self):
        self.assertEqual(projected_gravity((1,0,0,0)),(0,0,-1))
        g=projected_gravity((math.sqrt(.5),0,math.sqrt(.5),0))
        self.assertAlmostEqual(g[0],1.)
        self.assertAlmostEqual(g[2],0.)
        with self.assertRaises(ValueError):projected_gravity((2,0,0,0))

    def test_manifest_honestly_marks_donor_not_deployment(self):
        candidate=json.loads(DEFAULT_MANIFEST.read_text())
        self.assertFalse(candidate['physical_walking_verified'])
        self.assertFalse(candidate['target_mode15_dex3_compatible'])
        self.assertEqual(candidate['dex3_hand_dof_in_policy'],0)


if __name__ == '__main__':unittest.main()
