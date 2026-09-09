from __future__ import annotations

import unittest

from vskill_os.control import (
    ControlPhase,
    PhaseController,
    RootVelocityCommand,
    SeatedLocomotionController,
    SeatedLocomotionObservation,
    SeatedLocomotionSafetyError,
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

    def test_seated_locomotion_must_return_through_stabilization(self) -> None:
        phases = PhaseController(initial=ControlPhase.SEATED_STABILIZATION)
        phases.transition(ControlPhase.SEATED_LOCOMOTION, reason="start chair motion")
        with self.assertRaises(TransitionError):
            phases.transition(ControlPhase.TYPING, reason="unsafe direct handoff")
        phases.transition(ControlPhase.SEATED_STABILIZATION, reason="stop and settle")
        phases.transition(ControlPhase.UPPER_BODY_OPERATION, reason="pose stable")
        self.assertEqual(phases.phase, ControlPhase.UPPER_BODY_OPERATION)

    def test_root_velocity_command_is_limited_before_policy_execution(self) -> None:
        requested = RootVelocityCommand(1.5, -1.1, 2.0)
        issued = requested.bounded(max_linear_mps=0.8, max_yaw_rate_rps=1.2)
        self.assertEqual(issued, RootVelocityCommand(0.8, -0.8, 1.2))

    def test_root_velocity_command_rejects_non_finite_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "finite"):
            RootVelocityCommand(float("nan"), 0.0, 0.0)

    def test_seated_locomotion_limits_command_and_requires_seat_contact(self) -> None:
        controller = SeatedLocomotionController()
        unsafe = self._seated_observation(seat_contact_confidence=0.5)
        with self.assertRaises(SeatedLocomotionSafetyError) as raised:
            controller.begin(RootVelocityCommand(0.8, -0.7, 0.9), unsafe)
        self.assertEqual(raised.exception.failure_code, "SEAT_CONTACT_LOST")

        issued = controller.begin(
            RootVelocityCommand(0.8, -0.7, 0.9),
            self._seated_observation(),
        )
        self.assertEqual(issued, RootVelocityCommand(0.5, -0.5, 0.5))

    def test_seated_locomotion_metrics_include_progress_and_settling(self) -> None:
        controller = SeatedLocomotionController()
        command = RootVelocityCommand(0.2, 0.0, 0.0)
        controller.begin(command, self._seated_observation())
        for _ in range(4):
            controller.observe(
                self._seated_observation(vx_mps=0.19, sample_duration_s=0.25)
            )
        metrics = controller.finish(self._seated_observation(vx_mps=0.01))

        self.assertAlmostEqual(metrics.duration_s, 1.0)
        self.assertAlmostEqual(metrics.path_length_m, 0.19)
        self.assertLess(metrics.tracking_rmse_vx_mps, 0.02)
        self.assertTrue(metrics.settled)
        self.assertFalse(controller.active)

    @staticmethod
    def _seated_observation(
        *,
        vx_mps: float = 0.0,
        seat_contact_confidence: float = 0.96,
        sample_duration_s: float = 0.02,
    ) -> SeatedLocomotionObservation:
        return SeatedLocomotionObservation(
            measured_vx_mps=vx_mps,
            measured_vy_mps=0.0,
            measured_yaw_rate_rps=0.0,
            seat_contact_confidence=seat_contact_confidence,
            continuous_contact_loss_s=0.0,
            pelvis_seat_offset_m=0.018,
            torso_tilt_rad=0.04,
            chair_tilt_rad=0.03,
            left_foot_contact=True,
            right_foot_contact=True,
            sample_duration_s=sample_duration_s,
        )

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
