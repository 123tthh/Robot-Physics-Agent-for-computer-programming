"""Physical keycap state machine and deterministic digital-event bridge."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import math
import time
from typing import Iterable
from uuid import uuid4

from ..ipc import MessageHeader, MessageKind, WorkerMessage
from ..typing import normalize_key


class KeycapPhase(StrEnum):
    RELEASED = "released"
    CONTACT = "contact"
    ACTUATED = "actuated"
    HELD = "held"
    RELEASING = "releasing"


class KeyEventType(StrEnum):
    KEYDOWN = "keydown"
    KEYUP = "keyup"


@dataclass(frozen=True, slots=True)
class KeycapConfig:
    press_threshold_m: float = 0.0020
    release_threshold_m: float = 0.0012
    debounce_seconds: float = 0.008
    maximum_hold_seconds: float = 5.0
    maximum_sample_age_seconds: float = 0.250

    def __post_init__(self) -> None:
        values = (
            self.press_threshold_m,
            self.release_threshold_m,
            self.debounce_seconds,
            self.maximum_hold_seconds,
            self.maximum_sample_age_seconds,
        )
        if not all(math.isfinite(value) and value >= 0.0 for value in values):
            raise ValueError("keycap thresholds and timing values must be finite")
        if self.press_threshold_m <= self.release_threshold_m:
            raise ValueError("press threshold must exceed release threshold for hysteresis")
        if self.maximum_hold_seconds <= 0.0 or self.maximum_sample_age_seconds <= 0.0:
            raise ValueError("hold duration and sample age must be positive")


@dataclass(frozen=True, slots=True)
class KeycapSample:
    key: str
    displacement_m: float
    in_contact: bool
    monotonic_time: float
    source: str = "isaac_keycap_oracle"
    sample_id: str = ""
    adjacent_contacts: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.key.strip() or not self.source.strip():
            raise ValueError("keycap sample key and source are required")
        if not math.isfinite(self.displacement_m) or self.displacement_m < 0.0:
            raise ValueError("keycap displacement must be finite and non-negative")
        if not math.isfinite(self.monotonic_time):
            raise ValueError("keycap sample timestamp must be finite")

    def to_message(self, header: MessageHeader) -> WorkerMessage:
        return WorkerMessage(
            MessageKind.KEYCAP_STATE,
            header,
            {
                "key": normalize_key(self.key),
                "displacement_m": self.displacement_m,
                "in_contact": self.in_contact,
                "sample_time": self.monotonic_time,
                "sample_source": self.source,
                "sample_id": self.sample_id,
                "adjacent_contacts": list(self.adjacent_contacts),
            },
        )


@dataclass(frozen=True, slots=True)
class KeyEvent:
    event_type: KeyEventType
    key: str
    monotonic_time: float
    source: str
    modifiers: tuple[str, ...] = ()
    reason: str = "physical_threshold"
    event_id: str = ""

    def __post_init__(self) -> None:
        if not math.isfinite(self.monotonic_time):
            raise ValueError("key event timestamp must be finite")

    def to_message(self, header: MessageHeader) -> WorkerMessage:
        return WorkerMessage(
            MessageKind.KEY_EVENT,
            header,
            {
                "event_type": self.event_type.value,
                "key": self.key,
                "modifiers": list(self.modifiers),
                "event_time": self.monotonic_time,
                "event_source": self.source,
                "reason": self.reason,
                "event_id": self.event_id,
            },
        )


@dataclass(slots=True)
class _KeycapRuntime:
    phase: KeycapPhase = KeycapPhase.RELEASED
    is_down: bool = False
    last_sample_time: float = -math.inf
    last_event_time: float = -math.inf
    pressed_at: float | None = None


class KeyboardEventBridge:
    """Turns replayable key travel/contact samples into one Down/Up pair."""

    MODIFIERS = frozenset({"left_ctrl", "right_ctrl", "left_alt", "right_alt", "left_shift", "right_shift"})

    def __init__(self, config: KeycapConfig | None = None) -> None:
        self.config = config or KeycapConfig()
        self._keys: dict[str, _KeycapRuntime] = {}
        self._seen_samples: set[str] = set()
        self._modifiers: set[str] = set()
        self.rejected_samples: list[tuple[str, str]] = []
        self.adjacent_contact_log: list[tuple[float, str, tuple[str, ...]]] = []

    def phase(self, key: str) -> KeycapPhase:
        return self._keys.get(normalize_key(key), _KeycapRuntime()).phase

    @property
    def keys_down(self) -> tuple[str, ...]:
        return tuple(sorted(key for key, state in self._keys.items() if state.is_down))

    def process(self, sample: KeycapSample, *, now: float | None = None) -> tuple[KeyEvent, ...]:
        observed_now = time.monotonic() if now is None else now
        key = normalize_key(sample.key)
        state = self._keys.setdefault(key, _KeycapRuntime())
        sample_id = sample.sample_id
        if sample_id and sample_id in self._seen_samples:
            self.rejected_samples.append((sample_id, "DUPLICATE_SAMPLE"))
            return ()
        if sample.monotonic_time <= state.last_sample_time:
            self.rejected_samples.append((sample_id, "OUT_OF_ORDER_SAMPLE"))
            return ()
        if observed_now - sample.monotonic_time > self.config.maximum_sample_age_seconds:
            self.rejected_samples.append((sample_id, "STALE_SAMPLE"))
            return ()
        if sample_id:
            self._seen_samples.add(sample_id)
        state.last_sample_time = sample.monotonic_time
        if sample.adjacent_contacts:
            self.adjacent_contact_log.append(
                (sample.monotonic_time, key, tuple(sorted(map(normalize_key, sample.adjacent_contacts))))
            )

        events: list[KeyEvent] = []
        if state.is_down and state.pressed_at is not None:
            if sample.monotonic_time - state.pressed_at >= self.config.maximum_hold_seconds:
                events.append(self._release(key, state, sample.monotonic_time, "maximum_hold"))
                state.phase = KeycapPhase.RELEASING

        if state.is_down:
            if sample.displacement_m <= self.config.release_threshold_m:
                events.append(self._release(key, state, sample.monotonic_time, "release_threshold"))
                state.phase = KeycapPhase.RELEASING if sample.in_contact else KeycapPhase.RELEASED
            elif state.phase is KeycapPhase.ACTUATED:
                state.phase = KeycapPhase.HELD
            return tuple(events)

        # Once a release threshold has been crossed, the switch must physically
        # clear contact before it can arm another press. This prevents spring
        # bounce from creating a second Down/Up pair.
        if state.phase is KeycapPhase.RELEASING:
            if not sample.in_contact:
                state.phase = KeycapPhase.RELEASED
            return tuple(events)

        if not sample.in_contact:
            state.phase = KeycapPhase.RELEASED
            return tuple(events)

        if sample.displacement_m < self.config.press_threshold_m:
            state.phase = KeycapPhase.CONTACT
            return tuple(events)

        if sample.monotonic_time - state.last_event_time < self.config.debounce_seconds:
            return tuple(events)
        state.phase = KeycapPhase.ACTUATED
        state.is_down = True
        state.pressed_at = sample.monotonic_time
        state.last_event_time = sample.monotonic_time
        if key in self.MODIFIERS:
            self._modifiers.add(key)
        events.append(
            KeyEvent(
                KeyEventType.KEYDOWN,
                key,
                sample.monotonic_time,
                "keyboard_event_bridge",
                tuple(sorted(self._modifiers)),
                event_id=uuid4().hex,
            )
        )
        return tuple(events)

    def replay(
        self,
        samples: Iterable[KeycapSample],
        *,
        now: float | None = None,
    ) -> tuple[KeyEvent, ...]:
        events: list[KeyEvent] = []
        for sample in samples:
            events.extend(self.process(sample, now=sample.monotonic_time if now is None else now))
        return tuple(events)

    def all_keys_up(
        self,
        *,
        monotonic_time: float,
        reason: str = "worker_shutdown",
    ) -> tuple[KeyEvent, ...]:
        events: list[KeyEvent] = []
        for key in self.keys_down:
            state = self._keys[key]
            events.append(self._release(key, state, monotonic_time, reason))
            state.phase = KeycapPhase.RELEASED
        return tuple(events)

    def _release(
        self,
        key: str,
        state: _KeycapRuntime,
        timestamp: float,
        reason: str,
    ) -> KeyEvent:
        state.is_down = False
        state.pressed_at = None
        state.last_event_time = timestamp
        if key in self.MODIFIERS:
            self._modifiers.discard(key)
        return KeyEvent(
            KeyEventType.KEYUP,
            key,
            timestamp,
            "keyboard_event_bridge",
            tuple(sorted(self._modifiers)),
            reason=reason,
            event_id=uuid4().hex,
        )
