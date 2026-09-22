"""Deterministic, index-only cooperative typing contracts and replay.

No simulator or joint-control dependency belongs here.  Replay fabricates named
synthetic observations for contract testing; it is never physical acceptance.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path
from typing import Iterable

from .typing import SHIFTED, normalize_key

VERSION = "1.0.0"
HANDS = ("left", "right")
PARK_POSITIONS = {"left": (0.64, 0.16, 0.84), "right": (0.64, -0.16, 0.84)}


class StandingPlanningError(ValueError):
    """A plan cannot be executed by the two available index fingers."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def keyboard_positions() -> dict[str, tuple[float, float, float]]:
    """Pure-domain copy of the versioned P1 ANSI 74-key geometry, in metres.

    +X points away from the standing robot; +Y is the operator's left.
    The function's equivalence to the optional scene author is tested.
    """
    rows = (
        (0.0, (("escape", 1.0), *((f"f{i}", 1.0) for i in range(1, 13)))),
        (1.25, tuple((k, 1.0) for k in "`1234567890-=") + (("backspace", 2.0),)),
        (2.25, (("tab", 1.5),) + tuple((k, 1.0) for k in "qwertyuiop[]\\") + (("delete", 1.5),)),
        (3.25, (("capslock", 1.75),) + tuple((k, 1.0) for k in "asdfghjkl;'") + (("enter", 2.25),)),
        (4.25, (("left_shift", 2.25),) + tuple((k, 1.0) for k in "zxcvbnm,./") + (("right_shift", 2.75),)),
        (5.25, (("left_ctrl", 1.25), ("left_super", 1.25), ("left_alt", 1.25), ("space", 6.25), ("right_alt", 1.25), ("menu", 1.25), ("right_ctrl", 1.25))),
    )
    result = {}
    for row, keys in rows:
        column = 0.0
        for key, width in keys:
            result[key] = (0.72 + (5.25 - row) * 0.01905, 0.145 - (column + width / 2) * 0.01905, 0.7602)
            column += width
    return result


@dataclass(frozen=True, slots=True)
class KeyTarget:
    hand: str
    key: str
    xyz_m: tuple[float, float, float]
    finger: str = "index"

    @property
    def executor(self) -> str:
        return f"{self.hand}.{self.finger}"


@dataclass(frozen=True, slots=True)
class PlannedEvent:
    step_index: int
    event_type: str
    key: str
    hand: str


@dataclass(frozen=True, slots=True)
class TypingStep:
    index: int
    character: str
    primary: KeyTarget
    modifier: KeyTarget | None = None
    wave: int = 0

    @property
    def targets(self) -> tuple[KeyTarget, ...]:
        return (self.modifier, self.primary) if self.modifier else (self.primary,)


@dataclass(frozen=True, slots=True)
class PrepositionWave:
    index: int
    step_indices: tuple[int, ...]
    targets: tuple[KeyTarget, ...]
    parked_hands: tuple[str, ...]
    # All fingers first retract vertically, then move above the keybed.  A
    # shared physics controller updates both targets in one simulation step.
    minimum_clearance_m: float = 0.035
    ordered_key_commits: bool = True


@dataclass(frozen=True, slots=True)
class StandingTypingPlan:
    text: str
    steps: tuple[TypingStep, ...]
    waves: tuple[PrepositionWave, ...]
    expected_events: tuple[PlannedEvent, ...]
    movement_cost_m: float
    minimum_hand_separation_m: float = 0.025
    version: str = VERSION
    strategy: str = "global-dp-two-index-coordinated-preposition-ordered-commit"

    @property
    def park_positions(self) -> dict[str, tuple[float, float, float]]:
        return dict(PARK_POSITIONS)

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result.update({
            "schema": "standing_bimanual_typing_plan",
            "posture": "standing_fixed_base",
            "park_positions": self.park_positions,
            "preconditions": ["standing_fixed_base_verified", "keyboard_calibrated", "text_sink_ready"],
            "postconditions": ["exact_text_observed", "all_keys_physically_released"],
            "resources": ["left_arm", "right_arm", "left_dex3.index", "right_dex3.index", "keyboard"],
            "failure_codes": ["UNREACHABLE_KEY", "TWO_INDEX_CAPACITY", "HAND_COLLISION_RISK", "CONTACT_MISSING", "FORCE_LIMIT", "EVENT_MISMATCH", "PHYSICAL_RELEASE_MISSING", "TEXT_MISMATCH"],
            "joint_commands": False,
        })
        return result

    @property
    def plan_id(self) -> str:
        return hashlib.sha256(json.dumps(self.to_dict(), sort_keys=True).encode()).hexdigest()


