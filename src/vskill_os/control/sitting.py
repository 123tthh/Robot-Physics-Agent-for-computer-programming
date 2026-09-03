"""Task-constrained staged sit controller independent of any RL backend."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class SitStage(StrEnum):
    REVERSE_ALIGNMENT = "reverse_alignment"
    CONTROLLED_DESCENT = "controlled_descent"
    CONTACT_CONFIRMATION = "contact_confirmation"
    LOAD_TRANSFER = "load_transfer"
    STABILIZATION = "stabilization"
    WORK_POSE = "work_pose"
    COMPLETE = "complete"


@dataclass(frozen=True, slots=True)
class SitObservation:
    chair_position_error_m: float
    chair_yaw_error_rad: float
    pelvis_height_m: float
    seat_contact_confidence: float
    left_foot_contact: bool
    right_foot_contact: bool
    pelvis_speed_mps: float
    torso_angular_speed_rps: float
    screen_visible: bool = False
    hands_reachable: bool = False

    def __post_init__(self) -> None:
        if not 0.0 <= self.seat_contact_confidence <= 1.0:
            raise ValueError("seat_contact_confidence must be within [0, 1]")
        if self.pelvis_height_m <= 0:
            raise ValueError("pelvis height must be positive")


@dataclass(frozen=True, slots=True)
class SitObjective:
    stage: SitStage
    controller_slot: str
    target_pelvis_height_m: float | None
    require_seat_contact: bool
    description: str


class StagedSitController:
    """Guarded stage manager; learned controllers fill each controller slot."""

    def __init__(self, *, target_seat_height_m: float = 0.48) -> None:
        if not 0.3 <= target_seat_height_m <= 0.8:
            raise ValueError("target seat height is outside the supported range")
        self.target_seat_height_m = target_seat_height_m
        self.stage = SitStage.REVERSE_ALIGNMENT

    @property
    def objective(self) -> SitObjective:
        objectives = {
            SitStage.REVERSE_ALIGNMENT: SitObjective(
                self.stage,
                "geometric_alignment",
                None,
                False,
                "align feet and pelvis with the observed seat frame",
            ),
            SitStage.CONTROLLED_DESCENT: SitObjective(
                self.stage,
                "motion_imitation_policy",
                self.target_seat_height_m + 0.04,
                False,
                "track a smooth sit reference while preserving both foot contacts",
            ),
            SitStage.CONTACT_CONFIRMATION: SitObjective(
                self.stage,
                "contact_aware_policy",
                self.target_seat_height_m,
                True,
                "confirm seat contact before transferring body load",
            ),
            SitStage.LOAD_TRANSFER: SitObjective(
                self.stage,
                "task_residual_policy",
                self.target_seat_height_m,
                True,
                "transfer load to the seat without foot slip",
            ),
            SitStage.STABILIZATION: SitObjective(
                self.stage,
                "seated_stabilizer",
                self.target_seat_height_m,
                True,
                "damp pelvis and torso motion",
            ),
            SitStage.WORK_POSE: SitObjective(
                self.stage,
                "task_residual_policy",
                self.target_seat_height_m,
                True,
                "optimize screen visibility and bilateral keyboard reachability",
            ),
            SitStage.COMPLETE: SitObjective(
                self.stage,
                "hold_work_pose",
                self.target_seat_height_m,
                True,
                "maintain the verified work pose",
            ),
        }
        return objectives[self.stage]

    def update(self, observation: SitObservation) -> SitStage:
        """Advance at most one stage after its physical guard is satisfied."""

        if self.stage is SitStage.REVERSE_ALIGNMENT:
            if (
                abs(observation.chair_position_error_m) <= 0.05
                and abs(observation.chair_yaw_error_rad) <= 0.15
                and observation.left_foot_contact
                and observation.right_foot_contact
            ):
                self.stage = SitStage.CONTROLLED_DESCENT
        elif self.stage is SitStage.CONTROLLED_DESCENT:
            if (
                observation.pelvis_height_m <= self.target_seat_height_m + 0.08
                and observation.seat_contact_confidence >= 0.65
                and observation.left_foot_contact
                and observation.right_foot_contact
            ):
                self.stage = SitStage.CONTACT_CONFIRMATION
        elif self.stage is SitStage.CONTACT_CONFIRMATION:
            if observation.seat_contact_confidence >= 0.85:
                self.stage = SitStage.LOAD_TRANSFER
        elif self.stage is SitStage.LOAD_TRANSFER:
            if (
                observation.seat_contact_confidence >= 0.9
                and observation.left_foot_contact
                and observation.right_foot_contact
                and observation.pelvis_speed_mps <= 0.08
            ):
                self.stage = SitStage.STABILIZATION
        elif self.stage is SitStage.STABILIZATION:
            if (
                observation.pelvis_speed_mps <= 0.025
                and observation.torso_angular_speed_rps <= 0.08
            ):
                self.stage = SitStage.WORK_POSE
        elif self.stage is SitStage.WORK_POSE:
            if (
                observation.screen_visible
                and observation.hands_reachable
                and observation.seat_contact_confidence >= 0.9
            ):
                self.stage = SitStage.COMPLETE
        return self.stage

    @property
    def complete(self) -> bool:
        return self.stage is SitStage.COMPLETE

    def reset(self) -> None:
        self.stage = SitStage.REVERSE_ALIGNMENT

