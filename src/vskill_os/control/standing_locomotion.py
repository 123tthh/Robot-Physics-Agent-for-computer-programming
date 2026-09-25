"""Guarded SE(2) navigation; low-level walking belongs to a policy adapter.

The CPU backend integrates a kinematic model for deterministic contract tests.
It neither proves leg motion nor counts as physical walking.  No interface in
this module emits joint targets or sets a simulator root pose.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path
import random
from typing import Protocol

from .commands import RootVelocityCommand
from ..robot.g1 import G1Joint


def angle_error(target: float, current: float) -> float:
    return math.atan2(math.sin(target - current), math.cos(target - current))


@dataclass(frozen=True, slots=True)
class SE2Goal:
    x_m: float
    y_m: float
    yaw_rad: float = 0.0
    max_duration_s: float = 30.0
    position_tolerance_m: float = 0.025
    yaw_tolerance_rad: float = 0.06

    def __post_init__(self):
        if not all(math.isfinite(v) for v in asdict(self).values()):
            raise ValueError("goal values must be finite")
        if not 0.5 <= self.max_duration_s <= 120.0:
            raise ValueError("navigation horizon must be in [0.5, 120] seconds")
        if not 0 < self.position_tolerance_m <= 0.10 or not 0 < self.yaw_tolerance_rad <= 0.2:
            raise ValueError("goal tolerances are outside the deployment envelope")


@dataclass(frozen=True, slots=True)
class StandingNavigationLimits:
    period_s: float = 0.02
    max_linear_mps: float = 0.25
    max_yaw_rate_rps: float = 0.40
    max_linear_acceleration_mps2: float = 0.35
    max_yaw_acceleration_rps2: float = 0.8
    max_torso_tilt_rad: float = 0.30
    max_stance_slip_mps: float = 0.12
    max_contact_loss_s: float = 0.08
    min_base_height_m: float = 0.55
    max_base_height_m: float = 1.05
    settling_duration_s: float = 0.50
    stopped_linear_mps: float = 0.025
    stopped_yaw_rps: float = 0.045
    stop_timeout_s: float = 2.0

    def __post_init__(self):
        if not all(math.isfinite(v) and v > 0 for v in asdict(self).values()):
            raise ValueError("navigation limits must be finite and positive")
        if self.period_s > 0.05 or self.max_linear_mps > 0.5 or self.max_yaw_rate_rps > 1.0:
            raise ValueError("navigation limits exceed the conservative standing envelope")
        if self.min_base_height_m >= self.max_base_height_m or self.settling_duration_s > self.stop_timeout_s:
            raise ValueError("inconsistent height or settling bounds")


@dataclass(frozen=True, slots=True)
class LocomotionObservation:
    tick: int
    time_s: float
    x_m: float
    y_m: float
    yaw_rad: float
    vx_mps: float = 0.0
    vy_mps: float = 0.0
    yaw_rate_rps: float = 0.0
    torso_tilt_rad: float = 0.0
    base_height_m: float = 0.79
    left_foot_contact: bool = True
    right_foot_contact: bool = True
    left_foot_slip_mps: float = 0.0
    right_foot_slip_mps: float = 0.0
    collision: bool = False
    teleported: bool = False
    source: str = "synthetic_kinematics"
    actuation_mode: str = "synthetic_kinematics"

    def validate(self):
        if not isinstance(self.tick, int) or isinstance(self.tick, bool) or self.tick < 0:
            raise NavigationFailure("INVALID_OBSERVATION", "tick must be a nonnegative integer")
        values = (self.time_s, self.x_m, self.y_m, self.yaw_rad, self.vx_mps,
                  self.vy_mps, self.yaw_rate_rps, self.torso_tilt_rad,
                  self.base_height_m, self.left_foot_slip_mps, self.right_foot_slip_mps)
        if not all(math.isfinite(v) for v in values) or any(v < 0 for v in (self.time_s, self.torso_tilt_rad, self.base_height_m, self.left_foot_slip_mps, self.right_foot_slip_mps)):
            raise NavigationFailure("INVALID_OBSERVATION", "nonfinite or negative physical measurement")
        if any(type(v) is not bool for v in (self.left_foot_contact, self.right_foot_contact, self.collision, self.teleported)):
            raise NavigationFailure("INVALID_OBSERVATION", "contact/collision observations must be Boolean")


@dataclass(frozen=True, slots=True)
class PolicyCheckpoint:
    """Exact artifact/deployment contract; validation never executes a model.

    ``joint_order`` uses the 29 SDK body names, without a ``_joint`` suffix.
    A policy's internal observation order, scaling and default pose must be
    pinned; adapters must follow those descriptors when running inference.
    """
    checkpoint_path: str
    checkpoint_sha256: str
    robot_asset_sha256: str
    joint_order: tuple[str, ...]
    observation_schema: tuple[tuple[str, int], ...]
    observation_scales: tuple[float, ...]
    action_scale: tuple[float, ...]
    default_joint_positions: tuple[float, ...]
    simulator_version: str
    training_commit: str
    policy_hz: float = 50.0
    mode_machine: int = 15
    wrist_motor: str = "5010"
    body_dof: int = 29
    hand_dof: int = 14
    hand_payload: str = "dual_dex3_1"
    action_type: str = "joint_position_residual"
    version: str = "1.0.0"

    @classmethod
    def from_json(cls, path: str | Path) -> "PolicyCheckpoint":
        manifest = Path(path)
        data = json.loads(manifest.read_text())
        checkpoint = Path(data["checkpoint_path"])
        data["checkpoint_path"] = str((manifest.parent / checkpoint).resolve()) if not checkpoint.is_absolute() else str(checkpoint)
        for name in ("joint_order", "observation_scales", "action_scale", "default_joint_positions"):
            data[name] = tuple(data[name])
        data["observation_schema"] = tuple(tuple(item) for item in data["observation_schema"])
        return cls(**data)

    def validate(self, robot_asset_path: str | Path, *, runtime_version: str, period_s: float) -> dict[str, object]:
        expected_order = tuple(j.name.lower() for j in G1Joint)
        if (self.mode_machine, self.wrist_motor, self.body_dof, self.hand_dof, self.hand_payload) != (15, "5010", 29, 14, "dual_dex3_1"):
            raise NavigationFailure("POLICY_INCOMPATIBLE", "requires G1 mode15/5010 with both Dex3-1 hands")
        if self.joint_order != expected_order or self.action_type != "joint_position_residual":
            raise NavigationFailure("POLICY_INCOMPATIBLE", "joint order or policy action convention differs")
        if self.simulator_version != runtime_version or not self.simulator_version or not self.version:
            raise NavigationFailure("POLICY_INCOMPATIBLE", "policy/runtime versions must match exactly")
        if not math.isfinite(self.policy_hz) or self.policy_hz <= 0 or abs(self.policy_hz * period_s - 1.0) > 1e-6:
            raise NavigationFailure("POLICY_INCOMPATIBLE", "policy rate must match the navigation observation period")
        hexchars = set("0123456789abcdef")
        for value, size in ((self.checkpoint_sha256, 64), (self.robot_asset_sha256, 64), (self.training_commit, 40)):
            if len(value) != size or not set(value).issubset(hexchars):
                raise NavigationFailure("POLICY_UNPINNED", "policy hashes and training commit must be pinned")
        required = {"projected_gravity", "joint_positions", "joint_velocities", "last_action", "velocity_command"}
        schema = dict(self.observation_schema)
        if len(schema) != len(self.observation_schema) or not required.issubset(schema) or any(not name or type(size) is not int or size <= 0 for name, size in self.observation_schema):
            raise NavigationFailure("POLICY_INCOMPATIBLE", "observation descriptors are missing or invalid")
        if any(schema[name] != expected for name, expected in (("projected_gravity", 3), ("joint_positions", 29), ("joint_velocities", 29), ("last_action", 29), ("velocity_command", 3))):
            raise NavigationFailure("POLICY_INCOMPATIBLE", "observation dimensions do not match G1 policy contract")
        if len(self.observation_scales) != sum(schema.values()) or len(self.action_scale) != 29 or len(self.default_joint_positions) != 29:
            raise NavigationFailure("POLICY_INCOMPATIBLE", "policy scaling/default pose dimensions are incorrect")
        if not all(math.isfinite(v) and v > 0 for v in (*self.observation_scales, *self.action_scale)) or not all(math.isfinite(v) for v in self.default_joint_positions):
            raise NavigationFailure("POLICY_INCOMPATIBLE", "policy scaling/default pose contains invalid values")
        checkpoint, robot = Path(self.checkpoint_path), Path(robot_asset_path)
        if not checkpoint.is_file() or not robot.is_file():
            raise NavigationFailure("POLICY_ASSET_MISSING", "checkpoint and robot asset must be local files")
        for path, expected in ((checkpoint, self.checkpoint_sha256), (robot, self.robot_asset_sha256)):
            with path.open("rb") as stream:
                actual = hashlib.file_digest(stream, "sha256").hexdigest()
            if actual != expected:
                raise NavigationFailure("POLICY_ASSET_MISMATCH", f"artifact hash differs: {path.name}")
        return {"checkpoint_path": str(checkpoint), "checkpoint_sha256": self.checkpoint_sha256,
                "robot_asset_sha256": self.robot_asset_sha256, "policy_hz": self.policy_hz,
                "training_commit": self.training_commit, "validated": True,
                "note": "artifact compatibility is not a walking-performance claim; composed USD dependencies must also be pinned by the scene loader"}


class LocomotionPolicyAdapter(Protocol):
    evidence_mode: str
    checkpoint: PolicyCheckpoint | None
    robot_asset_path: str | Path | None
    runtime_version: str
    def observe(self) -> LocomotionObservation: ...
    def step(self, command: RootVelocityCommand, dt_s: float) -> LocomotionObservation: ...
    def emergency_stop(self) -> None: ...


class NavigationFailure(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class StandingLocomotionGuard:
    """Sensor-only guard; a policy completion message is never consulted."""
    def __init__(self, limits: StandingNavigationLimits, evidence_mode: str, *, require_initial_double_support: bool = True):
        self.limits, self.evidence_mode = limits, evidence_mode
        self.require_initial_double_support = require_initial_double_support
        self.previous: LocomotionObservation | None = None
        self.no_support_s = 0.0

    def observe(self, obs: LocomotionObservation) -> None:
        try:
            if not isinstance(obs, LocomotionObservation):
                raise ValueError("wrong observation payload type")
            obs.validate()
        except NavigationFailure:
            raise
        except (TypeError, ValueError) as exc:
            raise NavigationFailure("INVALID_OBSERVATION", str(exc)) from exc
        source = "synthetic_kinematics" if self.evidence_mode == "synthetic" else "isaac_physx_locomotion"
        mode = "synthetic_kinematics" if self.evidence_mode == "synthetic" else "joint_policy"
        if obs.source != source or obs.actuation_mode != mode:
            raise NavigationFailure("UNTRUSTED_LOCOMOTION_EVIDENCE", "pose and stance must come from independent physics/synthetic sensors")
        if obs.teleported:
            raise NavigationFailure("TELEPORT_NOT_WALKING", "root-pose reset cannot count as walking")
        if obs.collision:
            raise NavigationFailure("COLLISION", "unexpected body/environment contact")
        if obs.torso_tilt_rad > self.limits.max_torso_tilt_rad or not self.limits.min_base_height_m <= obs.base_height_m <= self.limits.max_base_height_m:
            raise NavigationFailure("STANDING_UNSTABLE", "torso tilt or base height left the standing envelope")
        if (obs.left_foot_contact and obs.left_foot_slip_mps > self.limits.max_stance_slip_mps) or (obs.right_foot_contact and obs.right_foot_slip_mps > self.limits.max_stance_slip_mps):
            raise NavigationFailure("STANCE_SLIP", "a supporting foot exceeded the slip limit")
        speed = math.hypot(obs.vx_mps, obs.vy_mps)
        if speed > self.limits.max_linear_mps * 1.6 or abs(obs.yaw_rate_rps) > self.limits.max_yaw_rate_rps * 1.6:
            raise NavigationFailure("UNCONTROLLED_VELOCITY", "measured velocity exceeds the safety envelope")
        if self.previous is None:
            if self.require_initial_double_support and not (obs.left_foot_contact and obs.right_foot_contact):
                raise NavigationFailure("START_SUPPORT_UNVERIFIED", "both feet must support the starting stance")
            dt = 0.0
        else:
            previous = self.previous
            dt = obs.time_s - previous.time_s
            if obs.tick <= previous.tick or not 0 < dt <= self.limits.period_s * 1.5:
                raise NavigationFailure("STALE_OBSERVATION", "locomotion sample is stale, repeated, or missing")
            distance = math.hypot(obs.x_m - previous.x_m, obs.y_m - previous.y_m)
            if distance > self.limits.max_linear_mps * 1.6 * dt + 0.002 or abs(angle_error(obs.yaw_rad, previous.yaw_rad)) > self.limits.max_yaw_rate_rps * 1.6 * dt + 0.005:
                raise NavigationFailure("POSE_DISCONTINUITY", "root pose jumped beyond the measured walking envelope")
        self.no_support_s = self.no_support_s + dt if not (obs.left_foot_contact or obs.right_foot_contact) else 0.0
        if self.no_support_s > self.limits.max_contact_loss_s + 1e-9:
            raise NavigationFailure("FOOT_CONTACT_LOST", "both feet lost support beyond the permitted interval")
        self.previous = obs


def _arrived(goal: SE2Goal, obs: LocomotionObservation) -> bool:
    return math.hypot(goal.x_m - obs.x_m, goal.y_m - obs.y_m) <= goal.position_tolerance_m and abs(angle_error(goal.yaw_rad, obs.yaw_rad)) <= goal.yaw_tolerance_rad


def _settled(obs: LocomotionObservation, limits: StandingNavigationLimits) -> bool:
    return obs.left_foot_contact and obs.right_foot_contact and math.hypot(obs.vx_mps, obs.vy_mps) <= limits.stopped_linear_mps and abs(obs.yaw_rate_rps) <= limits.stopped_yaw_rps and obs.torso_tilt_rad <= min(0.10, limits.max_torso_tilt_rad)


@dataclass(frozen=True, slots=True)
class NavigationVerification:
    arrived: bool
    stabilized: bool
    accepted: bool
    physical_walking_verified: bool
    failure_codes: tuple[str, ...]
    path_length_m: float


class StandingNavigationVerifier:
    def verify(self, goal: SE2Goal, observations: tuple[LocomotionObservation, ...], *, limits: StandingNavigationLimits,
               evidence_mode: str, policy_verified: bool = False) -> NavigationVerification:
        failures = []
        guard = StandingLocomotionGuard(limits, evidence_mode)
        settled_since = None
        path = 0.0
        valid_observations = []
        for obs in observations:
            try:
                if not isinstance(obs, LocomotionObservation):
                    raise ValueError("wrong observation payload type")
                obs.validate()
            except (NavigationFailure, ValueError, TypeError):
                failures.append("INVALID_OBSERVATION")
                continue
            try:
                guard.observe(obs)
            except NavigationFailure as exc:
                failures.append(exc.code)
            if valid_observations:
                last = valid_observations[-1]
                path += math.hypot(obs.x_m - last.x_m, obs.y_m - last.y_m)
            valid_observations.append(obs)
            if _arrived(goal, obs) and _settled(obs, limits):
                if settled_since is None:
                    settled_since = obs.time_s
            else:
                settled_since = None
        arrived = bool(valid_observations) and _arrived(goal, valid_observations[-1])
        stabilized = bool(valid_observations) and settled_since is not None and valid_observations[-1].time_s - settled_since >= limits.settling_duration_s - 1e-9
        if not arrived:
            failures.append("GOAL_NOT_REACHED")
        if not stabilized:
            failures.append("STANDING_NOT_STABILIZED")
        if evidence_mode not in {"physical", "synthetic"} or (evidence_mode == "physical" and not policy_verified):
            failures.append("POLICY_UNVERIFIED")
        codes = tuple(dict.fromkeys(failures))
        return NavigationVerification(arrived, stabilized, not codes,
            not codes and evidence_mode == "physical" and policy_verified, codes, path)


@dataclass(frozen=True, slots=True)
class NavigationResult:
    succeeded: bool
    failure_code: str | None
    message: str
    arrived: bool
    stabilized: bool
    stop_requested: bool
    stop_verified: bool
    physical_walking_verified: bool
    evidence_mode: str
    goal: SE2Goal
    observations: tuple[LocomotionObservation, ...]
    commands: tuple[RootVelocityCommand, ...]
    verification: NavigationVerification
    policy_evidence: dict[str, object]
    stop_error: str | None = None
    version: str = "1.0.0"

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result.update({"schema": "standing_navigation_result", "joint_commands_exposed": False,
                       "synthetic_is_physical_evidence": False})
        return result


class StandingNavigationSkill:
    contract = {
        "name": "walk_to_standing_work_pose", "version": "1.0.0", "level": "L2",
        "preconditions": ["standing_support_observed", "clear_route", "locomotion_policy_compatible"],
        "postconditions": ["goal_pose_observed", "standing_stable_observed", "velocity_stop_verified"],
        "resources": ["whole_body", "locomotion", "foot_contact_sensors", "base_pose_sensor"],
        "failure_codes": ["POLICY_UNVERIFIED", "POLICY_INCOMPATIBLE", "FOOT_CONTACT_LOST", "STANCE_SLIP",
                          "STANDING_UNSTABLE", "COLLISION", "POSE_DISCONTINUITY", "TELEPORT_NOT_WALKING",
                          "NAVIGATION_TIMEOUT", "CANCELLED", "STOP_UNVERIFIED"],
    }

    def __init__(self, adapter: LocomotionPolicyAdapter, limits: StandingNavigationLimits | None = None):
        self.adapter = adapter
        self.limits = limits or StandingNavigationLimits()

    def command_for(self, goal: SE2Goal, obs: LocomotionObservation, previous: RootVelocityCommand) -> RootVelocityCommand:
        dx, dy = goal.x_m - obs.x_m, goal.y_m - obs.y_m
        c, s = math.cos(obs.yaw_rad), math.sin(obs.yaw_rad)
        desired_x, desired_y = 0.9 * (c * dx + s * dy), 0.9 * (-s * dx + c * dy)
        distance = math.hypot(dx, dy)
        speed_cap = min(self.limits.max_linear_mps, math.sqrt(2 * self.limits.max_linear_acceleration_mps2 * max(0.0, distance - goal.position_tolerance_m * 0.5)))
        magnitude = math.hypot(desired_x, desired_y)
        scale = min(1.0, speed_cap / max(magnitude, 1e-12))
        desired_x, desired_y = desired_x * scale, desired_y * scale
        yaw = max(-self.limits.max_yaw_rate_rps, min(self.limits.max_yaw_rate_rps, 1.3 * angle_error(goal.yaw_rad, obs.yaw_rad)))
        if distance <= goal.position_tolerance_m:
            desired_x = desired_y = 0.0
        if abs(angle_error(goal.yaw_rad, obs.yaw_rad)) <= goal.yaw_tolerance_rad:
            yaw = 0.0
        vx, vy = desired_x - previous.vx_mps, desired_y - previous.vy_mps
        change = math.hypot(vx, vy)
        factor = min(1.0, self.limits.max_linear_acceleration_mps2 * self.limits.period_s / max(change, 1e-12))
        yaw_delta = self.limits.max_yaw_acceleration_rps2 * self.limits.period_s
        return RootVelocityCommand(previous.vx_mps + vx * factor, previous.vy_mps + vy * factor,
            previous.yaw_rate_rps + max(-yaw_delta, min(yaw_delta, yaw - previous.yaw_rate_rps)))

    def run(self, goal: SE2Goal) -> NavigationResult:
        mode = getattr(self.adapter, "evidence_mode", "unknown")
        observations: list[LocomotionObservation] = []
        commands: list[RootVelocityCommand] = []
        policy_evidence: dict[str, object] = {}
        policy_verified = False
        failure = message = None
        stop_requested = stop_verified = False
        stop_error = None
        guard = StandingLocomotionGuard(self.limits, mode)
        try:
            if mode == "physical":
                checkpoint = getattr(self.adapter, "checkpoint", None)
                robot_asset = getattr(self.adapter, "robot_asset_path", None)
                if not isinstance(checkpoint, PolicyCheckpoint) or robot_asset is None:
                    raise NavigationFailure("POLICY_UNVERIFIED", "physical walking requires a matching local checkpoint and robot artifact")
                policy_evidence = checkpoint.validate(robot_asset, runtime_version=self.adapter.runtime_version, period_s=self.limits.period_s)
                policy_verified = True
            elif mode != "synthetic":
                raise NavigationFailure("POLICY_UNVERIFIED", "adapter must declare physical or synthetic evidence scope")
            obs = self.adapter.observe()
            observations.append(obs)
            guard.observe(obs)
            command = RootVelocityCommand()
            stable_since = None
            for _ in range(math.ceil(goal.max_duration_s / self.limits.period_s)):
                command = self.command_for(goal, obs, command)
                commands.append(command)
                obs = self.adapter.step(command, self.limits.period_s)
                observations.append(obs)
                guard.observe(obs)
                if _arrived(goal, obs) and _settled(obs, self.limits):
                    stable_since = obs.time_s if stable_since is None else stable_since
                    if obs.time_s - stable_since >= self.limits.settling_duration_s - 1e-9:
                        break
                else:
                    stable_since = None
            else:
                raise NavigationFailure("NAVIGATION_TIMEOUT", "goal and stable stance were not observed within the finite horizon")
        except KeyboardInterrupt:
            failure, message = "CANCELLED", "navigation cancelled; requested policy safe stop"
        except NavigationFailure as exc:
            failure, message = exc.code, str(exc)
        except Exception as exc:
            failure, message = "ADAPTER_EXCEPTION", f"{type(exc).__name__}: {exc}"
        finally:
            # Never declare a stop merely because the zero command was sent.
            try:
                self.adapter.emergency_stop()
                stop_requested = True
                if mode not in {"synthetic", "physical"} or (mode == "physical" and not policy_verified):
                    raise NavigationFailure("STOP_UNVERIFIED", "no trusted policy/sensor contract for physical stop verification")
                stop_guard = StandingLocomotionGuard(self.limits, mode, require_initial_double_support=False)
                fresh = self.adapter.observe()
                stop_guard.observe(fresh)
                stable_since = fresh.time_s if _settled(fresh, self.limits) else None
                for _ in range(math.ceil(self.limits.stop_timeout_s / self.limits.period_s)):
                    fresh = self.adapter.step(RootVelocityCommand(), self.limits.period_s)
                    observations.append(fresh)
                    stop_guard.observe(fresh)
                    if _settled(fresh, self.limits):
                        stable_since = fresh.time_s if stable_since is None else stable_since
                        if fresh.time_s - stable_since >= self.limits.settling_duration_s - 1e-9:
                            stop_verified = True
                            break
                    else:
                        stable_since = None
            except (Exception, KeyboardInterrupt) as exc:
                stop_error = f"{type(exc).__name__}: {exc}"
        verification = StandingNavigationVerifier().verify(goal, tuple(observations), limits=self.limits,
            evidence_mode=mode, policy_verified=policy_verified)
        if failure is None and not stop_verified:
            failure, message = "STOP_UNVERIFIED", "zero command did not produce a verified stable stop"
        if failure is None and not verification.accepted:
            failure, message = verification.failure_codes[0], "independent navigation verification rejected observations"
        succeeded = failure is None and verification.accepted and stop_verified
        return NavigationResult(succeeded, failure, message or "goal and standing stabilization verified",
            verification.arrived, verification.stabilized, stop_requested, stop_verified,
            succeeded and verification.physical_walking_verified, mode, goal, tuple(observations),
            tuple(commands), verification, policy_evidence, stop_error)


class KinematicWalkingAdapter:
    """Seeded CPU navigation model.  No robot, joints or footsteps are simulated."""
    evidence_mode = "synthetic"
    checkpoint = None
    robot_asset_path = None
    runtime_version = "synthetic-kinematics-1.0.0"

    def __init__(self, *, seed: int = 0, initial_pose: tuple[float, float, float] = (0.0, 0.0, 0.0)):
        if type(seed) is not int or seed < 0 or len(initial_pose) != 3 or not all(math.isfinite(v) for v in initial_pose):
            raise ValueError("seed and initial SE2 pose are invalid")
        rng = random.Random(seed)
        self.response_gain = rng.uniform(0.98, 1.02)
        self.x, self.y, self.yaw = initial_pose
        self.vx = self.vy = self.omega = self.time = 0.0
        self.tick = 0
        self.stop_calls = 0

    def observe(self) -> LocomotionObservation:
        return LocomotionObservation(self.tick, self.time, self.x, self.y, self.yaw, self.vx, self.vy, self.omega)

    def step(self, command: RootVelocityCommand, dt_s: float) -> LocomotionObservation:
        if not math.isfinite(dt_s) or not 0 < dt_s <= 0.05:
            raise ValueError("synthetic step must be within (0, .05] seconds")
        alpha = 1.0 - math.exp(-dt_s / 0.06)
        self.vx += alpha * (command.vx_mps * self.response_gain - self.vx)
        self.vy += alpha * (command.vy_mps * self.response_gain - self.vy)
        self.omega += alpha * (command.yaw_rate_rps * self.response_gain - self.omega)
        c, s = math.cos(self.yaw), math.sin(self.yaw)
        self.x += (c * self.vx - s * self.vy) * dt_s
        self.y += (s * self.vx + c * self.vy) * dt_s
        self.yaw = angle_error(self.yaw + self.omega * dt_s, 0.0)
        self.time += dt_s
        self.tick += 1
        return self.observe()

    def emergency_stop(self) -> None:
        self.stop_calls += 1
        # Deliberately do not set measured velocity to zero: the skill must
        # observe the subsequent commanded deceleration and stable stance.


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Synthetic CPU standing navigation; never physical walking evidence")
    parser.add_argument("--goal-x", type=float, default=0.4)
    parser.add_argument("--goal-y", type=float, default=0.1)
    parser.add_argument("--goal-yaw", type=float, default=0.2)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    result = StandingNavigationSkill(KinematicWalkingAdapter(seed=args.seed)).run(SE2Goal(args.goal_x, args.goal_y, args.goal_yaw))
    payload = result.to_dict()
    payload["seed"] = args.seed
    payload["simulation_scope"] = "synthetic_SE2_kinematics_only_no_leg_dynamics"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, indent=2) + "\n")
    print(json.dumps({"succeeded": result.succeeded, "failure_code": result.failure_code,
        "physical_walking_verified": result.physical_walking_verified, "evidence_mode": result.evidence_mode,
        "output": str(args.output) if args.output else None}))
    return 0 if result.succeeded else 1


if __name__ == "__main__":
    raise SystemExit(main())