class StandingTypingPlanner:
    """Globally minimize hand travel, preserving reach and no-crossing gates.

    DP state stores the last target of each index.  Each transition assigns a
    character and optional opposite-hand Shift; ties are broken lexically.
    Travel is a comparative key-plane objective, not a physical duration or
    proof of reachability.  The adapter still checks IK and collision guards.
    """

    def __init__(self, *, minimum_hand_separation_m: float = 0.025):
        if not math.isfinite(minimum_hand_separation_m) or minimum_hand_separation_m <= 0:
            raise ValueError("hand separation must be finite and positive")
        self.positions = keyboard_positions()
        self.minimum_hand_separation_m = minimum_hand_separation_m

    def target(self, hand: str, key: str) -> KeyTarget:
        key = normalize_key(key)
        if hand not in HANDS or key not in self.positions:
            raise StandingPlanningError("UNREACHABLE_KEY", f"Unknown executor or key: {hand}/{key}")
        point = self.positions[key]
        # Deliberate 55 mm central overlap; reservations arbitrate this space.
        left_reachable = -0.020 <= point[1] <= 0.15
        right_reachable = -0.17 <= point[1] <= 0.035
        if not (0.715 <= point[0] <= 0.825 and (left_reachable if hand == "left" else right_reachable)):
            raise StandingPlanningError("UNREACHABLE_KEY", f"{hand}.index cannot reach {key}")
        return KeyTarget(hand, key, point)

    def plan_chord(self, keys: Iterable[str]) -> tuple[KeyTarget, ...]:
        logical = tuple(keys)
        if len(logical) > 2:
            raise StandingPlanningError("TWO_INDEX_CAPACITY", "Two index fingers cannot hold three keys; Ctrl+Alt+T is unsupported")
        if not logical:
            raise StandingPlanningError("NO_PRIMARY_KEY", "A key is required")
        modifiers = [k for k in logical if normalize_key(k).endswith(("shift", "ctrl", "alt"))]
        primaries = [k for k in logical if k not in modifiers]
        if len(primaries) != 1:
            raise StandingPlanningError("NO_PRIMARY_KEY", "A chord requires exactly one primary key")
        candidates = self._candidates(normalize_key(primaries[0]), modifiers[0] if modifiers else None)
        if not candidates:
            raise StandingPlanningError("UNREACHABLE_KEY", "No noncrossing two-index chord is reachable")
        return min(candidates, key=lambda targets: tuple((t.hand, t.key) for t in targets))

    def plan_text(self, text: str) -> StandingTypingPlan:
        if not text or len(text) > 256:
            raise ValueError("text must contain 1 to 256 characters")
        # (last-left-key, last-right-key) -> cost, assignments, tie signature
        states = {(None, None): (0.0, (), ())}
        for char in text:
            key, shifted = self._character(char)
            candidates = self._candidates(key, "shift" if shifted else None)
            if not candidates:
                raise StandingPlanningError("UNREACHABLE_KEY", f"No reachable two-index assignment for {char!r}")
            following = {}
            for previous, (cost, path, signature) in states.items():
                for targets in candidates:
                    updated = list(previous)
                    extra = 0.0
                    for target in targets:
                        i = HANDS.index(target.hand)
                        origin = self.positions[previous[i]] if previous[i] else PARK_POSITIONS[target.hand]
                        extra += math.dist(origin[:2], target.xyz_m[:2])
                        updated[i] = target.key
                    next_signature = (*signature, tuple((t.hand, t.key) for t in targets))
                    value = (cost + extra, (*path, targets), next_signature)
                    state = tuple(updated)
                    old = following.get(state)
                    if old is None or (round(value[0], 12), value[2]) < (round(old[0], 12), old[2]):
                        following[state] = value
            states = following
        cost, path, _ = min(states.values(), key=lambda item: (round(item[0], 12), item[2]))
        steps = [TypingStep(i, c, targets[-1], targets[0] if len(targets) == 2 else None) for i, (c, targets) in enumerate(zip(text, path))]
        waves = []
        finalized = []
        i = 0
        while i < len(steps):
            group = [steps[i]]
            targets = steps[i].targets
            if i + 1 < len(steps) and not steps[i].modifier and not steps[i + 1].modifier:
                pair = (*targets, *steps[i + 1].targets)
                if self._noncrossing(pair):
                    group.append(steps[i + 1])
                    targets = pair
            index = len(waves)
            waves.append(PrepositionWave(index, tuple(s.index for s in group), targets, tuple(h for h in HANDS if all(t.hand != h for t in targets))))
            finalized.extend(TypingStep(s.index, s.character, s.primary, s.modifier, index) for s in group)
            i += len(group)
        events = []
        for step in finalized:
            for target in step.targets:
                events.append(PlannedEvent(step.index, "keydown", target.key, target.hand))
            for target in reversed(step.targets):
                events.append(PlannedEvent(step.index, "keyup", target.key, target.hand))
        return StandingTypingPlan(text, tuple(finalized), tuple(waves), tuple(events), round(cost, 9), self.minimum_hand_separation_m)

    @staticmethod
    def _character(char: str) -> tuple[str, bool]:
        if char in {" ", "\n", "\b", "\t"}:
            return {" ": "space", "\n": "enter", "\b": "backspace", "\t": "tab"}[char], False
        if char in SHIFTED:
            return SHIFTED[char], True
        if char.isascii() and char.isalpha():
            return char.lower(), char.isupper()
        if char in "`1234567890-=[]\\;',./":
            return char, False
        raise StandingPlanningError("UNSUPPORTED_CHARACTER", f"Unsupported keyboard character: {char!r}")

    def _candidates(self, key: str, modifier: str | None) -> tuple[tuple[KeyTarget, ...], ...]:
        candidates = []
        for hand in HANDS:
            try:
                primary = self.target(hand, key)
                targets = (primary,)
                if modifier:
                    other = "right" if hand == "left" else "left"
                    physical = modifier if modifier.startswith(("left_", "right_")) else f"{other}_{modifier.lower().replace('control', 'ctrl')}"
                    targets = (self.target(other, physical), primary)
                    if not self._noncrossing(targets):
                        continue
                candidates.append(targets)
            except StandingPlanningError:
                continue
        return tuple(candidates)

    def _noncrossing(self, targets: tuple[KeyTarget, ...]) -> bool:
        if len(targets) != 2 or {t.hand for t in targets} != set(HANDS):
            return False
        by_hand = {t.hand: t for t in targets}
        return by_hand["left"].xyz_m[1] - by_hand["right"].xyz_m[1] >= self.minimum_hand_separation_m


