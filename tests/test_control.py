from __future__ import annotations

import unittest

from vskill_os.control import (
    ControlPhase,
    PhaseController,
    RootVelocityCommand,
    SitObservation,
    SitStage,
    StagedSitController,
    TransitionError,
)


class ControlTests(unittest.TestCase):
    def test_phase_machine_rejects_skipping_contact_transition(self) -> None:
        phases = PhaseController()
        with self.assertRaises(TransitionError):
            phases.transition(ControlPhase.TYPING, reason="unsafe shortcut")

    def test_recovery_is_explicit_and_returns_to_requested_phase(self) -> None:
        phases = PhaseController()
        phases.transition(ControlPhase.LOCOMOTION, reason="start")
        phases.fail(reason="obstacle")
        self.assertEqual(phases.phase, ControlPhase.RECOVERY)
        phases.recover(target=ControlPhase.LOCOMOTION, reason="path clear")
        self.assertEqual(phases.phase, ControlPhase.LOCOMOTION)

    def test_root_velocity_command_is_limited_before_policy_execution(self) -> None:
        requested = RootVelocityCommand(1.5, -1.1, 2.0)
        issued = requested.bounded(max_linear_mps=0.8, max_yaw_rate_rps=1.2)
        self.assertEqual(issued, RootVelocityCommand(0.8, -0.8, 1.2))

    def test_sit_stages_require_contact_and_work_readiness(self) -> None:
        controller = StagedSitController(target_seat_height_m=0.48)
        unsafe = SitObservation(0.01, 0.01, 0.5, 0.0, True, True, 0.0, 0.0)
        controller.update(unsafe)
        self.assertEqual(controller.stage, SitStage.CONTROLLED_DESCENT)
        controller.update(unsafe)
        self.assertEqual(controller.stage, SitStage.CONTROLLED_DESCENT)

        samples = (
            SitObservation(0.01, 0.01, 0.53, 0.7, True, True, 0.1, 0.1),
            SitObservation(0.01, 0.01, 0.49, 0.9, True, True, 0.07, 0.09),
            SitObservation(0.01, 0.01, 0.48, 0.95, True, True, 0.04, 0.07),
            SitObservation(0.01, 0.01, 0.48, 0.95, True, True, 0.01, 0.03),
            SitObservation(
                0.01,
                0.01,
                0.48,
                0.95,
                True,
                True,
                0.01,
                0.03,
                screen_visible=True,
                hands_reachable=True,
            ),
        )
        for sample in samples:
            controller.update(sample)
        self.assertTrue(controller.complete)


if __name__ == "__main__":
    unittest.main()
