"""Composed G1 control stack used by the simulator and future adapters."""

from __future__ import annotations

from ..robot.g1 import G1HardwareProfile
from .commands import RootVelocityCommand
from .phases import ControlPhase, PhaseController
from .seated_locomotion import (
    SeatedLocomotionController,
    SeatedLocomotionMetrics,
    SeatedLocomotionObservation,
)
from .sitting import SitObservation, StagedSitController


class G1ControlStack:
    """Owns phase, risk, and sit-stage state below the Skill runtime."""

    def __init__(
        self,
        profile: G1HardwareProfile,
        *,
        max_linear_mps: float = 0.8,
        max_yaw_rate_rps: float = 1.2,
    ) -> None:
        if max_linear_mps <= 0 or max_yaw_rate_rps <= 0:
            raise ValueError("locomotion limits must be positive")
        self.profile = profile
        self.phases = PhaseController()
        self.max_linear_mps = max_linear_mps
        self.max_yaw_rate_rps = max_yaw_rate_rps
        self.sitting = StagedSitController()
        self.seated_locomotion = SeatedLocomotionController()
        self.last_locomotion: RootVelocityCommand | None = None

    @property
    def phase(self) -> ControlPhase:
        return self.phases.phase

    def issue_locomotion(
        self,
        command: RootVelocityCommand,
    ) -> RootVelocityCommand:
        if self.phase is ControlPhase.RECOVERY:
            self.phases.recover(
                target=ControlPhase.LOCOMOTION,
                reason="resume locomotion after recovery",
            )
        elif self.phase is not ControlPhase.LOCOMOTION:
            self.phases.transition(
                ControlPhase.LOCOMOTION,
                reason="activate velocity executor",
            )
        bounded = command.bounded(
            max_linear_mps=self.max_linear_mps,
            max_yaw_rate_rps=self.max_yaw_rate_rps,
        )
        self.last_locomotion = bounded
        return bounded

    def reach_station(self) -> None:
        if self.phase is ControlPhase.RECOVERY:
            self.phases.recover(
                target=ControlPhase.LOCOMOTION,
                reason="navigation failure cleared",
            )
        if self.phase is ControlPhase.STAND_READY:
            self.phases.transition(
                ControlPhase.LOCOMOTION, reason="begin workstation approach"
            )
        self.phases.transition(
            ControlPhase.STATION_ALIGNMENT,
            reason="workstation approach tolerance reached",
        )

    def begin_alignment(self) -> None:
        if self.phase is ControlPhase.RECOVERY:
            self.phases.recover(
                target=ControlPhase.STATION_ALIGNMENT,
                reason="alignment observation recovered",
            )
        elif self.phase is ControlPhase.LOCOMOTION:
            self.phases.transition(
                ControlPhase.STATION_ALIGNMENT,
                reason="switch to chair-relative alignment",
            )

    def run_nominal_sit(self) -> None:
        if self.phase is ControlPhase.RECOVERY:
            self.phases.recover(
                target=ControlPhase.STATION_ALIGNMENT,
                reason="seat alignment recovered",
            )
        self.begin_sit_transition()
        samples = (
            SitObservation(0.02, 0.04, 0.72, 0.0, True, True, 0.02, 0.05),
            SitObservation(0.02, 0.04, 0.54, 0.7, True, True, 0.10, 0.10),
            SitObservation(0.02, 0.04, 0.49, 0.9, True, True, 0.07, 0.09),
            SitObservation(0.02, 0.04, 0.48, 0.95, True, True, 0.04, 0.07),
            SitObservation(0.02, 0.04, 0.48, 0.95, True, True, 0.01, 0.03),
            SitObservation(
                0.02,
                0.04,
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
            self.sitting.update(sample)
        if not self.sitting.complete:
            self.phases.fail(reason="sit stage guards did not complete")
            raise RuntimeError("nominal sit trace failed its stage guards")
        self.phases.transition(
            ControlPhase.SEATED_STABILIZATION,
            reason="seat and bilateral foot contacts verified",
        )
        self.phases.transition(
            ControlPhase.UPPER_BODY_OPERATION,
            reason="seated work-pose stability verified",
        )

    def begin_sit_transition(self) -> None:
        if self.phase is ControlPhase.RECOVERY:
            self.phases.recover(
                target=ControlPhase.STATION_ALIGNMENT,
                reason="seat alignment recovered",
            )
        self.phases.transition(
            ControlPhase.SIT_TRANSITION,
            reason="chair pose and safety guards verified",
        )

    def begin_keyboard_calibration(self) -> None:
        if self.phase is ControlPhase.RECOVERY:
            self.phases.recover(
                target=ControlPhase.UPPER_BODY_OPERATION,
                reason="work pose restored",
            )
        if self.phase is ControlPhase.SEATED_STABILIZATION:
            self.phases.transition(
                ControlPhase.UPPER_BODY_OPERATION,
                reason="upper-body disturbance metric is within tolerance",
            )
        self.phases.transition(
            ControlPhase.KEYBOARD_CALIBRATION,
            reason="activate close-range keyboard perception",
        )

    def issue_seated_locomotion(
        self,
        command: RootVelocityCommand,
        observation: SeatedLocomotionObservation,
    ) -> RootVelocityCommand:
        if self.phase is ControlPhase.SEATED_STABILIZATION:
            self.phases.transition(
                ControlPhase.UPPER_BODY_OPERATION,
                reason="seated pose is stable before chair locomotion",
            )
        if self.phase is not ControlPhase.UPPER_BODY_OPERATION:
            raise RuntimeError(
                "seated locomotion requires a verified stable upper-body phase"
            )
        issued = self.seated_locomotion.begin(command, observation)
        self.phases.transition(
            ControlPhase.SEATED_LOCOMOTION,
            reason="activate passive-chair velocity policy",
        )
        return issued

    def observe_seated_locomotion(
        self,
        observation: SeatedLocomotionObservation,
    ) -> None:
        self.seated_locomotion.observe(observation)

    def complete_seated_locomotion(
        self,
        settled_observation: SeatedLocomotionObservation,
    ) -> SeatedLocomotionMetrics:
        metrics = self.seated_locomotion.finish(settled_observation)
        self.phases.transition(
            ControlPhase.SEATED_STABILIZATION,
            reason="seated velocity command ended; require post-motion stabilization",
        )
        self.phases.transition(
            ControlPhase.UPPER_BODY_OPERATION,
            reason="post-motion work pose returned to the stable envelope",
        )
        return metrics

    def abort_seated_locomotion(self, reason: str) -> None:
        self.seated_locomotion.abort()
        self.phases.fail(reason=reason)

    def restore_work_pose(self) -> None:
        if self.phase is ControlPhase.RECOVERY:
            self.phases.recover(
                target=ControlPhase.SEATED_STABILIZATION,
                reason="seat contact restored by local recovery",
            )
        if self.phase is ControlPhase.SIT_TRANSITION:
            self.phases.transition(
                ControlPhase.SEATED_STABILIZATION,
                reason="seat contact recovered after interrupted descent",
            )
        if self.phase is ControlPhase.SEATED_STABILIZATION:
            self.phases.transition(
                ControlPhase.UPPER_BODY_OPERATION,
                reason="work-pose stability recovered",
            )

    def begin_typing(self) -> None:
        if self.phase is ControlPhase.RECOVERY:
            self.phases.recover(
                target=ControlPhase.KEYBOARD_CALIBRATION,
                reason="keyboard pose recovered",
            )
        if self.phase is ControlPhase.UPPER_BODY_OPERATION:
            self.phases.transition(
                ControlPhase.KEYBOARD_CALIBRATION,
                reason="begin keyboard calibration before typing",
            )
        self.phases.transition(
            ControlPhase.TYPING,
            reason="keyboard pose and key reachability verified",
        )

    def fail(self, reason: str) -> None:
        self.phases.fail(reason=reason)

    def recover(self, target: ControlPhase, reason: str) -> None:
        if self.phase is ControlPhase.RECOVERY:
            self.phases.recover(target=target, reason=reason)

    def synchronize(self, facts: set[str]) -> None:
        self.phases.synchronize(facts)
        self.seated_locomotion.abort()
        if "seated_stable" not in facts:
            self.sitting.reset()

    def snapshot(self) -> dict[str, object]:
        return {
            "phase": self.phase.value,
            "g1_profile": self.profile.compatibility_key,
            "sit_stage": self.sitting.stage.value,
            "seated_locomotion_active": self.seated_locomotion.active,
            "last_seated_locomotion": (
                None
                if self.seated_locomotion.last_metrics is None
                else self.seated_locomotion.last_metrics.to_dict()
            ),
            "last_locomotion": (
                None
                if self.last_locomotion is None
                else {
                    "vx_mps": self.last_locomotion.vx_mps,
                    "vy_mps": self.last_locomotion.vy_mps,
                    "yaw_rate_rps": self.last_locomotion.yaw_rate_rps,
                }
            ),
            "transition_count": len(self.phases.history),
        }