@dataclass(frozen=True, slots=True)
class ObservedKeyEvent:
    step_index: int
    event_type: str
    key: str
    hand: str
    timestamp_s: float
    source: str = "isaac_keycap_bridge"


@dataclass(frozen=True, slots=True)
class PressObservation:
    step_index: int
    key: str
    hand: str
    contact_force_n: float
    travel_m: float
    released: bool
    clearance_m: float
    source: str = "isaac_physx"
    finger: str = "index"
    adjacent_contacts: tuple[str, ...] = ()
    tip_error_m: float = 0.0


@dataclass(frozen=True, slots=True)
class VerificationResult:
    succeeded: bool
    failure_codes: tuple[str, ...]
    physical_acceptance: bool
    evidence_mode: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class StandingTypingVerifier:
    """Checks external observations; controller success is not evidence."""

    def verify(self, plan: StandingTypingPlan, *, observations: Iterable[PressObservation], events: Iterable[ObservedKeyEvent], observed_text: str, allow_synthetic: bool = False) -> VerificationResult:
        observed = tuple(observations)
        event_stream = tuple(events)
        failures = []
        synthetic = any(x.source == "synthetic_replay" for x in (*observed, *event_stream))
        contact_sources = {"isaac_physx", "isaac_physx_contact_sensor"}
        event_sources = {"isaac_keycap_bridge", "keyboard_event_bridge"}
        if allow_synthetic:
            contact_sources.add("synthetic_replay")
            event_sources.add("synthetic_replay")
        if any(o.source not in contact_sources for o in observed) or any(e.source not in event_sources for e in event_stream):
            failures.append("UNTRUSTED_EVIDENCE")
        expected_identity = [(s.index, t.key, t.hand) for s in plan.steps for t in s.targets]
        observed_identity = [(o.step_index, o.key, o.hand) for o in observed]
        if sorted(expected_identity) != sorted(observed_identity):
            failures.append("CONTACT_COVERAGE_MISMATCH")
        for o in observed:
            values = (o.contact_force_n, o.travel_m, o.clearance_m, o.tip_error_m)
            if not all(math.isfinite(v) for v in values):
                failures.append("INVALID_PHYSICAL_EVIDENCE")
                continue
            if o.finger != "index":
                failures.append("WRONG_FINGER")
            if o.contact_force_n <= 0.01 or o.travel_m < 0.002 or o.tip_error_m < 0 or o.tip_error_m > 0.012:
                failures.append("CONTACT_MISSING")
            if o.contact_force_n > 8.0:
                failures.append("FORCE_LIMIT")
            if o.adjacent_contacts:
                failures.append("ADJACENT_KEY_CONTACT")
            if not o.released or o.clearance_m < 0.010:
                failures.append("PHYSICAL_RELEASE_MISSING")
        expected_events = [(e.step_index, e.event_type, e.key, e.hand) for e in plan.expected_events]
        actual_events = [(e.step_index, e.event_type, e.key, e.hand) for e in event_stream]
        if expected_events != actual_events:
            failures.append("EVENT_MISMATCH")
        if any(not math.isfinite(e.timestamp_s) for e in event_stream) or any(b.timestamp_s <= a.timestamp_s for a, b in zip(event_stream, event_stream[1:])):
            failures.append("EVENT_TIME_INVALID")
        if observed_text != expected_text_after_keys(plan.text):
            failures.append("TEXT_MISMATCH")
        codes = tuple(dict.fromkeys(failures))
        return VerificationResult(not codes, codes, not codes and not synthetic, "synthetic" if synthetic else "physical")


