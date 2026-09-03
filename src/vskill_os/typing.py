"""Keyboard geometry, Dex3 semantics, keystroke planning, and repair logic.

This module deliberately emits fingertip/key objectives rather than body joint
commands.  A simulation, IK, residual-policy, or DDS adapter may implement the
same objectives without exposing low-level control to the Agent.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import IntEnum, StrEnum


class HandSide(StrEnum):
    LEFT = "left"
    RIGHT = "right"


class Finger(StrEnum):
    THUMB = "thumb"
    INDEX = "index"
    MIDDLE = "middle"


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


def verify_typed_text(*, expected: str, key_events: str, screen_text: str) -> bool:
    """Require agreement between the keyboard event stream and screen OCR."""

    return key_events == expected and screen_text.endswith(expected)

