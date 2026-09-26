"""Supervisory text state. This object is never a policy observation."""

from __future__ import annotations

from dataclasses import dataclass
import math

from .contracts import KeyBinding, KeyEventRecord, _nonempty, _unique
from .serialization import JsonRecord

EDIT_KEYS = ("backspace", "delete", "left", "right", "home", "end")


@dataclass(frozen=True, slots=True)
class EpisodeScope(JsonRecord):
    env_id: str
    episode_id: str
    clock_domain: str

    def __post_init__(self):
        JsonRecord.__post_init__(self)
        _nonempty(self.env_id, self.episode_id, self.clock_domain)


@dataclass(frozen=True, slots=True)
class TextTaskConfig(JsonRecord):
    target_text: str = "a"
    initial_text: str = ""
    initial_focus: bool = True
    character_keys: tuple[KeyBinding, ...] = (KeyBinding("a", "a"), KeyBinding("s", "s"), KeyBinding("space", " "))
    maximum_length: int = 128

    def __post_init__(self):
        JsonRecord.__post_init__(self)
        _unique(tuple(item.key for item in self.character_keys), "text bindings")
        if (self.maximum_length < 1 or len(self.initial_text) > self.maximum_length
                or not self.target_text or len(self.target_text) > self.maximum_length
                or not set(self.target_text) <= {item.character for item in self.character_keys}
                or set(EDIT_KEYS) & {item.key for item in self.character_keys}):
            raise ValueError("invalid text task or ambiguous editing keys")


@dataclass(frozen=True, slots=True)
class FocusChange(JsonRecord):
    time_s: float
    focused: bool

    def __post_init__(self):
        JsonRecord.__post_init__(self)
        if self.time_s < 0:
            raise ValueError("focus change cannot precede the episode")


@dataclass(frozen=True, slots=True)
class DigitalInputPlan(JsonRecord):
    """Explicit software disturbances, indexed by KeyDown, not physics errors."""
    dropped_keydown_indices: tuple[int, ...] = ()
    focus_changes: tuple[FocusChange, ...] = ()

    def __post_init__(self):
        JsonRecord.__post_init__(self)
        indices = self.dropped_keydown_indices
        times = tuple(change.time_s for change in self.focus_changes)
        if (any(i < 0 for i in indices) or tuple(sorted(set(indices))) != indices
                or tuple(sorted(set(times))) != times):
            raise ValueError("digital disturbance indices/times must be sorted and unique")


@dataclass(frozen=True, slots=True)
class TextSnapshot(JsonRecord):
    time_s: float
    text: str
    cursor: int
    focused: bool


@dataclass(frozen=True, slots=True)
class TextTransition(JsonRecord):
    event_id: str
    keydown_index: int
    time_s: float
    effect: str
    before: TextSnapshot
    after: TextSnapshot


class TextTaskState:
    """Cursor starts at the end; insertion/backspace/delete affect that cursor.

    No selection, shortcuts, clipboard, OS autorepeat or implicit focus repair.
    KeyUp never edits text. Duplicate/reordered deliveries fail before mutation.
    Immutable history supports a delayed *simulated* readout at capture time.
    """

    def __init__(self, scope: EpisodeScope, config: TextTaskConfig, plan: DigitalInputPlan):
        self.scope, self.config, self.plan = scope, config, plan
        self.events: list[KeyEventRecord] = []
        self.transitions: list[TextTransition] = []
        self.history = [TextSnapshot(0., config.initial_text, len(config.initial_text), config.initial_focus)]
        self.time_s = 0.
        self._focus_index = 0
        self._seen_ids: set[str] = set()
        self._held: set[str] = set()

    def snapshot_at(self, time_s: float) -> TextSnapshot:
        if not math.isfinite(time_s) or not 0 <= time_s <= self.time_s + 1e-9:
            raise ValueError("cannot read future or pre-episode text state")
        return next(snapshot for snapshot in reversed(self.history) if snapshot.time_s <= time_s + 1e-9)

    def advance(self, time_s: float, events: tuple[KeyEventRecord, ...] = ()) -> None:
        if not math.isfinite(time_s) or time_s < self.time_s - 1e-9:
            raise ValueError("text clock must advance monotonically")
        # Validate the whole batch before changing the document or delivery log.
        ids, held, previous = set(self._seen_ids), set(self._held), self.time_s
        for index, event in enumerate(events, len(self.events)):
            stamp = event.stamp
            if ((stamp.env_id, stamp.episode_id, stamp.clock_domain) !=
                    (self.scope.env_id, self.scope.episode_id, self.scope.clock_domain)
                    or stamp.sequence != index or stamp.record_id in ids
                    or stamp.arrival_time_s <= self.time_s + 1e-9
                    or stamp.arrival_time_s < previous - 1e-9 or stamp.arrival_time_s > time_s + 1e-9):
                raise ValueError("INPUT_IDENTITY: wrong scope, order, duplicate or future delivery")
            if event.event_type == "keydown":
                if event.key in held or event.reason != "physical_threshold":
                    raise ValueError("INPUT_IDENTITY: repeated KeyDown or unsupported input source")
                held.add(event.key)
            else:
                held.discard(event.key)
            ids.add(stamp.record_id)
            previous = stamp.arrival_time_s
        bindings = {item.key: item.character for item in self.config.character_keys}
        for event in events:
            self._apply_focus_until(event.stamp.arrival_time_s)
            if event.event_type == "keydown":
                before = self.history[-1]
                text, cursor, focused = before.text, before.cursor, before.focused
                key, effect = event.key, "ignored_key"
                if len(self.transitions) in self.plan.dropped_keydown_indices:
                    effect = "software_drop"
                elif not focused:
                    effect = "focus_lost"
                elif key in bindings:
                    if len(text) >= self.config.maximum_length:
                        effect = "length_limit"
                    else:
                        text = text[:cursor] + bindings[key] + text[cursor:]
                        cursor += 1
                        effect = "insert"
                elif key == "backspace":
                    text = text[:max(0, cursor-1)] + text[cursor:]
                    cursor = max(0, cursor-1)
                    effect = "backspace"
                elif key == "delete":
                    text = text[:cursor] + text[cursor+1:]
                    effect = "delete"
                elif key in {"left", "right", "home", "end"}:
                    cursor = {"left": max(0, cursor-1), "right": min(len(text), cursor+1), "home": 0, "end": len(text)}[key]
                    effect = "move_cursor"
                after = TextSnapshot(event.stamp.arrival_time_s, text, cursor, focused)
                self.transitions.append(TextTransition(event.stamp.record_id, len(self.transitions), after.time_s, effect, before, after))
                self.history.append(after)
        self._apply_focus_until(time_s)
        self.events.extend(events)
        self._seen_ids, self._held, self.time_s = ids, held, time_s

    def _apply_focus_until(self, time_s: float) -> None:
        while self._focus_index < len(self.plan.focus_changes):
            change = self.plan.focus_changes[self._focus_index]
            if change.time_s > time_s + 1e-9:
                break
            current = self.history[-1]
            self.history.append(TextSnapshot(change.time_s, current.text, current.cursor, change.focused))
            self._focus_index += 1
