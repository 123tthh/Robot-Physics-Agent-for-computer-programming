"""Keyboard geometry, Dex3 semantics, keystroke planning, and repair logic.

This module deliberately emits fingertip/key objectives rather than body joint
commands.  A simulation, IK, residual-policy, or DDS adapter may implement the
same objectives without exposing low-level control to the Agent.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import IntEnum, StrEnum
from typing import Iterable


class HandSide(StrEnum):
    LEFT = "left"
    RIGHT = "right"


class Finger(StrEnum):
    THUMB = "thumb"
    INDEX = "index"
    MIDDLE = "middle"


class KeyRole(StrEnum):
    MODIFIER = "modifier"
    PRIMARY = "primary"


class PlannedKeyEventType(StrEnum):
    KEYDOWN = "keydown"
    KEYUP = "keyup"


class Dex3Joint(IntEnum):
    """Per-hand seven-axis semantic order: thumb 3, index 2, middle 2."""

    THUMB_YAW = 0
    THUMB_PITCH = 1
    THUMB_DISTAL = 2
    INDEX_PROXIMAL = 3
    INDEX_DISTAL = 4
    MIDDLE_PROXIMAL = 5
    MIDDLE_DISTAL = 6


@dataclass(frozen=True, slots=True)
class KeyPose:
    key: str
    x_m: float
    y_m: float
    z_m: float
    yaw_rad: float


@dataclass(frozen=True, slots=True)
class KeyboardCalibration:
    """Planar keyboard frame and regular key pitch approximation."""

    origin_xyz_m: tuple[float, float, float]
    yaw_rad: float = 0.0
    key_pitch_x_m: float = 0.01905
    key_pitch_y_m: float = 0.01905

    def __post_init__(self) -> None:
        if len(self.origin_xyz_m) != 3:
            raise ValueError("keyboard origin must contain x, y, z")
        if self.key_pitch_x_m <= 0 or self.key_pitch_y_m <= 0:
            raise ValueError("keyboard key pitches must be positive")

    def key_pose(self, key: str) -> KeyPose:
        normalized = normalize_key(key)
        if normalized not in KEY_COORDINATES:
            raise KeyError(f"keyboard geometry is unavailable for key {key!r}")
        column, row = KEY_COORDINATES[normalized]
        local_x = column * self.key_pitch_x_m
        local_y = -row * self.key_pitch_y_m
        cosine = math.cos(self.yaw_rad)
        sine = math.sin(self.yaw_rad)
        origin_x, origin_y, origin_z = self.origin_xyz_m
        return KeyPose(
            key=normalized,
            x_m=origin_x + cosine * local_x - sine * local_y,
            y_m=origin_y + sine * local_x + cosine * local_y,
            z_m=origin_z,
            yaw_rad=self.yaw_rad,
        )


@dataclass(frozen=True, slots=True)
class Keystroke:
    key: str
    modifiers: tuple[str, ...]
    hand: HandSide
    finger: Finger
    expected_text: str


@dataclass(frozen=True, slots=True)
class KeyAssignment:
    """One physical key objective assigned to a semantic Dex3 executor."""

    logical_key: str
    physical_key: str
    role: KeyRole
    hand: HandSide
    finger: Finger

    @property
    def executor(self) -> str:
        return f"{self.hand.value}.{self.finger.value}"


@dataclass(frozen=True, slots=True)
class ExpectedKeyEvent:
    event_type: PlannedKeyEventType
    key: str
    modifiers: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ChordPlan:
    """Time-ordered multi-finger chord contract without joint commands."""

    logical_keys: tuple[str, ...]
    assignments: tuple[KeyAssignment, ...]
    press_order: tuple[str, ...]
    release_order: tuple[str, ...]
    held_while_primary: tuple[str, ...]
    expected_events: tuple[ExpectedKeyEvent, ...]
    maximum_duration_seconds: float = 3.0
    version: str = "1.0.0"

    def __post_init__(self) -> None:
        if not self.logical_keys or not self.assignments:
            raise ValueError("chord plan cannot be empty")
        if (
            not math.isfinite(self.maximum_duration_seconds)
            or self.maximum_duration_seconds <= 0
        ):
            raise ValueError("chord maximum duration must be finite and positive")
        keys = tuple(item.physical_key for item in self.assignments)
        if len(keys) != len(set(keys)):
            raise ValueError("chord plan assigns a physical key more than once")
        executors = tuple(item.executor for item in self.assignments)
        if len(executors) != len(set(executors)):
            raise FingerConflictError("chord plan assigns overlapping keys to one finger")
        primary = [item for item in self.assignments if item.role is KeyRole.PRIMARY]
        if len(primary) != 1:
            raise NoPrimaryKeyError("chord plan must contain exactly one primary key")
        if set(self.press_order) != set(keys) or set(self.release_order) != set(keys):
            raise ValueError("press and release order must contain every assigned key once")
        modifiers = {
            item.physical_key
            for item in self.assignments
            if item.role is KeyRole.MODIFIER
        }
        if set(self.held_while_primary) != modifiers:
            raise ValueError("held_while_primary must contain every modifier")

    def assignment_for(self, physical_key: str) -> KeyAssignment:
        normalized = normalize_key(physical_key)
        for assignment in self.assignments:
            if assignment.physical_key == normalized:
                return assignment
        raise KeyError(f"key is not assigned in chord: {physical_key}")

    @property
    def contains_joint_commands(self) -> bool:
        return False


class ChordPlanningError(ValueError):
    code = "CHORD_PLAN_INVALID"


class NoPrimaryKeyError(ChordPlanningError):
    code = "NO_PRIMARY_KEY"


class MultiplePrimaryKeysError(ChordPlanningError):
    code = "MULTIPLE_PRIMARY_KEYS"


class UnreachableKeyError(ChordPlanningError):
    code = "UNREACHABLE_KEY"


class FingerConflictError(ChordPlanningError):
    code = "FINGER_CONFLICT"


@dataclass(frozen=True, slots=True)
class TypingPlan:
    text: str
    strokes: tuple[Keystroke, ...]
    strategy: str = "two-hand-three-finger"


@dataclass(frozen=True, slots=True)
class TypingRepair:
    expected: str
    observed: str
    common_prefix_length: int
    backspaces: int
    replacement: TypingPlan


UNSHIFTED_ROWS = (
    "1234567890-=",
    "qwertyuiop[]\\",
    "asdfghjkl;'",
    "zxcvbnm,./",
)

SHIFTED = {
    "!": "1",
    "@": "2",
    "#": "3",
    "$": "4",
    "%": "5",
    "^": "6",
    "&": "7",
    "*": "8",
    "(": "9",
    ")": "0",
    "_": "-",
    "+": "=",
    "{": "[",
    "}": "]",
    "|": "\\",
    ":": ";",
    '"': "'",
    "<": ",",
    ">": ".",
    "?": "/",
}

KEY_COORDINATES: dict[str, tuple[float, float]] = {
    key: (column + offset, float(row))
    for row, (keys, offset) in enumerate(
        zip(UNSHIFTED_ROWS, (0.0, 0.25, 0.5, 0.75), strict=True)
    )
    for column, key in enumerate(keys)
}
KEY_COORDINATES.update(
    {
        "tab": (-0.75, 1.0),
        "capslock": (-0.75, 2.0),
        "left_shift": (-0.75, 3.0),
        "left_ctrl": (-0.75, 4.0),
        "left_alt": (2.25, 4.0),
        "space": (5.0, 4.0),
        "right_alt": (8.0, 4.0),
        "right_shift": (11.0, 3.0),
        "right_ctrl": (11.0, 4.0),
        "enter": (12.5, 2.0),
        "backspace": (12.5, 0.0),
    }
)

LEFT_KEYS = set("12345qwertasdfgzxcvb") | {
    "tab",
    "capslock",
    "left_shift",
    "left_ctrl",
    "left_alt",
}


def normalize_key(key: str) -> str:
    aliases = {
        "ctrl": "left_ctrl",
        "control": "left_ctrl",
        "alt": "left_alt",
        "shift": "left_shift",
        " ": "space",
        "\n": "enter",
        "\b": "backspace",
    }
    return aliases.get(key.lower(), key.lower())


class TypingPlanner:
    """Converts terminal text into inspectable, keyboard-event-level plans."""

    def plan_text(self, text: str) -> TypingPlan:
        strokes = tuple(self._stroke_for_character(character) for character in text)
        return TypingPlan(text=text, strokes=strokes)

    def plan_chord(self, keys: tuple[str, ...] | list[str]) -> tuple[Keystroke, ...]:
        """Return the legacy primary-key objective used by P0/P1 skills.

        New physical executors should use :meth:`plan_chord_detailed`, which
        assigns every modifier independently and carries an expected timeline.
        """

        if not keys:
            raise ValueError("keyboard chord cannot be empty")
        normalized = tuple(normalize_key(key) for key in keys)
        modifiers = tuple(
            key for key in normalized if key.endswith(("ctrl", "alt", "shift"))
        )
        primary = [key for key in normalized if key not in modifiers]
        if len(primary) != 1:
            raise ValueError("a chord must have exactly one non-modifier key")
        key = primary[0]
        hand, finger = self._hand_and_finger(key)
        return (
            Keystroke(
                key=key,
                modifiers=modifiers,
                hand=hand,
                finger=finger,
                expected_text="",
            ),
        )

    def plan_chord_detailed(
        self,
        keys: tuple[str, ...] | list[str],
        *,
        reachable_keys: Iterable[str] | None = None,
        maximum_duration_seconds: float = 3.0,
    ) -> ChordPlan:
        """Create a bimanual Dex3 chord plan with unique finger ownership."""

        if not keys:
            raise NoPrimaryKeyError("keyboard chord cannot be empty")
        logical = tuple(key.lower() for key in keys)
        normalized = tuple(normalize_key(key) for key in logical)
        modifier_indexes = tuple(
            index for index, key in enumerate(normalized) if _is_modifier(key)
        )
        primary_indexes = tuple(
            index for index in range(len(normalized)) if index not in modifier_indexes
        )
        if not primary_indexes:
            raise NoPrimaryKeyError("a chord requires one non-modifier key")
        if len(primary_indexes) > 1:
            raise MultiplePrimaryKeysError("a chord cannot contain multiple primary keys")

        primary_key = normalized[primary_indexes[0]]
        if primary_key not in KEY_COORDINATES:
            raise UnreachableKeyError(
                f"keyboard geometry is unavailable for {primary_key!r}"
            )
        reachable = (
            set(KEY_COORDINATES)
            if reachable_keys is None
            else {normalize_key(key) for key in reachable_keys}
        )
        if primary_key not in reachable:
            raise UnreachableKeyError(
                f"primary key is outside the reachable set: {primary_key}"
            )

        # Logical modifier aliases may select either physical side. Prefer the
        # side opposite the primary key to make the chord genuinely bimanual.
        primary_hand, _ = self._hand_and_finger(primary_key)
        physical_modifiers: list[tuple[str, str]] = []
        for index in modifier_indexes:
            logical_key = logical[index]
            candidates = _physical_modifier_candidates(logical_key, primary_hand)
            selected = next((key for key in candidates if key in reachable), None)
            if selected is None:
                raise UnreachableKeyError(
                    f"no reachable physical key is available for modifier {logical_key!r}"
                )
            physical_modifiers.append((logical_key, selected))

        occupied: set[tuple[HandSide, Finger]] = set()
        hand_load = {HandSide.LEFT: 0, HandSide.RIGHT: 0}
        assignments: list[KeyAssignment] = []
        for logical_key, physical_key in physical_modifiers:
            hand, finger = self._allocate_finger(
                physical_key, occupied=occupied, hand_load=hand_load
            )
            assignments.append(
                KeyAssignment(
                    logical_key=logical_key,
                    physical_key=physical_key,
                    role=KeyRole.MODIFIER,
                    hand=hand,
                    finger=finger,
                )
            )
            occupied.add((hand, finger))
            hand_load[hand] += 1

        hand, finger = self._allocate_finger(
            primary_key, occupied=occupied, hand_load=hand_load
        )
        assignments.append(
            KeyAssignment(
                logical_key=logical[primary_indexes[0]],
                physical_key=primary_key,
                role=KeyRole.PRIMARY,
                hand=hand,
                finger=finger,
            )
        )

        modifiers = tuple(item.physical_key for item in assignments[:-1])
        press_order = (*modifiers, primary_key)
        release_order = (primary_key, *reversed(modifiers))
        return ChordPlan(
            logical_keys=logical,
            assignments=tuple(assignments),
            press_order=press_order,
            release_order=release_order,
            held_while_primary=modifiers,
            expected_events=_expected_chord_events(modifiers, primary_key),
            maximum_duration_seconds=maximum_duration_seconds,
        )

    def repair(self, *, expected: str, observed: str) -> TypingRepair:
        prefix = 0
        for wanted, actual in zip(expected, observed, strict=False):
            if wanted != actual:
                break
            prefix += 1
        backspaces = len(observed) - prefix
        replacement = self.plan_text(expected[prefix:])
        return TypingRepair(
            expected=expected,
            observed=observed,
            common_prefix_length=prefix,
            backspaces=backspaces,
            replacement=replacement,
        )

    def _stroke_for_character(self, character: str) -> Keystroke:
        if character == "\n":
            key = "enter"
            modifiers: tuple[str, ...] = ()
        elif character == " ":
            key = "space"
            modifiers = ()
        elif character in SHIFTED:
            key = SHIFTED[character]
            modifiers = ("left_shift",)
        elif character.isalpha() and character.isupper():
            key = character.lower()
            modifiers = ("left_shift",)
        else:
            key = character.lower()
            modifiers = ()
        if key not in KEY_COORDINATES:
            raise ValueError(f"unsupported physical keyboard character: {character!r}")
        hand, finger = self._hand_and_finger(key)
        return Keystroke(
            key=key,
            modifiers=modifiers,
            hand=hand,
            finger=finger,
            expected_text=character,
        )

    @staticmethod
    def _hand_and_finger(key: str) -> tuple[HandSide, Finger]:
        normalized = normalize_key(key)
        if normalized == "space":
            return HandSide.RIGHT, Finger.THUMB
        hand = HandSide.LEFT if normalized in LEFT_KEYS else HandSide.RIGHT
        coordinate = KEY_COORDINATES.get(normalized, (6.0, 0.0))[0]
        if hand is HandSide.LEFT:
            finger = Finger.MIDDLE if coordinate < 2.5 else Finger.INDEX
        else:
            finger = Finger.MIDDLE if coordinate > 8.5 else Finger.INDEX
        return hand, finger

    @staticmethod
    def _allocate_finger(
        key: str,
        *,
        occupied: set[tuple[HandSide, Finger]],
        hand_load: dict[HandSide, int],
    ) -> tuple[HandSide, Finger]:
        candidates = _finger_candidates(key)
        available = [candidate for candidate in candidates if candidate not in occupied]
        if not available:
            raise FingerConflictError(
                f"no independent Dex3 finger is available for {key}"
            )
        return min(
            available,
            key=lambda candidate: (
                hand_load[candidate[0]],
                candidates.index(candidate),
            ),
        )


def _is_modifier(key: str) -> bool:
    return key in {
        "left_ctrl",
        "right_ctrl",
        "left_alt",
        "right_alt",
        "left_shift",
        "right_shift",
    }


def _physical_modifier_candidates(
    logical_key: str, primary_hand: HandSide
) -> tuple[str, ...]:
    lowered = logical_key.lower()
    families = {
        "ctrl": ("left_ctrl", "right_ctrl"),
        "control": ("left_ctrl", "right_ctrl"),
        "alt": ("left_alt", "right_alt"),
        "shift": ("left_shift", "right_shift"),
    }
    if lowered in families:
        left, right = families[lowered]
        return (right, left) if primary_hand is HandSide.LEFT else (left, right)
    return (normalize_key(lowered),)


def _finger_candidates(key: str) -> tuple[tuple[HandSide, Finger], ...]:
    normalized = normalize_key(key)
    explicit = {
        "left_ctrl": (
            (HandSide.LEFT, Finger.MIDDLE),
            (HandSide.LEFT, Finger.INDEX),
            (HandSide.LEFT, Finger.THUMB),
        ),
        "right_ctrl": (
            (HandSide.RIGHT, Finger.MIDDLE),
            (HandSide.RIGHT, Finger.INDEX),
            (HandSide.RIGHT, Finger.THUMB),
        ),
        "left_alt": (
            (HandSide.LEFT, Finger.THUMB),
            (HandSide.LEFT, Finger.INDEX),
            (HandSide.LEFT, Finger.MIDDLE),
        ),
        "right_alt": (
            (HandSide.RIGHT, Finger.THUMB),
            (HandSide.RIGHT, Finger.INDEX),
            (HandSide.RIGHT, Finger.MIDDLE),
        ),
        "left_shift": (
            (HandSide.LEFT, Finger.MIDDLE),
            (HandSide.LEFT, Finger.INDEX),
            (HandSide.LEFT, Finger.THUMB),
        ),
        "right_shift": (
            (HandSide.RIGHT, Finger.MIDDLE),
            (HandSide.RIGHT, Finger.INDEX),
            (HandSide.RIGHT, Finger.THUMB),
        ),
    }
    if normalized in explicit:
        return explicit[normalized]
    preferred_hand, preferred_finger = TypingPlanner._hand_and_finger(normalized)
    other_hand = HandSide.RIGHT if preferred_hand is HandSide.LEFT else HandSide.LEFT
    same_hand_alternates = tuple(
        (preferred_hand, finger)
        for finger in (Finger.INDEX, Finger.MIDDLE, Finger.THUMB)
        if finger is not preferred_finger
    )
    return (
        (preferred_hand, preferred_finger),
        *same_hand_alternates,
        (other_hand, Finger.INDEX),
        (other_hand, Finger.MIDDLE),
        (other_hand, Finger.THUMB),
    )


def _expected_chord_events(
    modifiers: tuple[str, ...], primary: str
) -> tuple[ExpectedKeyEvent, ...]:
    events: list[ExpectedKeyEvent] = []
    active: set[str] = set()
    for key in modifiers:
        active.add(key)
        events.append(
            ExpectedKeyEvent(
                PlannedKeyEventType.KEYDOWN,
                key,
                tuple(sorted(active)),
            )
        )
    events.append(
        ExpectedKeyEvent(
            PlannedKeyEventType.KEYDOWN,
            primary,
            tuple(sorted(active)),
        )
    )
    events.append(
        ExpectedKeyEvent(
            PlannedKeyEventType.KEYUP,
            primary,
            tuple(sorted(active)),
        )
    )
    for key in reversed(modifiers):
        active.discard(key)
        events.append(
            ExpectedKeyEvent(
                PlannedKeyEventType.KEYUP,
                key,
                tuple(sorted(active)),
            )
        )
    return tuple(events)


def verify_typed_text(*, expected: str, key_events: str, screen_text: str) -> bool:
    """Require agreement between the keyboard event stream and screen OCR."""

    return key_events == expected and screen_text.endswith(expected)
