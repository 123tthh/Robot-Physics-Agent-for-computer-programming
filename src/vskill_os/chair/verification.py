"""Safety guards and independent raw-trace verification, never reward-based."""

from __future__ import annotations

from dataclasses import dataclass
import math

from .model import DT, SCHEMA_VERSION, ChairSample, Trace, measured_velocity


def safety_failure(s: ChairSample) -> str | None:
    if s.emergency_stop:
        return "EMERGENCY_STOP"
    if s.collision:
        return "COLLISION"
    if s.seat_confidence < 0.65 or s.contact_loss_s > 0.25:
        return "SEAT_CONTACT_LOST"
    if s.pelvis_offset_m > 0.08 or max(s.torso_tilt_rad, s.chair_tilt_rad) > 0.35:
        return "SEATED_POSTURE_UNSTABLE"
    return None


@dataclass(frozen=True, slots=True)
class ChairVerdict:
    accepted: bool
    failures: tuple[str, ...]
    metrics: dict[str, float]
    evidence_level: str


class ChairVerifier:
    def __init__(self, *, observer_source: str, evidence_level: str = "contract_proxy") -> None:
        if evidence_level not in {"contract_proxy", "simulation_oracle", "hardware_sensor"}:
            raise ValueError("unknown evidence level")
        self.source = observer_source
        self.level = evidence_level

    def verify(self, trace: Trace) -> ChairVerdict:
        failures: list[str] = []
        metrics: dict[str, float] = {}

        def result() -> ChairVerdict:
            return ChairVerdict(not failures, tuple(dict.fromkeys(failures)), metrics, self.level)

        ss, end = trace.samples, trace.motion_end_index
        if trace.schema != SCHEMA_VERSION or not 1 <= end < len(ss) - 1:
            failures.append("INVALID_TRACE")
            return result()
        if any(s.source != self.source or s.episode_id != ss[0].episode_id for s in ss):
            failures.append("UNTRUSTED_EVIDENCE")
            return result()
        # Bound gaps and reject duplicate/reversed clocks; do not interpolate missing data.
        if any(not math.isclose(b.time_s - a.time_s, DT, abs_tol=1e-6)
               for a, b in zip(ss, ss[1:])):
            failures.append("STALE_OBSERVATION")
            return result()
        duration = ss[end].time_s - ss[0].time_s
        if not math.isclose(duration, trace.command.duration_s, abs_tol=1e-6):
            failures.append("INCOMPLETE_HORIZON")
        if ss[0].seat_confidence < 0.9:
            failures.append("SEAT_CONTACT_LOST")
        for sample in ss:
            problem = safety_failure(sample)
            if problem:
                failures.append(problem)
        if min(s.seat_confidence for s in ss) < 0.85 or max(s.pelvis_offset_m for s in ss) > 0.05:
            failures.append("SEATED_POSTURE_UNSTABLE")
        if max(max(s.torso_tilt_rad, s.chair_tilt_rad) for s in ss) > 0.20:
            failures.append("SEATED_POSTURE_UNSTABLE")
        cmd = trace.command.velocity
        target = (cmd.vx_mps, cmd.vy_mps, cmd.yaw_rate_rps)
        velocities = [measured_velocity(a, b) for a, b in zip(ss[:end], ss[1:end + 1])]
        for i, axis in enumerate(("vx", "vy", "yaw")):
            rmse = math.sqrt(sum((v[i] - target[i]) ** 2 for v in velocities) / len(velocities))
            metrics[f"tracking_rmse_{axis}"] = rmse
            if rmse > 0.20:
                failures.append("SEATED_TRACKING_FAILED")
        speed = math.hypot(*target[:2])
        # Signed command-aligned progress rejects oscillation and wrong-way motion.
        # Check robot AND chair; a pelvis moving over a stationary seat cannot pass.
        for is_chair, name in ((False, "robot"), (True, "chair")):
            vv = [measured_velocity(a, b, chair=is_chair)
                  for a, b in zip(ss[:end], ss[1:end + 1])]
            progress = sum((v[0] * target[0] + v[1] * target[1]) * DT
                           for v in vv) / max(speed, 1e-9)
            yaw = sum(v[2] * DT for v in vv) * (1 if target[2] >= 0 else -1)
            metrics[f"{name}_signed_progress_m"] = progress
            metrics[f"{name}_signed_yaw_rad"] = yaw
            if speed * duration >= 0.02 and progress < 0.5 * speed * duration:
                failures.append("NO_PROGRESS")
            if abs(target[2]) * duration >= 0.02 and yaw < 0.5 * abs(target[2]) * duration:
                failures.append("NO_PROGRESS")
        if not any(any(s.foot_contacts) for s in ss[1:end + 1]):
            failures.append("NO_PROPULSION_CONTACT")
        settle = ss[end:]
        metrics["settle_duration_s"] = settle[-1].time_s - settle[0].time_s
        if metrics["settle_duration_s"] < 0.5 - 1e-6:
            failures.append("NOT_SETTLED")
        for a, b in zip(settle, settle[1:]):
            for is_chair in (False, True):
                vx, vy, wz = measured_velocity(a, b, chair=is_chair)
                if math.hypot(vx, vy) > 0.03 or abs(wz) > 0.05:
                    failures.append("NOT_SETTLED")
        if not all(s.screen_visible and s.hands_reachable for s in settle):
            failures.append("WORK_POSE_NOT_READY")
        return result()
