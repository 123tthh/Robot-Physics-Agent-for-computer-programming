"""Simulator-neutral training environment and learner interface (standard library)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import itertools
import math
import random
from typing import Protocol

from ..control.commands import RootVelocityCommand
from .model import DT, ChairSample, Observation, finite, measured_velocity, vector


@dataclass(frozen=True, slots=True)
class TrainingConfig:
    schema: str = "chair-training-v1"
    episode_s: float = 20.0
    command_hold_s: float = 2.0
    iterations: int = 10000
    rollout_steps: int = 24
    seed: int = 42
    symmetry: bool = True
    foot_slip: bool = False
    curriculum: bool = True
    # Our baseline weights/ranges, not a claim of exact paper reproduction.
    linear_tracking_weight: float = 2.0
    yaw_tracking_weight: float = 1.0
    seat_weight: float = 0.5
    action_rate_weight: float = 0.01
    energy_weight: float = 0.0001
    slip_weight: float = 0.1

    def __post_init__(self) -> None:
        if self.schema != "chair-training-v1":
            raise ValueError("unsupported training config version")
        for name in ("iterations", "rollout_steps", "seed"):
            value = getattr(self, name)
            if type(value) is not int or value < (0 if name == "seed" else 1):
                raise ValueError(f"invalid {name}")
        for name in ("symmetry", "foot_slip", "curriculum"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be boolean")
        vals = (self.episode_s, self.command_hold_s, self.linear_tracking_weight,
                self.yaw_tracking_weight, self.seat_weight, self.action_rate_weight,
                self.energy_weight, self.slip_weight)
        finite(vals)
        if min(vals) < 0 or not DT <= self.command_hold_s <= self.episode_s <= 120:
            raise ValueError("invalid horizon or reward weights")
        for period in (self.episode_s, self.command_hold_s):
            if not math.isclose(period / DT, round(period / DT)):
                raise ValueError("periods must be integer multiples of 0.02 s")


@dataclass(frozen=True, slots=True)
class ResetSpec:
    seed: int
    foot_friction: float
    seat_friction: float
    chair_mass_scale: float
    caster_damping_scale: float
    seat_height_offset_m: float
    caster_yaw_rad: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class BackendFrame:
    observation: Observation
    sample: ChairSample
    joint_torques_nm: tuple[float, ...]
    joint_velocities_rps: tuple[float, ...]
    foot_slip_speed_mps: tuple[float, float]

    def __post_init__(self) -> None:
        vector(self.joint_torques_nm, 29)
        vector(self.joint_velocities_rps, 29)
        vector(self.foot_slip_speed_mps, 2)
        if min(self.foot_slip_speed_mps) < 0:
            raise ValueError("slip speed is a nonnegative magnitude")


class PhysicsBackend(Protocol):
    """Only this adapter accesses joints, collision geometry and simulator truth.

    reset/step/read return a NEW immutable frame. step advances exactly 0.02 s
    using four 0.005 s substeps. read only rebuilds observations after a new
    command; it MUST NOT advance physics. Action is 29 normalized residuals;
    q_default, scale, PD gains and joint limits belong to the backend manifest.
    """

    evidence_level: str

    def reset(self, spec: ResetSpec, command: RootVelocityCommand) -> BackendFrame: ...
    def read(self, command: RootVelocityCommand) -> BackendFrame: ...
    def step(self, action: tuple[float, ...], command: RootVelocityCommand) -> BackendFrame: ...
    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class TrainingStep:
    observation: Observation
    reward: float
    terminated: bool
    truncated: bool
    info: dict[str, object]


class ChairTrainingEnv:
    """Single-environment reference API; vectorization belongs in optional adapters."""

    def __init__(self, backend: PhysicsBackend, config: TrainingConfig) -> None:
        self.backend, self.config = backend, config
        self.rng = random.Random(config.seed)
        self.level = 0 if config.curriculum else 3
        self.ticks = 0
        self.frame: BackendFrame | None = None
        self.done = True
        self.previous_action = (0.0,) * 29
        self.command = RootVelocityCommand()
        self.last_reset: ResetSpec | None = None

    def _command(self) -> RootVelocityCommand:
        scale = (0.15, 0.3, 0.6, 1.0)[self.level]
        # Include a deliberate stop distribution without calling it reposition success.
        if self.rng.random() < 0.1:
            return RootVelocityCommand()
        return RootVelocityCommand(self.rng.uniform(-scale, scale),
                                   self.rng.uniform(-scale, scale),
                                   self.rng.uniform(-0.5 * scale, 0.5 * scale))

    def record_curriculum_evaluation(self, *, episodes: int, success_rate: float,
                                     stationary_rate: float) -> None:
        finite((success_rate, stationary_rate))
        if type(episodes) is not int or episodes < 1:
            raise ValueError("evaluation requires episodes")
        if not 0 <= success_rate <= 1 or not 0 <= stationary_rate <= 1:
            raise ValueError("rates must be probabilities")
        if self.config.curriculum and episodes >= 20 and success_rate >= 0.9 and stationary_rate <= 0.05:
            self.level = min(3, self.level + 1)

    def reset(self, *, seed: int | None = None) -> Observation:
        if seed is not None:
            self.rng.seed(seed)
        spec = ResetSpec(
            self.rng.randrange(2**31), self.rng.uniform(0.4, 1.1),
            self.rng.uniform(0.6, 1.1), self.rng.uniform(0.85, 1.15),
            self.rng.uniform(0.7, 1.3), self.rng.uniform(-0.02, 0.02),
            tuple(self.rng.uniform(-math.pi, math.pi) for _ in range(5)),
        )
        self.command = self._command()
        self.last_reset = spec
        self.frame = self.backend.reset(spec, self.command)
        self.ticks, self.done, self.previous_action = 0, False, (0.0,) * 29
        return self.frame.observation

    def step(self, action: tuple[float, ...]) -> TrainingStep:
        if self.done or self.frame is None:
            raise RuntimeError("reset required before stepping")
        action = vector(action, 29)
        if any(abs(v) > 1 for v in action):
            raise ValueError("normalized actions must be within [-1, 1]")
        old = self.frame
        new = self.backend.step(action, self.command)
        if (new.sample.episode_id != old.sample.episode_id
                or new.sample.source != old.sample.source
                or not math.isclose(new.sample.time_s - old.sample.time_s, DT, abs_tol=1e-6)):
            self.done = True
            raise ValueError("backend changed episode/source or violated policy timestep")
        vx, vy, wz = measured_velocity(old.sample, new.sample)
        c, cfg, s = self.command, self.config, new.sample
        terms = {
            "linear_tracking": cfg.linear_tracking_weight * math.exp(-((vx-c.vx_mps)**2 + (vy-c.vy_mps)**2) / 0.25),
            "yaw_tracking": cfg.yaw_tracking_weight * math.exp(-(wz-c.yaw_rate_rps)**2 / 0.25),
            "seat": cfg.seat_weight * s.seat_confidence * math.exp(-(s.pelvis_offset_m / 0.05)**2),
            "upright": -0.5 * (s.torso_tilt_rad**2 + s.chair_tilt_rad**2),
            "action_rate": -cfg.action_rate_weight * sum((a-b)**2 for a, b in zip(action, self.previous_action)),
            "energy": -cfg.energy_weight * sum(abs(t*v) for t, v in zip(new.joint_torques_nm, new.joint_velocities_rps)),
            "foot_slip": -cfg.slip_weight * sum(v*v for v, contact in zip(new.foot_slip_speed_mps, s.foot_contacts) if contact) if cfg.foot_slip else 0.0,
        }
        terminated = (s.contact_loss_s >= 1.0 or max(s.torso_tilt_rad, s.chair_tilt_rad) >= math.radians(70)
                      or s.collision or s.emergency_stop)
        self.ticks += 1
        truncated = self.ticks >= round(cfg.episode_s / DT) and not terminated
        self.done = terminated or truncated
        self.previous_action, self.frame = action, new
        if not self.done and self.ticks % round(cfg.command_hold_s / DT) == 0:
            self.command = self._command()
            refreshed = self.backend.read(self.command)
            if refreshed.sample != new.sample:
                self.done = True
                raise ValueError("backend read advanced or changed physical state")
            self.frame = refreshed
        return TrainingStep(self.frame.observation, sum(terms.values()) * DT,
                            terminated, truncated, {"reward_terms": terms,
                            "curriculum_level": self.level,
                            "evidence_level": self.backend.evidence_level,
                            "sample": asdict(s)})

    def close(self) -> None:
        self.backend.close()
        self.done = True


class Trainer(Protocol):
    def train(self, env: ChairTrainingEnv, output_dir: str) -> dict[str, object]:
        """Persist config/seeds, metrics, optimizer/checkpoint and artifact hashes.

        PPO must use actor inputs only for π, critic inputs only for V; retain
        terminal observations; bootstrap truncation but not physical termination.
        SY requires an explicitly validated robot-specific reflection transform.
        CC advances only after evaluation via record_curriculum_evaluation.
        Return a run summary; it cannot certify deployment or physical success.
        """
        ...


def experiment_matrix(config: TrainingConfig) -> list[dict[str, object]]:
    return [dict(asdict(config), seed=seed, symmetry=sy, foot_slip=fs, curriculum=cc)
            for seed, sy, fs, cc in itertools.product((42, 1234, 2026, 3407), (False, True), (False, True), (False, True))]
