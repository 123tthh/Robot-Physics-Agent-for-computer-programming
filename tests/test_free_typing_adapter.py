"""Regression for the fixed-base to floating-base actuator interface change."""
import unittest

from vskill_os.adapters.isaac.free_typing import joint_gravity_for_free_base


class FloatingGravityInterfaceTests(unittest.TestCase):
    def test_root_support_force_cannot_be_misrouted_to_actuators(self):
        # The floating body's support wrench is deliberately much larger than
        # any joint torque; a fixed-base interpretation incorrectly actuates it.
        joint_torques=[.1*i for i in range(69)]
        gravity=[11.,22.,350.,4.,5.,6.]+joint_torques
        projected=joint_gravity_for_free_base(gravity,69)
        self.assertEqual(projected,joint_torques)
        self.assertEqual(len(projected),69)
        self.assertNotIn(350.,projected)

    def test_fixed_base_or_truncated_data_cannot_silently_shift_joint_order(self):
        for size in (0,68,69,74,76):
            with self.subTest(size=size),self.assertRaises(ValueError):
                joint_gravity_for_free_base([0.]*size,69)
