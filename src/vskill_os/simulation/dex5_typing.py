"""Portable Dex5-1 typing objectives and an independent observation verifier.

The replay backend is a deterministic contract test, never Isaac evidence.
Adapters own motion/IK and supply measured observations; the planner contains
no body or finger joint targets. An assisted seated fixture is deliberately
distinct from an independently balanced sit-down controller.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from enum import StrEnum
import json
import math
from pathlib import Path
from typing import Iterable

from ..typing import HandSide, KEY_COORDINATES
from .keyboard_bridge import KeycapSample, KeyboardEventBridge


DEFAULT_DEMO_TEXT = "asdf jkl; "
CONTRACT_VERSION = "1.0.0"
# Portable monitor contract for the dedicated 74-key workstation. Keyboard
# geometry remains adapter-owned; the verifier must also see unplanned keys.
DEX5_MONITORED_KEYS = tuple(sorted(
    set(KEY_COORDINATES)
    | {"`", "delete", "escape", "left_super", "menu"}
    | {f"f{number}" for number in range(1, 13)}
))


class Dex5Finger(StrEnum):
    THUMB = "thumb"
    INDEX = "index"
    MIDDLE = "middle"
    RING = "ring"
    LITTLE = "little"


class Dex5Phase(StrEnum):
    SEATED = "seated"
    HOVER = "hover"
    PRESS = "press"
    RELEASE = "release"


class Dex5Failure(StrEnum):
    INVALID_EVIDENCE = "INVALID_EVIDENCE"
    UNSUPPORTED_SOURCE = "UNSUPPORTED_SOURCE"
    SEATED_PRECONDITION = "SEATED_PRECONDITION"
    TIMEOUT = "TIMEOUT"
    PHASE_ORDER = "PHASE_ORDER"
    MISSING_OBSERVATION = "MISSING_OBSERVATION"
    NO_CONTACT = "NO_CONTACT"
    WRONG_FINGER = "WRONG_FINGER"
    WRONG_KEY = "WRONG_KEY"
    MISSED_KEY = "MISSED_KEY"
    EVENT_MISMATCH = "EVENT_MISMATCH"
    SCREEN_MISMATCH = "SCREEN_MISMATCH"
    STUCK_KEY = "STUCK_KEY"
    FINGER_NOT_RELEASED = "FINGER_NOT_RELEASED"
    EXCESS_FORCE = "EXCESS_FORCE"
    UNSAFE_POSTURE = "UNSAFE_POSTURE"


@dataclass(frozen=True, slots=True)
class Dex5Stroke:
    index: int
    key: str
    hand: HandSide
    finger: Dex5Finger
    expected_prefix: str

    @property
    def executor(self) -> str:
        return f"{self.hand.value}.{self.finger.value}"


@dataclass(frozen=True, slots=True)
class Dex5Stage:
    stroke_index: int
    phase: Dex5Phase
    executor: str
    key: str
    maximum_duration_s: float
    preconditions: tuple[str, ...]
    postconditions: tuple[str, ...]
    resources: tuple[str, ...]
    failure_codes: tuple[str, ...]
    version: str = CONTRACT_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.phase, Dex5Phase) or self.phase not in (Dex5Phase.HOVER, Dex5Phase.PRESS, Dex5Phase.RELEASE):
            raise ValueError("typing stage has an invalid phase")
        if not math.isfinite(self.maximum_duration_s) or self.maximum_duration_s <= 0:
            raise ValueError("typing stage horizon must be finite and positive")
        if not all((self.preconditions, self.postconditions, self.resources, self.failure_codes)):
            raise ValueError("typing stage must declare its complete skill contract")


@dataclass(frozen=True, slots=True)
class Dex5TypingPlan:
    text: str
    strokes: tuple[Dex5Stroke, ...]
    stages: tuple[Dex5Stage, ...]
    support_mode: str = "assisted_fixture"
    initial_text: str = ""
    version: str = CONTRACT_VERSION
    maximum_setup_duration_s: float = 8.0

    def __post_init__(self) -> None:
        if self.support_mode not in {"assisted_fixture", "seat_contact"}:
            raise ValueError("unknown seated support mode")
        if not self.strokes or not math.isfinite(self.maximum_setup_duration_s) or self.maximum_setup_duration_s <= 0:
            raise ValueError("typing plan requires strokes and a finite setup horizon")
        if tuple(stroke.index for stroke in self.strokes) != tuple(range(len(self.strokes))):
            raise ValueError("typing stroke indices must be contiguous")
        expected_stages = tuple(
            (stroke.index, phase, stroke.executor, stroke.key)
            for stroke in self.strokes
            for phase in (Dex5Phase.HOVER, Dex5Phase.PRESS, Dex5Phase.RELEASE)
        )
        if tuple((stage.stroke_index, stage.phase, stage.executor, stage.key) for stage in self.stages) != expected_stages:
            raise ValueError("typing plan must include every ordered hover/press/release stage")
        prefix = self.initial_text
        for stroke in self.strokes:
            prefix = _apply_key(prefix, stroke.key)
            if stroke.expected_prefix != prefix:
                raise ValueError("expected display prefix must follow the planned keys")

    @property
    def maximum_duration_s(self) -> float:
        return self.maximum_setup_duration_s + sum(
            stage.maximum_duration_s for stage in self.stages
        )

    @property
    def executors(self) -> tuple[str, ...]:
        return tuple(sorted({stroke.executor for stroke in self.strokes}))

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["maximum_duration_s"] = self.maximum_duration_s
        result["executors"] = self.executors
        return result


_FINGER_KEYS = {
    (HandSide.LEFT, Dex5Finger.LITTLE): "1qaz",
    (HandSide.LEFT, Dex5Finger.RING): "2wsx",
    (HandSide.LEFT, Dex5Finger.MIDDLE): "3edc",
    (HandSide.LEFT, Dex5Finger.INDEX): "45rtfgvb",
    (HandSide.RIGHT, Dex5Finger.INDEX): "67yuhjnm",
    (HandSide.RIGHT, Dex5Finger.MIDDLE): "8ik,",
    (HandSide.RIGHT, Dex5Finger.RING): "9ol.",
    (HandSide.RIGHT, Dex5Finger.LITTLE): "0p;/'[]\\-=",
}


class Dex5TypingPlanner:
    """Conservative, sequential ten-finger QWERTY baseline.

    Two hands participate across strokes; unrelated fingers are never ordered
    to press simultaneously. Uppercase/shift chords are explicitly out of this
    baseline's scope instead of silently routing text directly to the display.
    """

    def plan(
        self,
        text: str = DEFAULT_DEMO_TEXT,
        *,
        support_mode: str = "assisted_fixture",
        initial_text: str = "",
        phase_timeout_s: float = 4.0,
    ) -> Dex5TypingPlan:
        if not text or len(text) > 256:
            raise ValueError("demo text must contain between 1 and 256 characters")
        if support_mode not in {"assisted_fixture", "seat_contact"}:
            raise ValueError("unknown seated support mode")
        if not math.isfinite(phase_timeout_s) or phase_timeout_s <= 0:
            raise ValueError("phase timeout must be finite and positive")
        strokes: list[Dex5Stroke] = []
        stages: list[Dex5Stage] = []
        space_count = 0
        prefix = initial_text
        for index, character in enumerate(text):
            if character == " ":
                hand = HandSide.LEFT if space_count % 2 == 0 else HandSide.RIGHT
                finger = Dex5Finger.THUMB
                key = "space"
                space_count += 1
            elif character in "\n\b":
                hand, finger = HandSide.RIGHT, Dex5Finger.LITTLE
                key = "enter" if character == "\n" else "backspace"
            else:
                assignment = next(
                    (executor for executor, keys in _FINGER_KEYS.items() if character in keys),
                    None,
                )
                if assignment is None:
                    raise ValueError(f"unsupported unshifted demo character: {character!r}")
                hand, finger = assignment
                key = character
            prefix = _apply_key(prefix, key)
            stroke = Dex5Stroke(index, key, hand, finger, prefix)
            strokes.append(stroke)
            for phase in (Dex5Phase.HOVER, Dex5Phase.PRESS, Dex5Phase.RELEASE):
                preconditions, postconditions = {
                    Dex5Phase.HOVER: (("seated_stable", "all_keys_released"), ("target_clearance",)),
                    Dex5Phase.PRESS: (("target_clearance",), ("assigned_finger_contact", "key_travel", "key_down")),
                    Dex5Phase.RELEASE: (("key_down",), ("key_up", "all_fingers_clear", "screen_prefix")),
                }[phase]
                stages.append(Dex5Stage(
                    index, phase, stroke.executor, key, phase_timeout_s,
                    preconditions, postconditions,
                    ("seated_support", "keyboard", f"{hand.value}_hand"),
                    tuple(item.value for item in Dex5Failure),
                ))
        return Dex5TypingPlan(text, tuple(strokes), tuple(stages), support_mode, initial_text)

    def plan_repair(
        self, *, expected: str, observed: str, maximum_correction_keys: int = 32,
    ) -> Dex5TypingPlan:
        """Bounded prefix repair; execution still requires fresh release proof.

        Safety/posture failures must be resolved before this separate plan may
        start. Planning a repair does not mark a failed attempt as recovered.
        """
        common = 0
        for wanted, actual in zip(expected, observed):
            if wanted != actual:
                break
            common += 1
        correction = "\b" * (len(observed) - common) + expected[common:]
        if not correction:
            raise ValueError("display already matches expected text")
        if not 1 <= len(correction) <= maximum_correction_keys:
            raise ValueError("text repair exceeds the correction horizon")
        return self.plan(correction, initial_text=observed)


@dataclass(frozen=True, slots=True)
class Dex5Contact:
    key: str
    hand: str
    finger: str
    force_n: float

    @property
    def executor(self) -> str:
        return f"{self.hand}.{self.finger}"


@dataclass(frozen=True, slots=True)
class Dex5Observation:
    """Read-only adapter snapshot, with delta events since the last snapshot.

    ``key_travel_m`` must cover every monitored key, including adjacent keys;
    ``fingertip_clearance_m`` must cover all ten executors at release. The
    display is a read-back of the event sink, not planned or controller text.
    Source names describe provenance; they are not cryptographic attestation.
    """

    time_s: float
    stroke_index: int
    phase: Dex5Phase
    key_travel_m: tuple[tuple[str, float], ...]
    contacts: tuple[Dex5Contact, ...]
    events: tuple[tuple[str, str], ...]
    screen_text: str
    keys_down: tuple[str, ...]
    fingertip_clearance_m: tuple[tuple[str, float], ...]
    seat_support_force_n: float
    pelvis_height_m: float
    torso_tilt_rad: float
    base_constraint_active: bool
    source: str
    screen_source: str


@dataclass(frozen=True, slots=True)
class Dex5SafetyLimits:
    maximum_contact_force_n: float = 12.0
    maximum_torso_tilt_rad: float = 0.45
    minimum_pelvis_height_m: float = 0.30
    maximum_pelvis_height_m: float = 0.90
    minimum_seat_support_force_n: float = 5.0
    press_threshold_m: float = 0.0020
    release_threshold_m: float = 0.0012
    minimum_release_clearance_m: float = 0.004

    def __post_init__(self) -> None:
        if any(not math.isfinite(value) or value <= 0 for value in asdict(self).values()):
            raise ValueError("safety limits must be finite and positive")
        if self.minimum_pelvis_height_m >= self.maximum_pelvis_height_m:
            raise ValueError("invalid pelvis height interval")
        if self.release_threshold_m >= self.press_threshold_m:
            raise ValueError("release threshold must be below press threshold")


class Dex5SafetyGuard:
    """Independent low-level guard usable by adapters before the next action."""

    def __init__(self, limits: Dex5SafetyLimits | None = None) -> None:
        self.limits = limits or Dex5SafetyLimits()

    def check(self, observation: Dex5Observation) -> Dex5Failure | None:
        numeric = (
            observation.time_s, observation.seat_support_force_n,
            observation.pelvis_height_m, observation.torso_tilt_rad,
            *(value for _, value in observation.key_travel_m),
            *(value for _, value in observation.fingertip_clearance_m),
            *(contact.force_n for contact in observation.contacts),
        )
        if any(not math.isfinite(value) for value in numeric):
            return Dex5Failure.INVALID_EVIDENCE
        if any(value < 0 for _, value in observation.key_travel_m):
            return Dex5Failure.INVALID_EVIDENCE
        if observation.seat_support_force_n < 0 or any(
            contact.force_n < 0 for contact in observation.contacts
        ):
            return Dex5Failure.INVALID_EVIDENCE
        if any(contact.force_n > self.limits.maximum_contact_force_n for contact in observation.contacts):
            return Dex5Failure.EXCESS_FORCE
        if (
            abs(observation.torso_tilt_rad) > self.limits.maximum_torso_tilt_rad
            or not self.limits.minimum_pelvis_height_m <= observation.pelvis_height_m <= self.limits.maximum_pelvis_height_m
        ):
            return Dex5Failure.UNSAFE_POSTURE
        return None


@dataclass(frozen=True, slots=True)
class Dex5VerificationReport:
    succeeded: bool
    capability: str
    failure_code: str | None
    failure_detail: str
    completed_strokes: int
    planned_strokes: int
    executors_observed: tuple[str, ...]
    observed_text: str
    expected_text: str
    keys_down_after_stop: tuple[str, ...]
    support_mode: str
    recovery_actions: tuple[str, ...]
    dynamic_sitting_verified: bool = False
    camera_ocr_verified: bool = False
    real_robot_verified: bool = False
    version: str = CONTRACT_VERSION

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


_ALL_EXECUTORS = tuple(
    f"{hand.value}.{finger.value}" for hand in HandSide for finger in Dex5Finger
)


class Dex5TypingVerifier:
    """Derive success only from sensor snapshots and event/display agreement."""

    def __init__(self, limits: Dex5SafetyLimits | None = None) -> None:
        self.guard = Dex5SafetyGuard(limits)
        self.limits = self.guard.limits

    def verify(
        self,
        plan: Dex5TypingPlan,
        observations: Iterable[Dex5Observation],
        *,
        capability: str = "replay",
    ) -> Dex5VerificationReport:
        snapshots = tuple(observations)
        completed = 0
        observed_executors: set[str] = set()
        expected_text = plan.strokes[-1].expected_prefix

        def result(code: Dex5Failure | None, detail: str = "") -> Dex5VerificationReport:
            last = snapshots[-1] if snapshots else None
            return Dex5VerificationReport(
                succeeded=code is None, capability=capability,
                failure_code=code.value if code else None, failure_detail=detail,
                completed_strokes=completed, planned_strokes=len(plan.strokes),
                executors_observed=tuple(sorted(observed_executors)),
                observed_text=last.screen_text if last else "",
                expected_text=expected_text,
                keys_down_after_stop=last.keys_down if last else (),
                support_mode=plan.support_mode,
                recovery_actions=() if code is None else (
                    "release_all_fingers", "stop_typing", "reobserve_keyboard",
                ),
            )

        sources = {
            "replay": ("replay_sensor", "replay_display"),
            "isaac_sim_5_1": ("isaac_articulation_contact", "isaac_virtual_keyboard_display"),
        }
        if capability not in sources:
            return result(Dex5Failure.UNSUPPORTED_SOURCE, "unknown backend capability")
        if not snapshots:
            return result(Dex5Failure.MISSING_OBSERVATION, "no measured observations")
        required_keys = set(DEX5_MONITORED_KEYS)
        expected_source, expected_screen_source = sources[capability]
        previous_time = -math.inf
        groups: list[list[Dex5Observation]] = []
        for sample in snapshots:
            failure = self.guard.check(sample)
            if failure:
                return result(failure, f"guard rejected sample at {sample.time_s}s")
            if sample.time_s < 0 or sample.time_s <= previous_time:
                return result(Dex5Failure.INVALID_EVIDENCE, "timestamps must be increasing and non-negative")
            previous_time = sample.time_s
            if sample.source != expected_source or sample.screen_source != expected_screen_source:
                return result(Dex5Failure.UNSUPPORTED_SOURCE, "sensor/display provenance does not match capability")
            travel = dict(sample.key_travel_m)
            clearances = dict(sample.fingertip_clearance_m)
            if (
                len(travel) != len(sample.key_travel_m)
                or len(clearances) != len(sample.fingertip_clearance_m)
                or not required_keys <= travel.keys()
                or set(_ALL_EXECUTORS) != clearances.keys()
                or len(set(sample.keys_down)) != len(sample.keys_down)
                or not set(sample.keys_down) <= travel.keys()
            ):
                return result(Dex5Failure.INVALID_EVIDENCE, "missing or duplicate key/fingertip measurements")
            if any(
                contact.executor not in _ALL_EXECUTORS or contact.key not in travel
                for contact in sample.contacts
            ):
                return result(Dex5Failure.INVALID_EVIDENCE, "contact does not identify a monitored key and Dex5 finger")
            if plan.support_mode == "assisted_fixture" and not sample.base_constraint_active:
                return result(Dex5Failure.SEATED_PRECONDITION, "assisted fixture constraint is not active")
            if plan.support_mode == "seat_contact" and (
                sample.base_constraint_active
                or sample.seat_support_force_n < self.limits.minimum_seat_support_force_n
            ):
                return result(Dex5Failure.SEATED_PRECONDITION, "unconstrained seat contact was not measured")
            if not groups or (
                sample.stroke_index, sample.phase
            ) != (groups[-1][0].stroke_index, groups[-1][0].phase):
                groups.append([])
            groups[-1].append(sample)

        if snapshots[-1].time_s - snapshots[0].time_s > plan.maximum_duration_s + 1e-9:
            return result(Dex5Failure.TIMEOUT, "demo exceeded its finite horizon")
        expected_phases = [(-1, Dex5Phase.SEATED)] + [
            (stage.stroke_index, stage.phase) for stage in plan.stages
        ]
        actual_phases = [(group[0].stroke_index, group[0].phase) for group in groups]
        if actual_phases != expected_phases:
            return result(Dex5Failure.PHASE_ORDER, "expected seated, then hover/press/release for every stroke")
        seated = groups[0][-1]
        if (
            any(sample.events for sample in groups[0])
            or seated.screen_text != plan.initial_text
            or not self._all_released(seated)
        ):
            return result(Dex5Failure.SEATED_PRECONDITION, "initial keyboard/display/clearance precondition failed")
        elapsed = groups[0][-1].time_s - groups[0][0].time_s
        if elapsed > plan.maximum_setup_duration_s + 1e-9:
            return result(Dex5Failure.TIMEOUT, "seated setup exceeded horizon")
        previous_end = seated.time_s
        for stage, group in zip(plan.stages, groups[1:], strict=True):
            stroke = plan.strokes[stage.stroke_index]
            last = group[-1]
            if last.time_s - previous_end > stage.maximum_duration_s + 1e-9:
                return result(Dex5Failure.TIMEOUT, f"{stage.phase.value} exceeded horizon for stroke {stroke.index}")
            previous_end = last.time_s
            events = tuple(event for sample in group for event in sample.events)
            if any(
                key != stroke.key and travel > self.limits.release_threshold_m
                for sample in group for key, travel in sample.key_travel_m
            ) or any(
                contact.key != stroke.key for sample in group for contact in sample.contacts
            ):
                return result(Dex5Failure.WRONG_KEY, f"adjacent key moved or contacted during stroke {stroke.index}")
            if stage.phase is Dex5Phase.HOVER:
                if events or any(not self._all_released(sample) for sample in group):
                    return result(Dex5Failure.FINGER_NOT_RELEASED, f"hover was not clear for stroke {stroke.index}")
                before = plan.initial_text if stroke.index == 0 else plan.strokes[stroke.index - 1].expected_prefix
                if any(sample.screen_text != before for sample in group):
                    return result(Dex5Failure.SCREEN_MISMATCH, "display changed without a planned press")
            elif stage.phase is Dex5Phase.PRESS:
                contacts = [contact for sample in group for contact in sample.contacts if contact.force_n > 0]
                if not contacts:
                    return result(Dex5Failure.NO_CONTACT, f"no physical fingertip contact for stroke {stroke.index}")
                if any(contact.executor != stroke.executor for contact in contacts):
                    return result(Dex5Failure.WRONG_FINGER, f"contact came from another executor at stroke {stroke.index}")
                actuation_samples = [sample for sample in group if (
                    dict(sample.key_travel_m)[stroke.key] >= self.limits.press_threshold_m
                    and any(contact.executor == stroke.executor and contact.force_n > 0 for contact in sample.contacts)
                )]
                if not actuation_samples:
                    return result(Dex5Failure.MISSED_KEY, f"contact and key travel did not agree for stroke {stroke.index}")
                if events != (("keydown", stroke.key),):
                    return result(Dex5Failure.EVENT_MISMATCH, f"press must emit exactly one target KeyDown at stroke {stroke.index}")
                if not any(("keydown", stroke.key) in sample.events for sample in actuation_samples):
                    return result(Dex5Failure.EVENT_MISMATCH, "KeyDown was not backed by simultaneous travel/contact")
                if last.keys_down != (stroke.key,):
                    return result(Dex5Failure.EVENT_MISMATCH, "held-key state disagrees with KeyDown")
                observed_executors.add(stroke.executor)
            else:
                if events != (("keyup", stroke.key),):
                    code = Dex5Failure.STUCK_KEY if last.keys_down else Dex5Failure.EVENT_MISMATCH
                    return result(code, f"release must emit exactly one target KeyUp at stroke {stroke.index}")
                if any(
                    dict(sample.key_travel_m)[stroke.key] > self.limits.release_threshold_m
                    for sample in group if ("keyup", stroke.key) in sample.events
                ):
                    return result(Dex5Failure.EVENT_MISMATCH, "KeyUp occurred before measured release travel")
                if last.keys_down or any(
                    travel > self.limits.release_threshold_m for _, travel in last.key_travel_m
                ):
                    return result(Dex5Failure.STUCK_KEY, f"key did not return at stroke {stroke.index}")
                if not self._all_released(last):
                    return result(Dex5Failure.FINGER_NOT_RELEASED, f"fingertips did not clear at stroke {stroke.index}")
                if last.screen_text != stroke.expected_prefix:
                    return result(Dex5Failure.SCREEN_MISMATCH, f"independent display mismatch at stroke {stroke.index}")
                completed += 1
        return result(None)

    def _all_released(self, sample: Dex5Observation) -> bool:
        return (
            not sample.keys_down and not sample.contacts
            and all(travel <= self.limits.release_threshold_m for _, travel in sample.key_travel_m)
            and all(clearance >= self.limits.minimum_release_clearance_m for _, clearance in sample.fingertip_clearance_m)
        )


def _apply_key(text: str, key: str) -> str:
    if key == "backspace":
        return text[:-1]
    return text + {"space": " ", "enter": "\n"}.get(key, key)


REPLAY_FAULTS = (
    "no_contact", "wrong_finger", "wrong_key", "stuck_key", "over_force",
    "screen_mismatch", "controller_claim", "timeout", "unsafe_posture",
)


def build_replay_observations(
    plan: Dex5TypingPlan, *, inject_fault: str | None = None,
) -> tuple[Dex5Observation, ...]:
    """Synthesize *labelled replay* samples, with events from the keycap bridge.

    This exercises contract verification without loading Isaac or pretending
    synthetic contact is physics. Faults target the first stroke and remain
    in the evidence even when subsequent synthetic strokes finish.
    """
    if inject_fault is not None and inject_fault not in REPLAY_FAULTS:
        raise ValueError(f"unknown replay fault: {inject_fault}")
    bridge = KeyboardEventBridge()
    screen = plan.initial_text
    snapshots: list[Dex5Observation] = []
    time_s = 0.0
    monitored_keys = DEX5_MONITORED_KEYS

    def snapshot(
        stroke: Dex5Stroke | None,
        phase: Dex5Phase,
        *,
        travel_key: str | None = None,
        travel_m: float = 0.0,
        contact: Dex5Contact | None = None,
    ) -> None:
        nonlocal time_s, screen
        time_s += 0.1
        fault_active = stroke is not None and stroke.index == 0
        if fault_active and phase is Dex5Phase.PRESS and inject_fault == "timeout":
            time_s += plan.stages[1].maximum_duration_s
        travel = {key: 0.0 for key in monitored_keys}
        if travel_key is not None:
            travel[travel_key] = travel_m
        generated = []
        for key in monitored_keys:
            generated.extend(bridge.process(KeycapSample(
                key=key, displacement_m=travel[key],
                in_contact=contact is not None and contact.key == key,
                monotonic_time=time_s, source="replay_sensor",
                sample_id=f"{len(snapshots)}:{key}",
            ), now=time_s))
        events = tuple((event.event_type.value, event.key) for event in generated)
        for event in generated:
            if event.event_type.value == "keydown":
                screen = _apply_key(screen, event.key)
        clearances = dict.fromkeys(_ALL_EXECUTORS, 0.020)
        if contact:
            clearances[contact.executor] = -travel_m
        observed_screen = screen
        if fault_active and phase is Dex5Phase.RELEASE and inject_fault == "screen_mismatch":
            observed_screen += "x"
        snapshots.append(Dex5Observation(
            time_s=time_s, stroke_index=stroke.index if stroke else -1, phase=phase,
            key_travel_m=tuple(travel.items()), contacts=(contact,) if contact else (),
            events=events, screen_text=observed_screen, keys_down=bridge.keys_down,
            fingertip_clearance_m=tuple(clearances.items()),
            seat_support_force_n=0.0 if plan.support_mode == "assisted_fixture" else 120.0,
            pelvis_height_m=0.65,
            torso_tilt_rad=0.8 if fault_active and inject_fault == "unsafe_posture" else 0.02,
            base_constraint_active=plan.support_mode == "assisted_fixture",
            source="controller_completion" if inject_fault == "controller_claim" else "replay_sensor",
            screen_source="replay_display",
        ))

    snapshot(None, Dex5Phase.SEATED)
    for stroke in plan.strokes:
        snapshot(stroke, Dex5Phase.HOVER)
        fault_active = stroke.index == 0
        physical_key = (
            ("x" if stroke.key != "x" else "z")
            if fault_active and inject_fault == "wrong_key" else stroke.key
        )
        finger = (
            Dex5Finger.INDEX if stroke.finger is not Dex5Finger.INDEX else Dex5Finger.MIDDLE
        ) if fault_active and inject_fault == "wrong_finger" else stroke.finger
        contact = None if fault_active and inject_fault == "no_contact" else Dex5Contact(
            physical_key, stroke.hand.value, finger.value,
            20.0 if fault_active and inject_fault == "over_force" else 2.0,
        )
        snapshot(stroke, Dex5Phase.PRESS, travel_key=physical_key, travel_m=0.0028, contact=contact)
        if fault_active and inject_fault == "stuck_key":
            snapshot(stroke, Dex5Phase.RELEASE, travel_key=physical_key, travel_m=0.0028, contact=contact)
        else:
            snapshot(stroke, Dex5Phase.RELEASE)
    return tuple(snapshots)


@dataclass(frozen=True, slots=True)
class Dex5ReplayResult:
    plan: Dex5TypingPlan
    observations: tuple[Dex5Observation, ...]
    report: Dex5VerificationReport

    def to_dict(self) -> dict[str, object]:
        return {
            "plan": self.plan.to_dict(),
            "report": self.report.to_dict(),
            "observations": [asdict(sample) for sample in self.observations],
        }


def run_replay_demo(
    text: str = DEFAULT_DEMO_TEXT, *, inject_fault: str | None = None,
) -> Dex5ReplayResult:
    plan = Dex5TypingPlanner().plan(text)
    observations = build_replay_observations(plan, inject_fault=inject_fault)
    report = Dex5TypingVerifier().verify(plan, observations, capability="replay")
    return Dex5ReplayResult(plan, observations, report)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deterministic Dex5-1 contract replay (not Isaac physics)")
    parser.add_argument("--text", default=DEFAULT_DEMO_TEXT)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--inject-fault", choices=REPLAY_FAULTS)
    args = parser.parse_args(argv)
    try:
        result = run_replay_demo(args.text, inject_fault=args.inject_fault)
    except ValueError as exc:
        parser.error(str(exc))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(result.to_dict(), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(result.report.to_dict(), ensure_ascii=False, indent=2, allow_nan=False))
    return 0 if result.report.succeeded else 1


if __name__ == "__main__":
    raise SystemExit(main())
