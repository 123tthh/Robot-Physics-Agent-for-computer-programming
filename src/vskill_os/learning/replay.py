"""Deterministic, explicitly synthetic acceptance fixtures for VF-01-T01.

These generate samples, not joint motion. The existing event bridge consumes
those samples; the independent evaluator must detect intentionally corrupted
outputs without sharing the bridge's state or implementation.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
from pathlib import Path

from ..simulation import keyboard_bridge
from ..simulation.dex5_typing import DEX5_MONITORED_KEYS
from ..simulation.keyboard_bridge import KeyboardEventBridge, KeycapConfig, KeycapSample
from .contracts import (
    EpisodeRecord, JointRecord, KeyBinding, KeyEventRecord, ObservationFrame,
    PhysicalKeyRecord, RecordStamp, RunManifest, SCHEMA_VERSION, ScreenObservation,
    TaskSpec, TextObservation, VerificationRequest,
)


def single_key_task() -> TaskSpec:
    return TaskSpec(
        SCHEMA_VERSION, "vf01-contract-single-a", "a", "",
        (KeyBinding("a", "a"), KeyBinding("s", "s"), KeyBinding("d", "d")),
        "backspace", tuple(DEX5_MONITORED_KEYS), ("synthetic_finger_joint",), "left",
        "synthetic_fixed_support",
        ("synthetic.joints", "synthetic.keycaps", "synthetic.events", "synthetic.text_oracle", "synthetic.screen"),
        1.0, 2, 0.5,
    )


@dataclass(frozen=True)
class ReplayCase:
    name: str
    task: TaskSpec
    episode: EpisodeRecord
    expected_pass: bool
    required_failures: tuple[str, ...]


CASE_EXPECTATIONS = {
    "correct_press": (True, ()),
    "timeout_release": (False, ("ADMINISTRATIVE_RELEASE", "STUCK_KEY")),
    "duplicate_event": (False, ("DUPLICATE_EVENT",)),
    "missing_event": (False, ("MISSING_EVENT",)),
    "delayed_feedback": (False, ("SCREEN_PENDING",)),
    "wrong_text": (False, ("TEXT_MISMATCH",)),
    "delayed_feedback_waited": (True, ()),
    "repeated_character": (True, ()),
    "backspace_recovery": (True, ()),
    "dropped_then_retried": (True, ()),
}


def build_replay_case(name: str) -> ReplayCase:
    if name not in CASE_EXPECTATIONS:
        raise ValueError(f"unknown replay case: {name}")
    task = single_key_task()
    presses = ("a",)
    if name == "repeated_character":
        task = replace(task, task_id="vf01-contract-repeat-aa", target_text="aa")
        presses = ("a", "a")
    elif name == "backspace_recovery":
        presses = ("s", "backspace", "a")
    elif name == "dropped_then_retried":
        presses = ("a", "a")
    episode_id = f"replay-{name}"

    def stamp(stream: str, sequence: int, capture: float, arrival: float | None = None) -> RecordStamp:
        return RecordStamp(
            f"{episode_id}:{stream}:{sequence:04d}", "env-0", episode_id, sequence,
            "synthetic_sim_seconds", capture, capture if arrival is None else arrival,
            f"synthetic.{stream}",
        )

    bridge = KeyboardEventBridge(KeycapConfig(maximum_hold_seconds=0.015 if name == "timeout_release" else 5.0))
    frames: list[ObservationFrame] = []
    events: list[KeyEventRecord] = []
    last_press_tick = 2 + 6 * (len(presses) - 1)
    request_time = (last_press_tick + 5) / 100
    capture_time = (last_press_tick + 6) / 100
    arrival_time = (last_press_tick + 7) / 100
    end_tick = last_press_tick + 8
    if name in {"delayed_feedback", "delayed_feedback_waited"}:
        arrival_time += 0.1
    if name == "delayed_feedback_waited":
        end_tick += 10

    for tick in range(end_tick + 1):
        now = tick / 100
        frame_stamp = stamp("keycaps", tick, now)
        keys = []
        for key in task.monitored_keys:
            travel, contact, force = 0.0, False, 0.0
            for press_index, pressed_key in enumerate(presses):
                phase = tick - (2 + 6 * press_index)
                if key == pressed_key:
                    if phase in (0, 1) or (name == "timeout_release" and phase >= 0):
                        travel, contact, force = 0.003, True, 3.0
                    elif phase == 2:
                        travel, contact, force = 0.0005, True, 0.2
            keys.append(PhysicalKeyRecord(key, travel, contact, force))
            raw = KeycapSample(key, travel, contact, now, "synthetic.keycaps", f"{frame_stamp.record_id}:{key}")
            for event in bridge.process(raw, now=now):
                events.append(KeyEventRecord(
                    stamp("events", len(events), event.monotonic_time), event.key,
                    event.event_type.value, event.reason, frame_stamp.record_id,
                ))
        frames.append(ObservationFrame(
            frame_stamp, tuple(keys),
            (JointRecord("synthetic_finger_joint", 0.0, 0.0, 0.0),), False,
        ))

    if name == "duplicate_event":
        events.insert(1, events[0])
    elif name == "missing_event":
        events = [event for event in events if event.event_type != "keyup"]
    # Stable fixture IDs replace the bridge's random UUIDs, preserving its
    # event type, reason, key, time and raw-sample association.
    events = [replace(event, stamp=stamp("events", index, event.stamp.capture_time_s))
              for index, event in enumerate(events)]

    texts = [TextObservation(stamp("text_oracle", 0, 0.0), task.initial_text, None, "snapshot")]
    text = task.initial_text
    bindings = {binding.key: binding.character for binding in task.character_keys}
    for event in events:
        if event.event_type != "keydown":
            continue
        delivery = "dropped" if name == "dropped_then_retried" and len(texts) == 1 else "applied"
        if delivery == "applied":
            text = text[:-1] if event.key == task.backspace_key else text + bindings[event.key]
        if name == "wrong_text":
            text = "s"
        texts.append(TextObservation(
            stamp("text_oracle", len(texts), event.stamp.capture_time_s), text,
            event.stamp.record_id, delivery,
        ))
    texts.append(TextObservation(stamp("text_oracle", len(texts), end_tick / 100), text, None, "snapshot"))
    request = VerificationRequest(stamp("requests", 0, request_time), "final")
    screen = ScreenObservation(
        stamp("screen", 0, capture_time, arrival_time), request.stamp.record_id,
        hashlib.sha256(f"synthetic-screen:{text}".encode()).hexdigest(), text, 1.0, False,
    )
    generator_hash = hashlib.sha256(
        Path(__file__).read_bytes() + Path(keyboard_bridge.__file__).read_bytes()
    ).hexdigest()
    episode = EpisodeRecord(
        SCHEMA_VERSION, "env-0", episode_id, "synthetic_sim_seconds", task.sha256(),
        RunManifest(
            f"vf01-{name}", "keyboard_event_bridge_replay", "contract_replay", "unrecorded",
            generator_hash, 0, "hand-authored samples; no motion, IK, physics or training",
        ),
        0.0, end_tick / 100, "finish_requested", tuple(frames), tuple(events), tuple(texts),
        (request,), (screen,),
    )
    expected_pass, failures = CASE_EXPECTATIONS[name]
    return ReplayCase(name, task, episode, expected_pass, failures)
