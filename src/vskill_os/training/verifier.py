"""Independent sensor/event verification and safety termination for training."""

from __future__ import annotations

import math
from dataclasses import dataclass

from .contracts import HAND_NAMES, Observation, TrainingConfig, TypingGoal


@dataclass(frozen=True)
class Verification:
    complete: bool
    failure_code: str | None
    accepted_events: int
    expected_events: int
    verified_characters: int


class TrainingVerifier:
    """Neither an environment's completion claim nor a reward implies success."""

    def __init__(self, goal: TypingGoal, config: TrainingConfig) -> None:
        self.goal = goal
        self.config = config
        self.expected: list[tuple[str, str, str]] = []
        for stroke in goal.strokes:
            if stroke.shift_key and stroke.shift_hand:
                self.expected.append(("keydown", stroke.shift_key, stroke.shift_hand))
            self.expected.extend((
                ("keydown", stroke.key, stroke.primary_hand),
                ("keyup", stroke.key, stroke.primary_hand),
            ))
            if stroke.shift_key and stroke.shift_hand:
                self.expected.append(("keyup", stroke.shift_key, stroke.shift_hand))
        self.accepted = 0
        self.verified_characters = 0
        self.last_tick = -1
        self.held: set[str] = set()
        self.failure_code: str | None = None
        self.last_positions: tuple | None = None

    def update(self, observation: Observation) -> Verification:
        if self.failure_code:
            return self.result(False)
        self.failure_code = self._safety_failure(observation)
        if self.failure_code:
            return self.result(False)
        if self.last_tick == -1 and (observation.key_events or observation.pressed_keys or observation.screen_text):
            self.failure_code = "RESET_NOT_CLEAR"
            return self.result(False)
        if observation.tick != self.last_tick + 1:
            self.failure_code = "STALE_OR_SKIPPED_OBSERVATION"
            return self.result(False)
        self.last_tick = observation.tick
        if self.last_positions is not None and any(
            math.dist(previous, current) > self.config.max_fingertip_step_m + 1e-9
            for previous, current in zip(self.last_positions, observation.fingertip_xyz_m, strict=True)
        ):
            self.failure_code = "FINGERTIP_VELOCITY_LIMIT"
            return self.result(False)
        self.last_positions = observation.fingertip_xyz_m
        for event in observation.key_events:
            if self.accepted >= len(self.expected):
                self.failure_code = "UNEXPECTED_KEY_EVENT"
                break
            event_type, key, hand = self.expected[self.accepted]
            if (event.event_type, event.key) != (event_type, key):
                self.failure_code = "KEY_EVENT_ORDER_MISMATCH"
                break
            if event_type == "keydown":
                contact = next((contact for contact in observation.contacts
                                if contact.key == key and contact.hand == hand
                                and contact.finger == "index"
                                and contact.normal_force_n >= self.config.minimum_contact_force_n
                                and contact.key_travel_m >= self.config.minimum_key_travel_m), None)
                if contact is None or key not in observation.pressed_keys or key in self.held:
                    self.failure_code = "KEYDOWN_WITHOUT_INDEX_CONTACT"
                    break
                self.held.add(key)
                if not key.endswith("_shift"):
                    self.verified_characters += 1
            else:
                if key not in self.held or key in observation.pressed_keys:
                    self.failure_code = "KEYUP_WITHOUT_RELEASE"
                    break
                if any(contact.key == key for contact in observation.contacts):
                    self.failure_code = "KEYUP_WITHOUT_PHYSICAL_RELEASE"
                    break
                self.held.remove(key)
            self.accepted += 1
        if not self.failure_code and self.held != set(observation.pressed_keys):
            self.failure_code = "PRESSED_KEY_STATE_MISMATCH"
        # Every emitted character must agree immediately with independent display
        # evidence. Completion also requires modifier release and full event order.
        expected_prefix = self.goal.text[:self.verified_characters]
        if not self.failure_code and observation.screen_text != expected_prefix:
            self.failure_code = "DISPLAY_TEXT_MISMATCH"
        complete = (
            not self.failure_code and self.accepted == len(self.expected)
            and not self.held and not observation.contacts
            and observation.screen_text == self.goal.text
        )
        return self.result(complete)

    def _safety_failure(self, observation: Observation) -> str | None:
        if type(observation.tick) is not int or observation.tick < 0:
            return "INVALID_OBSERVATION"
        vectors = (*observation.fingertip_xyz_m, *observation.waypoint_xyz_m)
        if len(observation.fingertip_xyz_m) != 2 or len(observation.waypoint_xyz_m) != 2:
            return "INVALID_OBSERVATION"
        if any(len(vector) != 3 or not all(math.isfinite(x) for x in vector) for vector in vectors):
            return "INVALID_OBSERVATION"
        if not math.isfinite(observation.base_tilt_rad) or not math.isfinite(observation.foot_slip_m):
            return "INVALID_OBSERVATION"
        if not observation.standing or abs(observation.base_tilt_rad) > self.config.max_base_tilt_rad:
            return "STANDING_STABILITY_LOST"
        if abs(observation.foot_slip_m) > self.config.max_foot_slip_m:
            return "FOOT_SLIP"
        if observation.forbidden_collision:
            return "FORBIDDEN_COLLISION"
        if math.dist(*observation.fingertip_xyz_m) < self.config.minimum_hand_separation_m:
            return "HAND_SEPARATION_VIOLATION"
        if len(observation.pressed_keys) != len(set(observation.pressed_keys)):
            return "INVALID_OBSERVATION"
        for contact in observation.contacts:
            if (contact.hand not in HAND_NAMES or contact.finger != "index"
                    or not math.isfinite(contact.normal_force_n)
                    or not math.isfinite(contact.key_travel_m)
                    or contact.normal_force_n < 0 or contact.key_travel_m < 0):
                return "INVALID_OR_NON_INDEX_CONTACT"
            if contact.normal_force_n > self.config.max_contact_force_n:
                return "CONTACT_FORCE_LIMIT"
        return None

    def result(self, complete: bool) -> Verification:
        return Verification(complete, self.failure_code, self.accepted,
                            len(self.expected), self.verified_characters)
