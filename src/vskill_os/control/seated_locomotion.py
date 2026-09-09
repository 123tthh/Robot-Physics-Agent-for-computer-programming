"""Guarded low-dimensional control for locomotion on a passive mobile chair.

The high-level runtime only sees planar velocity intent and verification
metrics.  A future policy adapter may produce joint targets internally, but
those targets never cross the Agent/Skill boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

from .commands import RootVelocityCommand


@dataclass(frozen=True, slots=True)
class SeatedLocomotionLimits:
    """Conservative deployment limits, intentionally below training termination."""

    max_linear_mps: float = 0.5
    max_yaw_rate_rps: float = 0.5
    min_start_seat_confidence: float = 0.9
    min_active_seat_confidence: float = 0.65
    max_contact_loss_s: float = 0.25
    max_pelvis_seat_offset_m: float = 0.08
    max_torso_tilt_rad: float = 0.35
    max_chair_tilt_rad: float = 0.35

    def __post_init__(self) -> None:
        positive = (
            self.max_linear_mps,
            self.max_yaw_rate_rps,
            self.max_contact_loss_s,
            self.max_pelvis_seat_offset_m,
            self.max_torso_tilt_rad,
            self.max_chair_tilt_rad,
        )
        if any(value <= 0.0 or not math.isfinite(value) for value in positive):
            raise ValueError("seated-locomotion limits must be finite and positive")
        for value in (
            self.min_start_seat_confidence,
            self.min_active_seat_confidence,
        ):
            if not 0.0 <= value <= 1.0:
                raise ValueError("seat confidence limits must be within [0, 1]")
        if self.min_active_seat_confidence > self.min_start_seat_confidence:
            raise ValueError("active seat confidence cannot exceed the start threshold")


@dataclass(frozen=True, slots=True)
class SeatedLocomotionObservation:
    """One policy-rate observation expressed without controller joint commands."""

    measured_vx_mps: float
    measured_vy_mps: float
    measured_yaw_rate_rps: float
    seat_contact_confidence: float
    continuous_contact_loss_s: float
    pelvis_seat_offset_m: float
    torso_tilt_rad: float
    chair_tilt_rad: float
    left_foot_contact: bool
    right_foot_contact: bool
    sample_duration_s: float = 0.02

    def __post_init__(self) -> None:
        numeric = (
            self.measured_vx_mps,
            self.measured_vy_mps,
            self.measured_yaw_rate_rps,
            self.continuous_contact_loss_s,
            self.pelvis_seat_offset_m,
            self.torso_tilt_rad,
            self.chair_tilt_rad,
            self.sample_duration_s,
        )
        if any(not math.isfinite(value) for value in numeric):
            raise ValueError("seated-locomotion observations must be finite")
        if not 0.0 <= self.seat_contact_confidence <= 1.0:
            raise ValueError("seat_contact_confidence must be within [0, 1]")
        if any(
            value < 0.0
            for value in (
                self.continuous_contact_loss_s,
                self.pelvis_seat_offset_m,
                self.torso_tilt_rad,
                self.chair_tilt_rad,
            )
        ):
            raise ValueError("contact loss, offsets, and tilts cannot be negative")
        if self.sample_duration_s <= 0.0:
            raise ValueError("sample_duration_s must be positive")

    def to_dict(self) -> dict[str, object]:
        return {
            "measured_velocity": {
                "vx_mps": self.measured_vx_mps,
                "vy_mps": self.measured_vy_mps,
                "yaw_rate_rps": self.measured_yaw_rate_rps,
            },
            "seat_contact_confidence": self.seat_contact_confidence,
            "continuous_contact_loss_s": self.continuous_contact_loss_s,
            "pelvis_seat_offset_m": self.pelvis_seat_offset_m,
            "torso_tilt_rad": self.torso_tilt_rad,
            "chair_tilt_rad": self.chair_tilt_rad,
            "left_foot_contact": self.left_foot_contact,
            "right_foot_contact": self.right_foot_contact,
            "sample_duration_s": self.sample_duration_s,
        }


class SeatedLocomotionSafetyError(RuntimeError):
    def __init__(self, failure_code: str, message: str) -> None:
        super().__init__(message)
        self.failure_code = failure_code


@dataclass(frozen=True, slots=True)
class SeatedLocomotionMetrics:
    requested_command: RootVelocityCommand
    issued_command: RootVelocityCommand
    duration_s: float
    path_length_m: float
    absolute_yaw_rad: float
    tracking_rmse_vx_mps: float
    tracking_rmse_vy_mps: float
    tracking_rmse_yaw_rps: float
    pelvis_seat_rms_m: float
    minimum_seat_contact_confidence: float
    maximum_contact_loss_s: float
    maximum_torso_tilt_rad: float
    maximum_chair_tilt_rad: float
    left_foot_contact_fraction: float
    right_foot_contact_fraction: float
    settled: bool

    def to_dict(self) -> dict[str, object]:
        def command_dict(command: RootVelocityCommand) -> dict[str, float]:
            return {
                "vx_mps": command.vx_mps,
                "vy_mps": command.vy_mps,
                "yaw_rate_rps": command.yaw_rate_rps,
            }

        return {
            "requested_command": command_dict(self.requested_command),
            "issued_command": command_dict(self.issued_command),
            "duration_s": self.duration_s,
            "path_length_m": self.path_length_m,
            "absolute_yaw_rad": self.absolute_yaw_rad,
            "tracking_rmse": {
                "vx_mps": self.tracking_rmse_vx_mps,
                "vy_mps": self.tracking_rmse_vy_mps,
                "yaw_rate_rps": self.tracking_rmse_yaw_rps,
            },
            "pelvis_seat_rms_m": self.pelvis_seat_rms_m,
            "minimum_seat_contact_confidence": self.minimum_seat_contact_confidence,
            "maximum_contact_loss_s": self.maximum_contact_loss_s,
            "maximum_torso_tilt_rad": self.maximum_torso_tilt_rad,
            "maximum_chair_tilt_rad": self.maximum_chair_tilt_rad,
            "left_foot_contact_fraction": self.left_foot_contact_fraction,
            "right_foot_contact_fraction": self.right_foot_contact_fraction,
            "settled": self.settled,
        }


class SeatedLocomotionController:
    """Owns command limiting and contact/upright guards for one bounded rollout."""

    def __init__(self, limits: SeatedLocomotionLimits | None = None) -> None:
        self.limits = limits or SeatedLocomotionLimits()
        self._requested: RootVelocityCommand | None = None
        self._issued: RootVelocityCommand | None = None
        self._samples: list[SeatedLocomotionObservation] = []
        self.last_metrics: SeatedLocomotionMetrics | None = None

    @property
    def active(self) -> bool:
        return self._issued is not None

    def begin(
        self,
        command: RootVelocityCommand,
        observation: SeatedLocomotionObservation,
    ) -> RootVelocityCommand:
        if self.active:
            raise RuntimeError("a seated-locomotion rollout is already active")
        if observation.seat_contact_confidence < self.limits.min_start_seat_confidence:
            raise SeatedLocomotionSafetyError(
                "SEAT_CONTACT_LOST",
                "seat contact is not reliable enough to start seated locomotion",
            )
        self._check_active_guard(observation)
        self._requested = command
        self._issued = command.bounded(
            max_linear_mps=self.limits.max_linear_mps,
            max_yaw_rate_rps=self.limits.max_yaw_rate_rps,
        )
        self._samples.clear()
        return self._issued

    def observe(self, observation: SeatedLocomotionObservation) -> None:
        if not self.active:
            raise RuntimeError("seated locomotion has not started")
        self._check_active_guard(observation)
        self._samples.append(observation)

    def finish(
        self,
        settled_observation: SeatedLocomotionObservation,
    ) -> SeatedLocomotionMetrics:
        if self._requested is None or self._issued is None:
            raise RuntimeError("seated locomotion has not started")
        if not self._samples:
            raise RuntimeError("seated locomotion requires at least one motion sample")
        self._check_active_guard(settled_observation)

        requested = self._requested
        issued = self._issued
        duration = sum(sample.sample_duration_s for sample in self._samples)

        def weighted_rmse(values: list[tuple[float, float]]) -> float:
            return math.sqrt(
                sum(error * error * weight for error, weight in values) / duration
            )

        metrics = SeatedLocomotionMetrics(
            requested_command=requested,
            issued_command=issued,
            duration_s=duration,
            path_length_m=sum(
                math.hypot(sample.measured_vx_mps, sample.measured_vy_mps)
                * sample.sample_duration_s
                for sample in self._samples
            ),
            absolute_yaw_rad=sum(
                abs(sample.measured_yaw_rate_rps) * sample.sample_duration_s
                for sample in self._samples
            ),
            tracking_rmse_vx_mps=weighted_rmse(
                [
                    (sample.measured_vx_mps - issued.vx_mps, sample.sample_duration_s)
                    for sample in self._samples
                ]
            ),
            tracking_rmse_vy_mps=weighted_rmse(
                [
                    (sample.measured_vy_mps - issued.vy_mps, sample.sample_duration_s)
                    for sample in self._samples
                ]
            ),
            tracking_rmse_yaw_rps=weighted_rmse(
                [
                    (
                        sample.measured_yaw_rate_rps - issued.yaw_rate_rps,
                        sample.sample_duration_s,
                    )
                    for sample in self._samples
                ]
            ),
            pelvis_seat_rms_m=weighted_rmse(
                [
                    (sample.pelvis_seat_offset_m, sample.sample_duration_s)
                    for sample in self._samples
                ]
            ),
            minimum_seat_contact_confidence=min(
                sample.seat_contact_confidence for sample in self._samples
            ),
            maximum_contact_loss_s=max(
                sample.continuous_contact_loss_s for sample in self._samples
            ),
            maximum_torso_tilt_rad=max(
                sample.torso_tilt_rad for sample in self._samples
            ),
            maximum_chair_tilt_rad=max(
                sample.chair_tilt_rad for sample in self._samples
            ),
            left_foot_contact_fraction=sum(
                sample.sample_duration_s
                for sample in self._samples
                if sample.left_foot_contact
            )
            / duration,
            right_foot_contact_fraction=sum(
                sample.sample_duration_s
                for sample in self._samples
                if sample.right_foot_contact
            )
            / duration,
            settled=(
                settled_observation.seat_contact_confidence
                >= self.limits.min_start_seat_confidence
                and math.hypot(
                    settled_observation.measured_vx_mps,
                    settled_observation.measured_vy_mps,
                )
                <= 0.05
                and abs(settled_observation.measured_yaw_rate_rps) <= 0.08
                and settled_observation.pelvis_seat_offset_m <= 0.04
                and settled_observation.torso_tilt_rad <= 0.12
                and settled_observation.chair_tilt_rad <= 0.12
            ),
        )
        self.last_metrics = metrics
        self._clear_active()
        return metrics

    def abort(self) -> None:
        self._clear_active()

    def _clear_active(self) -> None:
        self._requested = None
        self._issued = None
        self._samples.clear()

    def _check_active_guard(self, observation: SeatedLocomotionObservation) -> None:
        if (
            observation.continuous_contact_loss_s > self.limits.max_contact_loss_s
            or observation.seat_contact_confidence
            < self.limits.min_active_seat_confidence
            or observation.pelvis_seat_offset_m
            > self.limits.max_pelvis_seat_offset_m
        ):
            raise SeatedLocomotionSafetyError(
                "SEAT_CONTACT_LOST",
                "pelvis-seat contact guard was violated",
            )
        if (
            observation.torso_tilt_rad > self.limits.max_torso_tilt_rad
            or observation.chair_tilt_rad > self.limits.max_chair_tilt_rad
        ):
            raise SeatedLocomotionSafetyError(
                "SEATED_POSTURE_UNSTABLE",
                "torso or chair tilt exceeded the seated safety envelope",
            )
