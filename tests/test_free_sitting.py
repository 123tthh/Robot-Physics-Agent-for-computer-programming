from dataclasses import replace
import math
import unittest

from vskill_os.simulation.free_sitting import SittingSample, SITTING_CONTRACT, sitting_guard, verify_free_sitting


def evidence():
    rows=[]
    for i in range(401):
        t=.005+.02*i
        seated=t>=4.
        contact=({'body':'pelvis','position_m':[.10,0.,.4775],
                  'force_n':[0.,0.,120.],'on_seat':True},) if seated else ()
        rows.append(SittingSample(t,'settling' if seated else 'standing',
            (.10,0.,.59 if seated else .78),.01,.1,(0.,0.,0.),.005,.01,
            120. if seated else 0.,110.,110.,220.,.5,.005,contact))
    return rows


class FreeSittingTests(unittest.TestCase):
    def verify(self, rows):
        return verify_free_sitting(rows,robot_mass_kg=35.4)

    def test_complete_supported_transition_passes(self):
        result=self.verify(evidence())
        self.assertTrue(result['free_balance_sitting_verified'])
        self.assertGreaterEqual(result['stable_window_s'],1.98)

    def test_hover_is_not_sitting(self):
        rows=[replace(s,seat_force_n=0.,seat_contacts=()) for s in evidence()]
        self.assertEqual(self.verify(rows)['failure'],'INSUFFICIENT_SEAT_SUPPORT')

    def test_lower_chair_requires_contacts_at_its_actual_height(self):
        rows=[replace(s,seat_top_local_m=.44,seat_contacts=tuple(
            {**c,'position_m':[c['position_m'][0],c['position_m'][1],.44]} for c in s.seat_contacts)) for s in evidence()]
        self.assertTrue(self.verify(rows)['succeeded'])
        rows[-1]=replace(rows[-1],seat_top_local_m=.4775)
        self.assertEqual(self.verify(rows)['failure'],'CONTACT_EVIDENCE_MISMATCH')

    def test_initial_seated_reset_cannot_replace_transition(self):
        rows=[replace(evidence()[-1],time_s=s.time_s) for s in evidence()]
        self.assertEqual(self.verify(rows)['failure'],'STANDING_PRECONDITION')

    def test_controller_contact_flag_is_not_evidence(self):
        rows=evidence()
        rows[-1]=replace(rows[-1],seat_contacts=())
        self.assertEqual(self.verify(rows)['failure'],'CONTACT_EVIDENCE_MISMATCH')
        rows=evidence()
        # A controller's false flag cannot override raw measured seat support.
        rows[-1]=replace(rows[-1],seat_contacts=({**rows[-1].seat_contacts[0],'on_seat':False},))
        self.assertTrue(self.verify(rows)['succeeded'])

    def test_front_edge_and_backrest_contact_do_not_count_as_seat_load(self):
        for position in ((.25,0.,.4),(0.,0.,.8)):
            rows=evidence()
            rows[-1]=replace(rows[-1],seat_contacts=({**rows[-1].seat_contacts[0],'position_m':position},))
            self.assertEqual(self.verify(rows)['failure'],'CONTACT_EVIDENCE_MISMATCH')

    def test_support_drift_slip_and_edge_gates(self):
        for change,reason in (
            ({'robot_fixed':True},'EXTERNAL_SUPPORT'),
            ({'chair_fixed':True},'EXTERNAL_SUPPORT'),
            ({'external_support':True},'EXTERNAL_SUPPORT'),
            ({'left_foot_force_n':0.},'FOOT_CONTACT_LOST'),
            ({'chair_speed_mps':.1},'NOT_SETTLED'),
            ({'foot_slip_mps':.1},'NOT_SETTLED'),
            ({'pelvis_xyz_m':(.22,0.,.59)},'SEAT_EDGE'),
            ({'unexpected_contact_force_n':8.},'UNPLANNED_CONTACT'),
        ):
            rows=evidence();rows[-1]=replace(rows[-1],**change)
            with self.subTest(change=change):self.assertEqual(self.verify(rows)['failure'],reason)

    def test_independent_safety_rejects_impact_tilt_and_actuator_saturation_violation(self):
        for change,reason in (
            ({'maximum_contact_force_n':1501.},'EXCESS_CONTACT_FORCE'),
            ({'torso_tilt_rad':.61},'UNSAFE_TILT'),
            ({'maximum_joint_effort_ratio':1.1},'EFFORT_LIMIT'),
            ({'unexpected_contact_force_n':21.},'UNPLANNED_CONTACT'),
        ):
            self.assertEqual(sitting_guard(replace(evidence()[0],**change)),reason)

    def test_invalid_nested_evidence_and_configuration(self):
        for change in (
            {'pelvis_xyz_m':(math.nan,0.,.59)}, {'chair_quat_wxyz':(0.,0.,0.,0.)},
            {'seat_contacts':({'body':'pelvis','force_n':[0,0,math.nan],'position_m':[0,0,.4775]},)},
            {'left_foot_force_n':-1.}, {'robot_fixed':0},
            {'seat_top_local_m':math.nan}, {'seat_top_local_m':.1}, {'seat_top_local_m':'low'},
        ):
            with self.subTest(change=change):
                self.assertEqual(sitting_guard(replace(evidence()[-1],**change)),'INVALID_EVIDENCE')
        for mass in (math.nan,math.inf,0.,-1.):
            self.assertEqual(verify_free_sitting(evidence(),robot_mass_kg=mass)['failure'],'INVALID_EVIDENCE')
        self.assertEqual(verify_free_sitting(evidence(),robot_mass_kg=35.4,stable_window_s=0.)['failure'],'INVALID_EVIDENCE')

    def test_missing_time_and_source_are_rejected(self):
        rows=evidence();rows[5]=replace(rows[5],time_s=rows[4].time_s)
        self.assertEqual(self.verify(rows)['failure'],'SAMPLE_GAP')
        rows=evidence();rows[-1]=replace(rows[-1],source='controller_claim')
        self.assertEqual(self.verify(rows)['failure'],'UNSUPPORTED_SOURCE')
        self.assertEqual(self.verify(evidence()[10:])['failure'],'MISSING_INITIAL_STATE')

    def test_skill_contract_is_versioned_bounded_and_has_no_joint_targets(self):
        for field in ('version','preconditions','postconditions','resources','failure_codes'):
            self.assertTrue(SITTING_CONTRACT[field])
        self.assertLessEqual(SITTING_CONTRACT['maximum_duration_s'],60.)
        self.assertNotIn('joint_targets',SITTING_CONTRACT)


if __name__=='__main__':unittest.main()
