"""Explicitly non-physics CPU adapter plus a lazy physics-adapter factory hook."""

from __future__ import annotations

import importlib
import math
import random

from vskill_os.standing_typing import PARK_POSITIONS, keyboard_positions
from vskill_os.typing import SHIFTED

from .contracts import (
    HAND_NAMES, CartesianResidual, ContactEvidence, DomainRandomization,
    KeyEvent, Observation, TrainingConfig, TypingEnvironment, TypingGoal, Vec3,
)


class SyntheticTypingEnvironment:
    """A deterministic kinematic fixture, NOT Isaac or a robot dynamics model.

    Contact values are geometric synthetic evidence, useful only for testing the
    trainer, curriculum, policy checkpoint, and independent verifier plumbing.
    """

    backend_name = "cpu_synthetic_index_typing"
    evidence_kind = "synthetic_non_physics"

    def __init__(self, config: TrainingConfig) -> None:
        self.config = config
        self._has_reset = False

    def reset(self, *, goal: TypingGoal, seed: int,
              randomization: DomainRandomization) -> Observation:
        rng = random.Random(seed)
        self.goal = goal
        shift = tuple(rng.uniform(-randomization.keyboard_translation_m,
                                  randomization.keyboard_translation_m) for _ in range(3))
        self.keys = {key: tuple(point[i] + shift[i] for i in range(3))
                     for key, point in keyboard_positions().items()}
        self.bias = tuple(tuple(rng.uniform(-randomization.controller_bias_m,
                                           randomization.controller_bias_m)
                                for _ in range(3)) for _ in HAND_NAMES)
        self.gain = rng.uniform(randomization.tracking_gain_min, randomization.tracking_gain_max)
        self.force_scale = rng.uniform(randomization.contact_force_scale_min,
                                       randomization.contact_force_scale_max)
        self.positions: tuple[Vec3, Vec3] = tuple(PARK_POSITIONS[hand] for hand in HAND_NAMES)
        self.parked = self.positions
        self.phases: list[tuple[tuple[Vec3, Vec3], set[str]]] = []
        groups = [[stroke for stroke in goal.strokes if stroke.wave == wave]
                  for wave in dict.fromkeys(stroke.wave for stroke in goal.strokes)]
        for group in groups:
            approach = list(self.parked)
            for stroke in group:
                hand = HAND_NAMES.index(stroke.primary_hand)
                point = self.keys[stroke.key]
                approach[hand] = (point[0], point[1], point[2] + 0.012)
                if stroke.shift_hand and stroke.shift_key:
                    other = HAND_NAMES.index(stroke.shift_hand)
                    modifier = self.keys[stroke.shift_key]
                    approach[other] = (modifier[0], modifier[1], modifier[2] + 0.012)
            self.phases.append((tuple(approach), set()))
            for stroke in group:
                hand = HAND_NAMES.index(stroke.primary_hand)
                point = self.keys[stroke.key]
                pressed = list(approach)
                held: set[str] = set()
                if stroke.shift_hand and stroke.shift_key:
                    other = HAND_NAMES.index(stroke.shift_hand)
                    modifier = self.keys[stroke.shift_key]
                    pressed[other] = (modifier[0], modifier[1], modifier[2] - 0.0034)
                    held.add(stroke.shift_key)
                    self.phases.append((tuple(pressed), set(held)))
                pressed[hand] = (point[0], point[1], point[2] - 0.0034)
                self.phases.append((tuple(pressed), held | {stroke.key}))
                released = list(pressed)
                released[hand] = approach[hand]
                self.phases.append((tuple(released), set(held)))
                if held:
                    self.phases.append((tuple(approach), set()))
        self.phase = 0
        self.tick = 0
        self.screen = ""
        self.pressed: set[str] = set()
        self._has_reset = True
        return self._observation((), ())

    def step(self, action: CartesianResidual) -> Observation:
        if not self._has_reset:
            raise RuntimeError("reset must precede step")
        action.validated(self.config.residual_limit_m)
        target, target_pressed = self.phases[min(self.phase, len(self.phases) - 1)]
        positions = []
        for i, residual in enumerate((action.left_xyz_m, action.right_xyz_m)):
            error = tuple((target[i][j] + residual[j] + self.bias[i][j] - self.positions[i][j])
                          * self.gain for j in range(3))
            length = math.dist(error, (0, 0, 0))
            scale = min(1.0, self.config.max_fingertip_step_m / max(length, 1e-12))
            positions.append(tuple(self.positions[i][j] + error[j] * scale for j in range(3)))
        self.positions = tuple(positions)
        contacts, events = self._read_sensors()
        self.tick += 1
        if self.pressed == target_pressed and all(math.dist(self.positions[i], target[i]) < 0.006
                                                 for i in range(2)):
            self.phase = min(self.phase + 1, len(self.phases) - 1)
        return self._observation(contacts, events)

    def _read_sensors(self) -> tuple[tuple[ContactEvidence, ...], tuple[KeyEvent, ...]]:
        contacts = []
        new_pressed = set()
        for hand, point in zip(HAND_NAMES, self.positions, strict=True):
            nearest = min(self.keys, key=lambda key: math.dist(point[:2], self.keys[key][:2]))
            key_position = self.keys[nearest]
            travel = key_position[2] - point[2]
            if math.dist(point[:2], key_position[:2]) < 0.006 and travel > 0:
                contacts.append(ContactEvidence(nearest, hand, "index", travel * 800 * self.force_scale, travel))
                if travel >= self.config.minimum_key_travel_m:
                    new_pressed.add(nearest)
        # Hysteresis: a previously pressed key remains down until the associated
        # fingertip clears the surface; keyup is not inferred from low force.
        for key in self.pressed:
            point = self.keys[key]
            if any(math.dist(position[:2], point[:2]) < 0.006 and position[2] < point[2] + 0.003
                   for position in self.positions):
                new_pressed.add(key)
        events = [KeyEvent(key, "keyup") for key in sorted(self.pressed - new_pressed)]
        events += [KeyEvent(key, "keydown") for key in sorted(new_pressed - self.pressed)]
        for event in events:
            if event.event_type == "keydown" and not event.key.endswith("_shift"):
                shifted = bool(new_pressed & {"left_shift", "right_shift"})
                char = " " if event.key == "space" else event.key
                if shifted:
                    char = {value: key for key, value in SHIFTED.items()}.get(char, char.upper())
                self.screen += char
        self.pressed = new_pressed
        return tuple(contacts), tuple(events)

    def emergency_release(self) -> Observation:
        if not self._has_reset:
            raise RuntimeError("cannot obtain release evidence before reset")
        clearance_z = max(point[2] for point in self.keys.values()) + 0.015
        observation = self._observation((), ())
        for _ in range(40):
            self.positions = tuple((point[0], point[1], point[2] + min(
                self.config.max_fingertip_step_m, max(0.0, clearance_z - point[2])))
                for point in self.positions)
            contacts, events = self._read_sensors()
            self.tick += 1
            observation = self._observation(contacts, events)
            if not contacts and not self.pressed and all(point[2] >= clearance_z for point in self.positions):
                return observation
        return observation

    def _observation(self, contacts: tuple[ContactEvidence, ...],
                     events: tuple[KeyEvent, ...]) -> Observation:
        return Observation(self.tick, self.positions, self.phases[self.phase][0], contacts,
                           events, tuple(sorted(self.pressed)), self.screen)

    def close(self) -> None:
        self._has_reset = False


def make_environment(config: TrainingConfig) -> TypingEnvironment:
    if config.adapter == "synthetic":
        return SyntheticTypingEnvironment(config)
    module_name, separator, factory_name = config.adapter.partition(":")
    if not separator or not module_name or not factory_name:
        raise ValueError("adapter must be 'synthetic' or an importable module:factory")
    factory = getattr(importlib.import_module(module_name), factory_name)
    environment = factory(config=config)
    for name in ("reset", "step", "emergency_release", "close", "backend_name", "evidence_kind"):
        if not hasattr(environment, name):
            raise TypeError(f"environment adapter lacks required member {name!r}")
    if environment.evidence_kind not in {"synthetic_non_physics", "physics_sensor"}:
        raise ValueError("adapter must explicitly classify its evidence kind")
    return environment
