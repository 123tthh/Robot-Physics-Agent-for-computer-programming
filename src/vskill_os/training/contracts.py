"""Simulator-independent contracts for finite-horizon index-finger training."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Protocol

Vec3 = tuple[float, float, float]
HAND_NAMES = ("left", "right")
CONTRACT_VERSION = "1.0.0"
MAX_CARTESIAN_RESIDUAL_M = 0.004


@dataclass(frozen=True)
class Stroke:
    key: str
    character: str
    primary_hand: str
    shift_key: str | None = None
    shift_hand: str | None = None
    wave: int = 0

    def __post_init__(self) -> None:
        if self.primary_hand not in HAND_NAMES or len(self.character) != 1 or not self.key:
            raise ValueError("stroke must identify one character and a valid hand/key")
        if bool(self.shift_key) != bool(self.shift_hand):
            raise ValueError("shift key and hand must be specified together")
        if self.shift_hand and (self.shift_hand not in HAND_NAMES or self.shift_hand == self.primary_hand
                                or self.shift_key != f"{self.shift_hand}_shift"):
            raise ValueError("Shift requires the opposite index fingertip")
        if type(self.wave) is not int or self.wave < 0:
            raise ValueError("stroke wave must be a nonnegative integer")


@dataclass(frozen=True)
class TypingGoal:
    text: str
    strokes: tuple[Stroke, ...]
    stage: str
    version: str = CONTRACT_VERSION

    def __post_init__(self) -> None:
        if not self.text or len(self.text) > 256 or "".join(stroke.character for stroke in self.strokes) != self.text:
            raise ValueError("goal text and stroke sequence must agree (1 to 256 characters)")
        if self.version != CONTRACT_VERSION:
            raise ValueError("unsupported typing-goal version")
        if tuple(stroke.wave for stroke in self.strokes) != tuple(sorted(stroke.wave for stroke in self.strokes)):
            raise ValueError("coordinated waves must be monotonically ordered")
        for wave in {stroke.wave for stroke in self.strokes}:
            executors = [hand for stroke in self.strokes if stroke.wave == wave
                         for hand in (stroke.primary_hand, stroke.shift_hand) if hand]
            if len(executors) != len(set(executors)):
                raise ValueError("a preposition wave cannot reserve the same index twice")


@dataclass(frozen=True)
class DomainRandomization:
    """SI units; adapters must report which fields they actually implement."""

    keyboard_translation_m: float = 0.004
    controller_bias_m: float = 0.001
    tracking_gain_min: float = 0.65
    tracking_gain_max: float = 1.0
    contact_force_scale_min: float = 0.75
    contact_force_scale_max: float = 1.25

    def __post_init__(self) -> None:
        values = asdict(self)
        if not all(math.isfinite(value) for value in values.values()):
            raise ValueError("domain randomization values must be finite")
        if self.keyboard_translation_m < 0 or self.controller_bias_m < 0:
            raise ValueError("randomization distances cannot be negative")
        if not 0 < self.tracking_gain_min <= self.tracking_gain_max <= 1:
            raise ValueError("tracking gains must be in (0, 1]")
        if not 0 < self.contact_force_scale_min <= self.contact_force_scale_max:
            raise ValueError("contact-force scale range is invalid")


@dataclass(frozen=True)
class TrainingConfig:
    adapter: str = "synthetic"
    seed: int = 42
    population: int = 12
    elite_count: int = 3
    episodes_per_candidate: int = 4
    max_steps: int = 480
    residual_limit_m: float = 0.004
    max_fingertip_step_m: float = 0.006
    max_contact_force_n: float = 8.0
    minimum_contact_force_n: float = 0.05
    minimum_key_travel_m: float = 0.002
    max_base_tilt_rad: float = 0.18
    max_foot_slip_m: float = 0.02
    minimum_hand_separation_m: float = 0.025
    initial_std: float = 0.16
    minimum_std: float = 0.02
    curriculum_pass_rate: float = 0.8
    curriculum_evaluation_episodes: int = 5
    timestep_s: float = 1 / 60
    version: str = CONTRACT_VERSION

    def __post_init__(self) -> None:
        integer_fields = (
            "seed", "population", "elite_count", "episodes_per_candidate",
            "max_steps", "curriculum_evaluation_episodes",
        )
        for name in integer_fields:
            value = getattr(self, name)
            if type(value) is not int or value < (0 if name == "seed" else 1):
                raise ValueError(f"{name} must be an appropriate nonnegative/positive integer")
        if self.seed >= 1_000_000_000:
            raise ValueError("seed must be smaller than 1000000000 for split isolation")
        if not 1 <= self.elite_count < self.population:
            raise ValueError("elite_count must be smaller than population")
        for name, value in asdict(self).items():
            if name in integer_fields or name in {"adapter", "version"}:
                continue
            if type(value) not in {int, float} or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        if not isinstance(self.adapter, str) or not self.adapter:
            raise ValueError("adapter must be a nonempty string")
        if self.curriculum_pass_rate > 1:
            raise ValueError("curriculum pass rate must be in (0, 1]")
        if self.minimum_contact_force_n >= self.max_contact_force_n:
            raise ValueError("contact-force thresholds are inverted")
        if self.minimum_std > self.initial_std:
            raise ValueError("minimum_std exceeds initial_std")
        if self.residual_limit_m > MAX_CARTESIAN_RESIDUAL_M:
            raise ValueError("residual_limit_m exceeds the fixed independent 4 mm safety ceiling")
        if self.version != CONTRACT_VERSION:
            raise ValueError("unsupported training contract version")


@dataclass(frozen=True)
class CartesianResidual:
    """Offsets from controller-owned waypoints; never joint commands."""

    left_xyz_m: Vec3 = (0.0, 0.0, 0.0)
    right_xyz_m: Vec3 = (0.0, 0.0, 0.0)

    def validated(self, limit_m: float) -> CartesianResidual:
        for vector in (self.left_xyz_m, self.right_xyz_m):
            if len(vector) != 3 or not all(math.isfinite(x) for x in vector):
                raise ValueError("residual must contain finite Cartesian coordinates")
            if math.dist(vector, (0, 0, 0)) > limit_m + 1e-12:
                raise ValueError("residual exceeds independent safety bound")
        return self


@dataclass(frozen=True)
class ContactEvidence:
    key: str
    hand: str
    finger: str
    normal_force_n: float
    key_travel_m: float


@dataclass(frozen=True)
class KeyEvent:
    key: str
    event_type: str  # keydown / keyup, from keyboard sensor edge detection


@dataclass(frozen=True)
class Observation:
    tick: int
    fingertip_xyz_m: tuple[Vec3, Vec3]
    waypoint_xyz_m: tuple[Vec3, Vec3]
    contacts: tuple[ContactEvidence, ...]
    key_events: tuple[KeyEvent, ...]
    pressed_keys: tuple[str, ...]
    screen_text: str
    standing: bool = True
    base_tilt_rad: float = 0.0
    foot_slip_m: float = 0.0
    forbidden_collision: bool = False


class TypingEnvironment(Protocol):
    """An optional physics adapter owns sensing, IK, and independent safety.

    Factories are loaded with ``--adapter package.module:factory`` and called
    as ``factory(config=TrainingConfig)``. A reset must provide tick 0, empty
    event history and no pressed keys. Steps advance exactly one control tick.
    """

    backend_name: str
    evidence_kind: str  # "synthetic_non_physics" or "physics_sensor"

    def reset(
        self, *, goal: TypingGoal, seed: int, randomization: DomainRandomization
    ) -> Observation: ...

    def step(self, action: CartesianResidual) -> Observation: ...

    def emergency_release(self) -> Observation:
        """Bounded controller-owned release; return actual final sensor evidence."""
        ...

    def close(self) -> None: ...
