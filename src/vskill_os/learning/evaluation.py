"""Independent offline evidence checks, never a training reward or controller.

This evaluator derives threshold crossings from raw samples without replaying
KeyboardEventBridge. Its v1 acceptance scope is contract replay only. Ingesting
Isaac/camera data requires a separately validated adapter and provenance policy.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import Literal

from .contracts import EpisodeRecord, TaskSpec
from .serialization import JsonRecord

EPSILON = 1e-9


@dataclass(frozen=True, slots=True)
class EvaluationPolicy(JsonRecord):
    version: Literal["vf-offline/1"] = "vf-offline/1"
    press_threshold_m: float = 0.002
    release_threshold_m: float = 0.0012
    release_dwell_s: float = 0.01
    maximum_sample_gap_s: float = 0.025
    maximum_contact_force_n: float = 20.0
    maximum_screen_age_s: float = 0.25
    minimum_screen_confidence: float = 0.9

    def __post_init__(self) -> None:
        JsonRecord.__post_init__(self)
        if not 0 <= self.release_threshold_m < self.press_threshold_m:
            raise ValueError("press threshold must exceed the nonnegative release threshold")
        if min(self.release_dwell_s, self.maximum_sample_gap_s, self.maximum_contact_force_n,
               self.maximum_screen_age_s) <= 0 or not 0 < self.minimum_screen_confidence <= 1:
            raise ValueError("evaluation limits must be positive, with confidence in (0, 1]")


@dataclass(frozen=True, slots=True)
class Finding(JsonRecord):
    code: str
    record_id: str
    detail: str


@dataclass(frozen=True, slots=True)
class EvaluationResult(JsonRecord):
    evaluator_version: str
    evaluator_sha256: str
    task_sha256: str
    episode_sha256: str
    policy_sha256: str
    capability: Literal["contract_replay"]
    contract_passed: bool
    physical_execution_verified: Literal[False]
    findings: tuple[Finding, ...]
    observed_text: str | None
    event_text: str
    completed_press_release_cycles: int
    keydown_count: int
    dropped_deliveries: int
    verification_count: int
    verification_time_s: float
    elapsed_s: float


@dataclass
class _KeyState:
    down: bool = False
    pending_cycle: bool = False
    armed: bool = False
    clear_since: float | None = None


class IndependentTypingEvaluator:
    """Evaluate against a caller-supplied task and frozen policy.

    The episode cannot redefine its target, monitored keyboard or thresholds.
    Reports are deliberately disconnected from Runtime/Ledger verified facts.
    """

    def __init__(self, policy: EvaluationPolicy | None = None) -> None:
        self.policy = policy or EvaluationPolicy()
        directory = Path(__file__).parent
        self.code_sha256 = hashlib.sha256(b"".join(
            (directory / name).read_bytes() for name in ("evaluation.py", "contracts.py", "serialization.py")
        )).hexdigest()

    def evaluate(self, task: TaskSpec, episode: EpisodeRecord) -> EvaluationResult:
        policy = self.policy
        findings: list[Finding] = []
        seen_findings: set[tuple[str, str]] = set()
        completed = 0
        verification_time = 0.0
        event_text = task.initial_text
        keydowns = tuple(event for event in episode.events if event.event_type == "keydown")

        def fail(code: str, detail: str, record_id: str = "episode") -> None:
            if (code, record_id) not in seen_findings:
                findings.append(Finding(code, record_id, detail))
                seen_findings.add((code, record_id))

        def result() -> EvaluationResult:
            return EvaluationResult(
                policy.version, self.code_sha256, task.sha256(), episode.sha256(), policy.sha256(),
                "contract_replay", not findings, False, tuple(findings),
                episode.text_observations[-1].text if episode.text_observations else None,
                event_text, completed, len(keydowns),
                sum(text.delivery == "dropped" for text in episode.text_observations),
                len(episode.verification_requests), verification_time,
                episode.end_time_s - episode.start_time_s,
            )

        if episode.task_sha256 != task.sha256():
            fail("TASK_MISMATCH", "episode is not bound to the supplied task")
        if episode.manifest.evidence_scope != "contract_replay":
            fail("UNSUPPORTED_SOURCE", "v1 cannot certify physics, camera or hardware evidence")

        # Reject cross-episode/clock contamination before comparing any evidence.
        streams = (episode.observations, episode.events, episode.text_observations,
                   episode.verification_requests, episode.screen_observations)
        record_ids: set[str] = set()
        for stream in streams:
            previous_time = episode.start_time_s
            for index, record in enumerate(stream):
                stamp = record.stamp
                ref = stamp.record_id
                if ref in record_ids:
                    fail("DUPLICATE_RECORD", "record IDs must be unique across all streams", ref)
                record_ids.add(ref)
                if (stamp.env_id, stamp.episode_id, stamp.clock_domain) != (
                    episode.env_id, episode.episode_id, episode.clock_domain
                ):
                    fail("INVALID_EVIDENCE", "record belongs to a different environment, episode or clock", ref)
                if stamp.sequence != index or stamp.capture_time_s + EPSILON < previous_time:
                    fail("INVALID_EVIDENCE", "stream sequence is missing, repeated or out of order", ref)
                previous_time = stamp.capture_time_s
                if stamp.capture_time_s > episode.end_time_s + EPSILON:
                    fail("INVALID_EVIDENCE", "capture occurred after the episode ended", ref)
                if stream is not episode.screen_observations and stamp.arrival_time_s > episode.end_time_s + EPSILON:
                    fail("INVALID_EVIDENCE", "supervisory evidence was not available at the episode boundary", ref)
                if not stamp.source.startswith("synthetic."):
                    fail("UNSUPPORTED_SOURCE", "contract replay records must explicitly declare synthetic sources", ref)

        observations = episode.observations
        if len(observations) < 2:
            fail("INVALID_EVIDENCE", "at least initial and terminal physical samples are required")
        else:
            if (abs(observations[0].stamp.capture_time_s - episode.start_time_s) > EPSILON
                    or abs(observations[-1].stamp.capture_time_s - episode.end_time_s) > EPSILON):
                fail("INVALID_EVIDENCE", "physical evidence must cover both episode boundaries")
            for index, frame in enumerate(observations):
                if (len(frame.keys) != len(task.monitored_keys)
                        or {key.key for key in frame.keys} != set(task.monitored_keys)
                        or tuple(joint.name for joint in frame.joints) != task.joint_names):
                    fail("INVALID_EVIDENCE", "full monitored keyboard and ordered joints must be sampled", frame.stamp.record_id)
                if index:
                    gap = frame.stamp.capture_time_s - observations[index - 1].stamp.capture_time_s
                    if gap <= 0 or gap > policy.maximum_sample_gap_s + EPSILON:
                        fail("SAMPLE_GAP", "physical sampling cannot establish continuous release/safety", frame.stamp.record_id)
        if findings:
            return result()

        bindings = {binding.key: binding.character for binding in task.character_keys}
        permitted_keys = set(bindings) | {task.backspace_key}

        def apply_key(text: str, key: str) -> str:
            return text[:-1] if key == task.backspace_key else text + bindings.get(key, "")

        # Independent physical state machine. A digital KeyUp can precede the
        # end of contact, but a cycle completes only after sustained clearance.
        states = {key: _KeyState() for key in task.monitored_keys}
        expected: Counter = Counter()
        sample_map = {frame.stamp.record_id: frame for frame in observations}
        for index, frame in enumerate(observations):
            stamp = frame.stamp
            if frame.safety_stopped:
                fail("SAFETY_STOP", "independent safety monitor stopped the episode", stamp.record_id)
            for sample in frame.keys:
                state = states[sample.key]
                clear = not sample.in_contact and sample.travel_m <= policy.release_threshold_m
                if sample.normal_force_n > policy.maximum_contact_force_n + EPSILON:
                    fail("EXCESS_FORCE", f"{sample.key} exceeded contact-force limit", stamp.record_id)
                if index == 0 and not clear:
                    fail("INITIAL_STATE", f"{sample.key} was not initially released", stamp.record_id)
                if state.down and sample.travel_m <= policy.release_threshold_m:
                    expected[(stamp.record_id, sample.key, "keyup", "release_threshold")] += 1
                    state.down = False
                elif not state.down and sample.travel_m >= policy.press_threshold_m:
                    if not sample.in_contact:
                        fail("NO_CONTACT", f"{sample.key} reached actuation travel without contact", stamp.record_id)
                    else:
                        if not state.armed:
                            fail("INCOMPLETE_RELEASE", f"{sample.key} was pressed before physical rearming", stamp.record_id)
                        if sample.key not in permitted_keys:
                            fail("UNEXPECTED_KEY", f"{sample.key} is outside the declared input keys", stamp.record_id)
                        expected[(stamp.record_id, sample.key, "keydown", "physical_threshold")] += 1
                        state.down = True
                        state.pending_cycle = True
                        state.armed = False
                if clear:
                    if state.clear_since is None:
                        state.clear_since = stamp.capture_time_s
                    if stamp.capture_time_s - state.clear_since + EPSILON >= policy.release_dwell_s:
                        state.armed = True
                        if state.pending_cycle and not state.down:
                            completed += 1
                            state.pending_cycle = False
                else:
                    state.clear_since = None
        for key, state in states.items():
            if (state.down or state.pending_cycle or state.clear_since is None
                    or episode.end_time_s - state.clear_since + EPSILON < policy.release_dwell_s):
                fail("STUCK_KEY", f"{key} lacks sustained physical release at the episode boundary")

        # Compare observed events to independently derived crossings, not to the
        # controller's success flag or the event bridge's internal key state.
        actual: Counter = Counter()
        for event in episode.events:
            ref = event.stamp.record_id
            if event.reason in {"maximum_hold", "worker_shutdown", "cleanup", "cancelled"}:
                fail("ADMINISTRATIVE_RELEASE", f"{event.reason} cannot prove a physical press/release cycle", ref)
                continue
            if (event.event_type, event.reason) not in {
                ("keydown", "physical_threshold"), ("keyup", "release_threshold")
            }:
                fail("EVENT_MISMATCH", "event reason does not describe a physical threshold crossing", ref)
            frame = sample_map.get(event.sample_id)
            if (frame is None or abs(frame.stamp.capture_time_s - event.stamp.capture_time_s) > EPSILON
                    or event.stamp.arrival_time_s + EPSILON < frame.stamp.arrival_time_s):
                fail("EVENT_MISMATCH", "event has no simultaneous, causally available raw sample", ref)
            actual[(event.sample_id, event.key, event.event_type, event.reason)] += 1
        for signature, count in actual.items():
            if count > 1:
                fail("DUPLICATE_EVENT", f"repeated event: {signature[1:]}", str(signature[0]))
        for signature in expected - actual:
            fail("MISSING_EVENT", f"unreported physical transition: {signature[1:]}", signature[0])
        for signature in actual - expected:
            fail("EVENT_MISMATCH", f"event without matching physical transition: {signature[1:]}", str(signature[0]))
        if not keydowns:
            fail("MISSING_EVENT", "episode contains no key press")
        for event in keydowns:
            event_text = apply_key(event_text, event.key)

        # This v1 oracle has deliberately narrow append/backspace semantics.
        # Digital drops are recorded as software disturbances; corrected drops
        # can pass, whereas unaccounted edits and missing acknowledgements cannot.
        texts = episode.text_observations
        last_change_time = episode.start_time_s
        if len(texts) != len(keydowns) + 2:
            fail("TEXT_EVIDENCE_MISSING", "require initial/final snapshots and one outcome per KeyDown")
        else:
            initial, final = texts[0], texts[-1]
            if (initial.text != task.initial_text or initial.delivery != "snapshot"
                    or initial.caused_by_event_id is not None
                    or abs(initial.stamp.capture_time_s - episode.start_time_s) > EPSILON
                    or final.delivery != "snapshot" or final.caused_by_event_id is not None
                    or abs(final.stamp.capture_time_s - episode.end_time_s) > EPSILON):
                fail("TEXT_EVIDENCE_MISSING", "invalid initial/final text snapshots")
            previous = task.initial_text
            for observation, event in zip(texts[1:-1], keydowns, strict=True):
                ref = observation.stamp.record_id
                if (observation.caused_by_event_id != event.stamp.record_id
                        or observation.stamp.capture_time_s + EPSILON < event.stamp.arrival_time_s
                        or observation.delivery == "snapshot"):
                    fail("TEXT_EVENT_MISMATCH", "text outcome is not causally linked to its KeyDown", ref)
                expected_text = previous if observation.delivery == "dropped" else apply_key(previous, event.key)
                if observation.text != expected_text:
                    fail("TEXT_EVENT_MISMATCH", "text change violates declared append/backspace or dropped delivery", ref)
                # Even a dropped press needs subsequent fresh verification.
                last_change_time = observation.stamp.capture_time_s
                previous = observation.text
            if final.text != previous:
                fail("TEXT_EVENT_MISMATCH", "final text changed without an associated input event", final.stamp.record_id)
        if not texts or texts[-1].text != task.target_text:
            fail("TEXT_MISMATCH", "final oracle text is not exactly the target")

        requests = {request.stamp.record_id: request for request in episode.verification_requests}
        screens = {}
        for screen in episode.screen_observations:
            if screen.request_id not in requests or screen.request_id in screens:
                fail("INVALID_SCREEN", "screen is unsolicited or duplicates a verification response", screen.stamp.record_id)
            else:
                screens[screen.request_id] = screen
                if screen.stamp.capture_time_s + EPSILON < requests[screen.request_id].stamp.arrival_time_s:
                    fail("SCREEN_STALE", "frame was captured before its verification request", screen.stamp.record_id)
        for ref, request in requests.items():
            screen = screens.get(ref)
            arrival = episode.end_time_s if screen is None else min(screen.stamp.arrival_time_s, episode.end_time_s)
            verification_time += max(0.0, arrival - request.stamp.capture_time_s)
        if (len(requests) > task.max_verifications
                or verification_time > task.verification_time_budget_s + EPSILON):
            fail("BUDGET_EXHAUSTED", "verification count or cumulative request-to-response time exceeded its budget")
        final_requests = [request for request in requests.values() if request.purpose == "final"]
        if len(final_requests) != 1 or final_requests[0] != episode.verification_requests[-1]:
            fail("FINAL_VERIFICATION_MISSING", "exactly one final verification must follow intermediate requests")
        else:
            request = final_requests[0]
            screen = screens.get(request.stamp.record_id)
            if screen is None or screen.stamp.arrival_time_s > episode.end_time_s + EPSILON:
                fail("SCREEN_PENDING", "final feedback had not arrived when finish was requested")
            elif screen.missing or screen.confidence < policy.minimum_screen_confidence:
                fail("SCREEN_UNREADABLE", "final screen is missing or below the frozen confidence threshold", screen.stamp.record_id)
            else:
                if (request.stamp.capture_time_s + EPSILON < last_change_time
                        or screen.stamp.capture_time_s + EPSILON < last_change_time
                        or episode.end_time_s - screen.stamp.capture_time_s > policy.maximum_screen_age_s + EPSILON):
                    fail("SCREEN_STALE", "final feedback predates the last input or exceeds its allowed age", screen.stamp.record_id)
                if screen.text != task.target_text:
                    fail("SCREEN_MISMATCH", "final screen does not show the exact target", screen.stamp.record_id)

        if episode.end_time_s - episode.start_time_s > task.time_budget_s + EPSILON:
            fail("TIME_LIMIT", "completion includes feedback waiting and exceeded the deadline")
        if episode.termination != "finish_requested":
            fail("INCOMPLETE_EPISODE", f"episode ended with {episode.termination}")
        return result()
