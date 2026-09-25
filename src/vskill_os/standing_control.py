"""Bounded Cartesian execution with one physics clock and independent checks.

The driver owns joint control.  This module only requests two index-tip points,
observes all keyboard switches, and commits characters in their planned order.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Mapping, Protocol

from .simulation.keyboard_bridge import KeyboardEventBridge, KeycapSample, KeyEvent, KeyEventType
from .standing_typing import (
    HANDS, KeyTarget, ObservedKeyEvent, PressObservation, StandingTypingPlan,
    StandingTypingVerifier, VerificationResult, keyboard_positions,
)
from .typing import SHIFTED

Point = tuple[float, float, float]


class PhysicalFrameLike(Protocol):
    tick: int
    time_s: float
    tips: Mapping[str, Point]
    travel: Mapping[str, float]
    forces: Mapping[str, float]
    index_forces: Mapping[str, Mapping[str, float]]
    source: str


class StandingDriver(Protocol):
    def observe(self) -> PhysicalFrameLike: ...
    def step(self, targets: Mapping[str, Point]) -> PhysicalFrameLike: ...


class TextSink(Protocol):
    source: str
    def consume(self, event: KeyEvent) -> None: ...
    def snapshot(self) -> str: ...


class DigitalTextBuffer:
    """Independent event consumer; this is a digital buffer, never camera OCR."""
    source = "keyboard_event_buffer"

    def __init__(self):
        self.text = ""
        self._shifted = {key: char for char, key in SHIFTED.items()}

    def consume(self, event: KeyEvent) -> None:
        if event.event_type is not KeyEventType.KEYDOWN:
            return
        key = event.key
        if key == "backspace":
            self.text = self.text[:-1]
        elif key in {"space", "enter", "tab"}:
            self.text += {"space": " ", "enter": "\n", "tab": "\t"}[key]
        elif len(key) == 1:
            shifted = any(mod.endswith("shift") for mod in event.modifiers)
            self.text += self._shifted.get(key, key.upper()) if shifted else key

    def snapshot(self) -> str:
        return self.text


@dataclass(frozen=True, slots=True)
class CleanupResult:
    attempted: bool
    physical_clear: bool
    keys_down: tuple[str, ...]
    keys_in_contact: tuple[str, ...]
    depressed_keys: tuple[str, ...]
    steps: int
    error: str | None = None


@dataclass(frozen=True, slots=True)
class ControllerResult:
    succeeded: bool
    failure_code: str | None
    failure_message: str
    verification: VerificationResult
    cleanup: CleanupResult
    observations: tuple[PressObservation, ...]
    events: tuple[ObservedKeyEvent, ...]
    observed_text: str
    observed_text_source: str
    phase_trace: tuple[dict[str, object], ...]
    physics_steps: int
    plan_id: str
    maximum_force_n: float

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result.update({"schema": "standing_typing_controller_result", "version": "1.0.0", "text_evidence_is_ocr": False, "joint_commands_in_controller": False})
        return result


class ControlFailure(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class StandingTypingController:
    """Finite-horizon two-index controller, with physical cleanup on failure.

    Index-to-key pair forces prove finger identity.  Every switch is sampled,
    including unplanned switches.  The bridge is never forcibly cleared to
    manufacture release evidence.  A successful result is still an oracle and
    digital-buffer result; the caller must separately review visual evidence.
    """

    TIP_CONTACT_OFFSET_M = 0.013
    HOVER_M = 0.035

    def __init__(self, driver: StandingDriver, *, maximum_total_steps: int = 30000,
                 maximum_phase_steps: int = 600, maximum_force_n: float = 8.0,
                 minimum_tip_distance_m: float = 0.025, text_sink: TextSink | None = None,
                 tip_contact_offset_m: float = 0.013, maximum_press_depth_m: float = 0.012):
        if maximum_total_steps <= 0 or maximum_phase_steps <= 0:
            raise ValueError("step budgets must be positive")
        if not all(math.isfinite(x) and x > 0 for x in (maximum_force_n, minimum_tip_distance_m)):
            raise ValueError("safety limits must be finite and positive")
        self.driver = driver
        self.maximum_total_steps = maximum_total_steps
        self.maximum_phase_steps = maximum_phase_steps
        self.maximum_force_n = maximum_force_n
        self.minimum_tip_distance_m = minimum_tip_distance_m
        if not math.isfinite(tip_contact_offset_m) or not 0 <= tip_contact_offset_m <= 0.02:
            raise ValueError("invalid tip/contact geometry")
        if not math.isfinite(maximum_press_depth_m) or not 0 < maximum_press_depth_m <= 0.012:
            raise ValueError("invalid press depth bound")
        self.TIP_CONTACT_OFFSET_M = tip_contact_offset_m
        self.maximum_press_depth_m = maximum_press_depth_m
        self.sink = text_sink or DigitalTextBuffer()
        self.positions = keyboard_positions()
        self._used = False

    def run(self, plan: StandingTypingPlan) -> ControllerResult:
        if self._used:
            raise ValueError("a controller instance executes exactly one plan; create a new instance for another episode")
        self._used = True
        self.plan = plan
        self.bridge = KeyboardEventBridge()
        self.events: list[ObservedKeyEvent] = []
        self.observations: list[PressObservation] = []
        self.trace: list[dict[str, object]] = []
        self.targets: dict[str, Point] = {}
        self.frame: PhysicalFrameLike | None = None
        self.steps = 0
        self.maximum_observed_force = 0.0
        self.current_step = -1
        self.phase = "initialization"
        self.owners: dict[str, str] = {}
        self.held: set[str] = set()
        self.records: dict[tuple[int, str, str], dict[str, float]] = {}
        failure_code = None
        failure_message = ""
        try:
            initial_frame = self.driver.observe()
            self._validate_frame(initial_frame)
            self.frame = initial_frame
            if self.sink.snapshot():
                raise ControlFailure("TEXT_BUFFER_NOT_EMPTY", "a fresh digital text buffer is required")
            self.targets = {h: tuple(self.frame.tips[h]) for h in HANDS}
            self._sample(self.frame)
            self._guard(self.frame)
            if any(v > 0.0012 for v in self.frame.travel.values()):
                raise ControlFailure("INITIAL_KEY_DEPRESSED", "episode did not start with released keys")
            for wave in plan.waves:
                self.current_step = -1
                self._lift_both()
                if wave.parked_hands:
                    self._move({h: plan.park_positions[h] for h in wave.parked_hands}, "park")
                self._move({t.hand: self._hover(t) for t in wave.targets}, "coordinated_prepose")
                for index in wave.step_indices:
                    step = plan.steps[index]
                    self.current_step = index
                    for target in step.targets:
                        self._press(target)
                    for target in reversed(step.targets):
                        self._release(target)
            self.current_step = -1
            self._lift_both()
            self._move(plan.park_positions, "final_park")
        except KeyboardInterrupt:
            failure_code, failure_message = "CANCELLED", "typing was cancelled; physical release was requested"
        except ControlFailure as exc:
            failure_code, failure_message = exc.code, str(exc)
        except Exception as exc:
            failure_code, failure_message = "DRIVER_EXCEPTION", f"{type(exc).__name__}: {exc}"
        if failure_code and self.frame is not None:
            self.trace.append({"phase": "failure_snapshot", "step_index": self.current_step,
                "tick": self.frame.tick, "failure_code": failure_code,
                "tips": dict(self.frame.tips), "requested_tips": dict(self.targets),
                "contact_forces": {k: v for k, v in self.frame.forces.items() if v > 0.01},
                "key_travel": {k: v for k, v in self.frame.travel.items() if v > 0.0001}})
        cleanup = self._cleanup() if failure_code else self._cleanup_state(False, 0)
        verification = StandingTypingVerifier().verify(plan, observations=self.observations,
            events=self.events, observed_text=self.sink.snapshot())
        if not failure_code and not cleanup.physical_clear:
            failure_code, failure_message = "PHYSICAL_RELEASE_MISSING", "final physical keyboard state is not clear"
        if not failure_code and not verification.succeeded:
            failure_code, failure_message = verification.failure_codes[0], "independent verification rejected the trace"
        return ControllerResult(failure_code is None and verification.succeeded, failure_code, failure_message,
            verification, cleanup, tuple(self.observations), tuple(self.events), self.sink.snapshot(),
            self.sink.source, tuple(self.trace), self.steps, plan.plan_id, self.maximum_observed_force)

    def _set_phase(self, phase: str) -> None:
        self.phase = phase
        self.trace.append({"phase": phase, "step_index": self.current_step,
                           "tick": None if self.frame is None else self.frame.tick})

    def _hover(self, target: KeyTarget) -> Point:
        x, y, z = target.xyz_m
        return x, y, z + self.TIP_CONTACT_OFFSET_M + self.HOVER_M

    def _lift_both(self, *, cleanup: bool = False) -> None:
        assert self.frame is not None
        safe_z = 0.7602 + self.TIP_CONTACT_OFFSET_M + self.HOVER_M
        self._move({h: (p[0], p[1], max(p[2], safe_z)) for h, p in self.frame.tips.items()}, "cleanup_lift" if cleanup else "lift", cleanup=cleanup)

    def _move(self, destinations: Mapping[str, Point], phase: str, *, cleanup: bool = False) -> None:
        self._set_phase(phase)
        for _ in range(self.maximum_phase_steps):
            assert self.frame is not None
            if all(math.dist(self.frame.tips[h], goal) <= 0.004 for h, goal in destinations.items()):
                self.targets.update(destinations)
                return
            for hand, goal in destinations.items():
                old = self.targets.get(hand, tuple(self.frame.tips[hand]))
                delta = tuple(b - a for a, b in zip(old, goal))
                distance = math.sqrt(sum(d*d for d in delta))
                factor = min(1.0, 0.001 / max(distance, 1e-12))
                self.targets[hand] = tuple(a + d * factor for a, d in zip(old, delta))
            self._tick(cleanup=cleanup)
        raise ControlFailure("MOTION_TIMEOUT", f"{phase} did not reach its bounded target")

    def _press(self, target: KeyTarget) -> None:
        x, y, z = target.xyz_m
        identity = self.current_step, target.key, target.hand
        self.records[identity] = {"force": 0.0, "travel": 0.0, "tip_error": float("inf")}
        self._move({target.hand: (x, y, z + self.TIP_CONTACT_OFFSET_M + 0.002)}, "approach")
        self._set_phase("press")
        depth_limit = z + self.TIP_CONTACT_OFFSET_M - self.maximum_press_depth_m
        for _ in range(min(self.maximum_phase_steps, 240)):
            assert self.frame is not None
            if target.key in self.bridge.keys_down:
                if self.frame.index_forces[target.key][target.hand] <= 0.01:
                    raise ControlFailure("INDEX_CONTACT_MISSING", "key actuation lacks intended index-to-key pair contact")
                self.held.add(target.key)
                break
            current = self.targets[target.hand]
            # Never seek unbounded penetration if a target cannot actuate.
            # At the depth limit, wait for measured tracking/debounce instead
            # of treating a commanded point as already physically reached.
            self.targets[target.hand] = current[0], current[1], max(depth_limit, current[2] - 0.00015)
            self._tick()
        else:
            raise ControlFailure("MISSED_CONTACT", f"{target.key} did not actuate at bounded depth within its step budget")
        self._set_phase("hold")
        for _ in range(3):
            self._tick()

    def _release(self, target: KeyTarget) -> None:
        self.held.discard(target.key)
        self._move({target.hand: self._hover(target)}, "release")
        self._set_phase("release_settle")
        for _ in range(min(self.maximum_phase_steps, 120)):
            assert self.frame is not None
            if target.key not in self.bridge.keys_down and self.frame.forces[target.key] <= 0.01 and self.frame.travel[target.key] <= 0.0012:
                record = self.records.pop((self.current_step, target.key, target.hand))
                clearance = self.frame.tips[target.hand][2] - target.xyz_m[2] - self.TIP_CONTACT_OFFSET_M
                self.observations.append(PressObservation(self.current_step, target.key, target.hand,
                    record["force"], record["travel"], True, clearance,
                    source=self.frame.source, tip_error_m=record["tip_error"]))
                return
            self._tick()
        raise ControlFailure("STUCK_KEY", f"{target.key} remains physically depressed or in contact")

    def _tick(self, *, cleanup: bool = False) -> None:
        if not cleanup and self.steps >= self.maximum_total_steps:
            raise ControlFailure("EPISODE_STEP_LIMIT", "total physics-step budget exhausted")
        old = self.frame
        frame = self.driver.step(dict(self.targets))
        self.steps += 1
        self._validate_frame(frame)
        if old is not None and (frame.tick <= old.tick or frame.time_s <= old.time_s):
            raise ControlFailure("PHYSICS_CLOCK_INVALID", "physics tick and time must strictly increase")
        self.frame = frame
        self._sample(frame, cleanup=cleanup)
        if cleanup and self.phase == "cleanup_park":
            # A release escape may lift out of an existing force violation.
            # Lateral parking must never drag a hand across a depressed key or
            # bypass separation checks after that escape has completed.
            if any(frame.forces[k] > 0.01 or frame.travel[k] > 0.0012 for k in self.positions):
                contacts = [k for k in self.positions if frame.forces[k] > 0.01 or frame.travel[k] > 0.0012]
                raise ControlFailure("PHYSICAL_RELEASE_MISSING", f"key contact returned during lateral cleanup: {contacts}")
            if math.dist(frame.tips["left"], frame.tips["right"]) < self.minimum_tip_distance_m or frame.tips["left"][1] < frame.tips["right"][1]:
                raise ControlFailure("HAND_COLLISION_RISK", "lateral cleanup violated hand separation")
        elif not cleanup:
            self._guard(frame)
            if not self.held.issubset(self.bridge.keys_down):
                raise ControlFailure("HOLD_LOST", "a committed held key released prematurely")

    def _sample(self, frame: PhysicalFrameLike, *, cleanup: bool = False) -> None:
        mismatch = False
        for key in sorted(self.positions):
            force = frame.forces[key]
            self.maximum_observed_force = max(self.maximum_observed_force, force)
            for (index, recorded_key, hand), record in self.records.items():
                if recorded_key == key and frame.index_forces[key][hand] > 0.01:
                    record["force"] = max(record["force"], force)
                    record["travel"] = max(record["travel"], frame.travel[key])
                    record["tip_error"] = min(record["tip_error"], math.dist(frame.tips[hand][:2], self.positions[key][:2]))
            sample = KeycapSample(key, frame.travel[key], force > 0.01, frame.time_s,
                source=frame.source, sample_id=f"standing:{frame.tick}:{key}")
            for event in self.bridge.process(sample, now=frame.time_s):
                if event.event_type is KeyEventType.KEYDOWN:
                    pair = frame.index_forces[key]
                    owner = max(HANDS, key=lambda hand: pair[hand])
                    if pair[owner] <= 0.01:
                        owner = "unknown"
                    self.owners[key] = owner
                else:
                    owner = self.owners.pop(key, "unknown")
                observed = ObservedKeyEvent(self.current_step, event.event_type.value, key, owner,
                    event.monotonic_time, event.source)
                self.events.append(observed)
                self.sink.consume(event)
                i = len(self.events) - 1
                if i >= len(self.plan.expected_events):
                    mismatch = True
                else:
                    expected = self.plan.expected_events[i]
                    if (observed.step_index, observed.event_type, observed.key, observed.hand) != (expected.step_index, expected.event_type, expected.key, expected.hand):
                        mismatch = True
        if mismatch and not cleanup:
            raise ControlFailure("UNEXPECTED_KEY_EVENT", "all-key sensor observed an out-of-plan or wrongly attributed key event")

    def _validate_frame(self, frame: PhysicalFrameLike) -> None:
        if frame.source != "isaac_physx":
            raise ControlFailure("UNTRUSTED_FRAME", "physical driver must provide isaac_physx observations")
        if set(frame.tips) != set(HANDS) or any(set(values) != set(self.positions) for values in (frame.travel, frame.forces, frame.index_forces)):
            raise ControlFailure("INCOMPLETE_SENSOR_COVERAGE", "both fingertips and all 74 keys are required")
        numbers = [frame.time_s]
        for hand in HANDS:
            if len(frame.tips[hand]) != 3:
                raise ControlFailure("INVALID_PHYSICAL_FRAME", "index tip must have three coordinates")
            numbers.extend(frame.tips[hand])
        for key in self.positions:
            if set(frame.index_forces[key]) != set(HANDS):
                raise ControlFailure("INCOMPLETE_SENSOR_COVERAGE", "every key requires both index contact pairs")
            values = (frame.travel[key], frame.forces[key], *frame.index_forces[key].values())
            if any(v < 0 for v in values):
                raise ControlFailure("INVALID_PHYSICAL_FRAME", "travel and force must be nonnegative")
            numbers.extend(values)
        if not all(math.isfinite(v) for v in numbers):
            raise ControlFailure("INVALID_PHYSICAL_FRAME", "nonfinite physics observation")

    def _guard(self, frame: PhysicalFrameLike) -> None:
        if any(force > self.maximum_force_n for force in frame.forces.values()):
            raise ControlFailure("FORCE_LIMIT", f"independent force guard exceeded: {[(k, f) for k, f in frame.forces.items() if f > self.maximum_force_n]}")
        allowed_keys = {t.key for t in self.plan.steps[self.current_step].targets} if self.current_step >= 0 else set()
        if any(force > 0.01 and key not in allowed_keys for key, force in frame.forces.items()):
            raise ControlFailure("ADJACENT_KEY_CONTACT", f"unreserved key contact: {[(k, f) for k, f in frame.forces.items() if f > 0.01 and k not in allowed_keys]}")
        if math.dist(frame.tips["left"], frame.tips["right"]) < self.minimum_tip_distance_m:
            raise ControlFailure("HAND_COLLISION_RISK", "index tip separation is below the configured limit")
        if frame.tips["left"][1] < frame.tips["right"][1]:
            raise ControlFailure("HAND_CROSSING", "left index crossed the right index")

    def _cleanup(self) -> CleanupResult:
        start = self.steps
        self.held.clear()
        error = None
        try:
            if self.frame is None or not self.targets:
                return self._cleanup_state(True, 0, "no usable physical frame/targets")
            # A cancelled/failed driver.step may already have advanced physics.
            # Re-read sensors so an old, clear frame cannot certify cleanup.
            fresh = self.driver.observe()
            self._validate_frame(fresh)
            if fresh.tick < self.frame.tick or fresh.time_s < self.frame.time_s:
                raise ControlFailure("PHYSICS_CLOCK_INVALID", "cleanup observation regressed")
            self.frame = fresh
            self._sample(fresh, cleanup=True)
            self._lift_both(cleanup=True)
            self._set_phase("cleanup_release_settle")
            for _ in range(min(120, self.maximum_phase_steps)):
                if self._cleanup_state(True, self.steps - start).physical_clear:
                    break
                self._tick(cleanup=True)
            if not self._cleanup_state(True, self.steps - start).physical_clear:
                raise ControlFailure("PHYSICAL_RELEASE_MISSING", "physical release/separation was not established; lateral parking withheld")
            self._move(self.plan.park_positions, "cleanup_park", cleanup=True)
            self._set_phase("cleanup_settle")
            for _ in range(min(120, self.maximum_phase_steps)):
                if self._cleanup_state(True, self.steps - start).physical_clear:
                    break
                self._tick(cleanup=True)
        except (Exception, KeyboardInterrupt) as exc:
            error = f"{type(exc).__name__}: {exc}"
        return self._cleanup_state(True, self.steps - start, error)

    def _cleanup_state(self, attempted: bool, steps: int, error: str | None = None) -> CleanupResult:
        if self.frame is None:
            return CleanupResult(attempted, False, self.bridge.keys_down, (), (), steps, error)
        try:
            self._validate_frame(self.frame)
        except Exception as exc:
            return CleanupResult(attempted, False, self.bridge.keys_down, (), (), steps,
                error or f"cleanup lacks a trusted complete physical frame: {type(exc).__name__}: {exc}")
        contacts = tuple(sorted(k for k, force in self.frame.forces.items() if force > 0.01))
        depressed = tuple(sorted(k for k, travel in self.frame.travel.items() if travel > 0.0012))
        safe_z = 0.7602 + self.TIP_CONTACT_OFFSET_M + 0.010
        tips_clear = set(self.frame.tips) == set(HANDS) and all(p[2] >= safe_z for p in self.frame.tips.values())
        separated = math.dist(self.frame.tips["left"], self.frame.tips["right"]) >= self.minimum_tip_distance_m and self.frame.tips["left"][1] >= self.frame.tips["right"][1]
        physical_clear = not contacts and not depressed and not self.bridge.keys_down and tips_clear and separated and error is None
        return CleanupResult(attempted, physical_clear, self.bridge.keys_down, contacts, depressed, steps, error)