def expected_text_after_keys(text: str) -> str:
    result = ""
    for character in text:
        result = result[:-1] if character == "\b" else result + character
    return result


def synthetic_replay(plan: StandingTypingPlan) -> dict[str, object]:
    """Explicit contract-only evidence generator, never a simulator claim."""
    observations = tuple(PressObservation(s.index, t.key, t.hand, 1.5, 0.0022, True, 0.035, source="synthetic_replay") for s in plan.steps for t in s.targets)
    events = tuple(ObservedKeyEvent(e.step_index, e.event_type, e.key, e.hand, (i + 1) * 0.05, "synthetic_replay") for i, e in enumerate(plan.expected_events))
    result = StandingTypingVerifier().verify(plan, observations=observations, events=events, observed_text=expected_text_after_keys(plan.text), allow_synthetic=True)
    return {"schema": "standing_typing_replay", "version": VERSION, "capability": "synthetic_contract_replay_only", "physical_simulation_executed": False, "plan_id": plan.plan_id, "plan": plan.to_dict(), "verification": result.to_dict(), "observations": [asdict(o) for o in observations], "events": [asdict(e) for e in events], "observed_text": expected_text_after_keys(plan.text)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Plan standing Dex3-1 index-only typing; synthetic replay is not physical acceptance")
    parser.add_argument("--text", default="hello world")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--plan-only", action="store_true")
    args = parser.parse_args(argv)
    try:
        plan = StandingTypingPlanner().plan_text(args.text)
    except (ValueError, StandingPlanningError) as exc:
        parser.error(str(exc))
    report = plan.to_dict() if args.plan_only else synthetic_replay(plan)
    payload = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
        print(json.dumps({"output": str(args.output), "plan_id": plan.plan_id, "steps": len(plan.steps), "waves": len(plan.waves), "physical_simulation_executed": False}))
    else:
        print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
