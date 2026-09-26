"""Independent raw-sample checks; backend/file provenance is checked separately.

The checks here return consistency findings, never an authenticated sensor or
Runtime state claim. The Isaac bundle verifier supplies frozen asset limits.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

from .contracts import EpisodeRecord, TaskSpec
from .environment import ActionRejected, JointControlSpec, JointTargetGuard
from .evaluation import EvaluationPolicy, Finding, check_key_transitions
from .physical import CoordinateLimit, PhysicsSafetyMonitor, PhysicsTrace
from .serialization import JsonRecord


@dataclass(frozen=True, slots=True)
class PhysicalConsistencyResult(JsonRecord):
    consistent: bool
    findings: tuple[Finding, ...]
    completed_press_release_cycles: int
    text_oracle_matched: bool
    maximum_key_travel_m: float
    maximum_contact_force_n: float
    elapsed_sim_s: float


def check_physical_episode(task: TaskSpec, episode: EpisodeRecord, trace: PhysicsTrace, *,
                           control: JointControlSpec, limits: tuple[CoordinateLimit, ...],
                           key_body_centers_xyz_m: tuple[float, ...],
                           maximum_force_n: float = 12.0, support_tolerance_m: float = 0.03,
                           physics_dt_s: float = 1 / 240) -> PhysicalConsistencyResult:
    findings = []
    completed = 0
    max_travel = max_force = 0.0
    seen = set()

    def fail(code: str, detail: str, ref: str = "episode") -> None:
        if (code, ref) not in seen:
            findings.append(Finding(code, ref, detail))
            seen.add((code, ref))

    def result() -> PhysicalConsistencyResult:
        return PhysicalConsistencyResult(not findings, tuple(findings), completed,
                                         bool(episode.text_observations and episode.text_observations[-1].text == task.target_text),
                                         max_travel, max_force, episode.end_time_s - episode.start_time_s)

    if (trace.control_spec != control or trace.coordinate_limits != limits
            or trace.key_names != task.monitored_keys or task.joint_names != control.joint_names
            or trace.physics_dt_s != physics_dt_s or trace.maximum_force_n != maximum_force_n
            or trace.support_tolerance_m != support_tolerance_m
            or trace.support_origin_xyz_m != (0.0, 0.0, 0.625)):
        fail("FROZEN_CONFIG_MISMATCH", "recorded safety/coordinate settings differ from the independently supplied configuration")
    if (episode.task_sha256 != task.sha256() or trace.episode_sha256 != episode.sha256()
            or episode.manifest.evidence_scope != "isaac_physx_single_hand_v1"
            or episode.manifest.backend != "isaac_sim_5_1"):
        fail("INVALID_EVIDENCE", "wrong task, trace binding or physical backend")
    if (len(episode.observations) != len(trace.samples) or not trace.samples
            or (len(trace.samples) < 2 and not (episode.end_time_s == episode.start_time_s
                                               and episode.termination in {"safety_stop", "cancelled"}))):
        fail("INVALID_EVIDENCE", "raw samples must cover the full nonzero-duration episode")
    if (len(limits) != len({limit.name for limit in limits})
            or not set(control.joint_names) <= {limit.name for limit in limits}):
        fail("JOINT_MAPPING", "coordinate names are duplicated or missing")
    if len(key_body_centers_xyz_m) != len(task.monitored_keys) * 3:
        fail("FROZEN_CONFIG_MISMATCH", "expected world coordinates must cover all monitored keys")
    if len(trace.initial_joint_positions) != len(limits):
        fail("JOINT_MAPPING", "initialization does not cover every coordinate")
    if findings:
        return result()

    indexes = [next(i for i, limit in enumerate(limits) if limit.name == name) for name in control.joint_names]
    monitor = PhysicsSafetyMonitor(limits, maximum_force_n=maximum_force_n,
                                    support_origin_xyz_m=(0.0, 0.0, 0.625), support_tolerance_m=support_tolerance_m)
    ids = set()
    for stream in (episode.observations, episode.events, episode.text_observations):
        previous_time = episode.start_time_s
        for sequence, record in enumerate(stream):
            stamp = record.stamp
            if (stamp.record_id in ids or stamp.sequence != sequence or stamp.capture_time_s < previous_time - 1e-9
                    or (stamp.env_id, stamp.episode_id, stamp.clock_domain) != (episode.env_id, episode.episode_id, "isaac_sim_seconds")
                    or not stamp.source.startswith("isaac.") or stamp.arrival_time_s != stamp.capture_time_s
                    or stamp.capture_time_s > episode.end_time_s + 1e-9):
                fail("INVALID_EVIDENCE", "record identity, ordering, source or clock mismatch", stamp.record_id)
            ids.add(stamp.record_id)
            previous_time = stamp.capture_time_s
    for index, (frame, raw) in enumerate(zip(episode.observations, trace.samples, strict=True)):
        ref = raw.stamp.record_id
        if raw.stamp != frame.stamp or abs(raw.stamp.capture_time_s - index * physics_dt_s) > 1e-9:
            fail("SAMPLE_GAP", "expected one raw sample per physics tick", ref)
        safety_codes = monitor.check(raw)
        for code in safety_codes:
            fail(code, "independent safety check rejected a raw physics sample", ref)
        if "INVALID_PHYSICS_SAMPLE" in safety_codes:
            continue
        if (len(frame.keys) != len(trace.key_names) or tuple(key.key for key in frame.keys) != trace.key_names
                or len(raw.key_joint_positions_m) != len(trace.key_names)
                or tuple(joint.name for joint in frame.joints) != control.joint_names):
            fail("INVALID_EVIDENCE", "incomplete keyboard/joint coverage", ref)
            continue
        if len(raw.joint_positions) != len(limits) or len(raw.key_force_vectors_n) != len(trace.key_names) * 3:
            continue  # Shape failure is already reported by the independent monitor.
        for joint, raw_index in zip(frame.joints, indexes, strict=True):
            if (joint.position_rad != raw.joint_positions[raw_index] or joint.velocity_rad_s != raw.joint_velocities[raw_index]
                    or joint.commanded_target_rad != raw.joint_targets[raw_index]):
                fail("RAW_DERIVATION_MISMATCH", "joint observation does not match its raw tensor sample", ref)
        for key_index, key in enumerate(frame.keys):
            force = math.sqrt(sum(value**2 for value in raw.key_force_vectors_n[key_index*3:key_index*3+3]))
            travel = max(0.0, -raw.key_joint_positions_m[key_index])
            expected_xyz = list(key_body_centers_xyz_m[key_index*3:key_index*3+3])
            expected_xyz[2] += raw.key_joint_positions_m[key_index]
            if math.dist(raw.key_body_positions_xyz_m[key_index*3:key_index*3+3], expected_xyz) > 1e-5:
                fail("KEYBOARD_POSE_MISMATCH", "keycap world position differs from the fixed scene and raw joint travel", ref)
            max_force, max_travel = max(max_force, force), max(max_travel, travel)
            if (abs(key.travel_m - travel) > 1e-9 or key.in_contact != (force > 0.01)
                    or abs(key.normal_force_n - abs(raw.key_force_vectors_n[key_index*3+2])) > 1e-9):
                fail("RAW_DERIVATION_MISMATCH", "key travel/contact is not derived from the raw tensors", ref)
    if abs(trace.samples[-1].stamp.capture_time_s - episode.end_time_s) > 1e-9 or episode.start_time_s != 0.0:
        fail("INVALID_EVIDENCE", "missing episode boundary sample")
    if any(finding.code in {"INVALID_EVIDENCE", "SAMPLE_GAP", "INVALID_PHYSICS_SAMPLE", "JOINT_MAPPING"} for finding in findings):
        return result()

    if any(abs(a-b) > 1e-6 for a, b in zip(trace.samples[0].joint_targets, trace.initial_joint_positions, strict=True)):
        fail("INITIALIZATION_SOURCE", "first applied targets differ from the independently audited direct-joint initialization")

    # Reconstruct every commanded target from the external guard and action log.
    guard = JointTargetGuard(control)
    previous_time = -control.control_dt_s
    accepted = []
    for action_index, record in enumerate(trace.actions):
        ref = f"action:{action_index}"
        if abs(record.time_s - previous_time - control.control_dt_s) > 1e-9:
            fail("ACTION_SEQUENCE", "action log is not contiguous at the control period", ref)
        previous_time = record.time_s
        sample_index = round(record.time_s / physics_dt_s)
        if (sample_index < 0 or sample_index >= len(trace.samples)
                or len(record.previous_targets_rad) != len(indexes) or len(record.applied_targets_rad) != len(indexes)):
            fail("ACTION_SEQUENCE", "action occurs beyond the final physical sample", ref)
            continue
        raw_targets = tuple(trace.samples[sample_index].joint_targets[i] for i in indexes)
        if any(abs(a-b) > 1e-6 for a, b in zip(raw_targets, record.previous_targets_rad, strict=True)):
            fail("ACTION_TARGET_MISMATCH", "action did not start from the last applied target", ref)
        try:
            expected = guard.apply(record.previous_targets_rad, record.action, time_s=record.time_s,
                                   owner_token=trace.owner_token, expected_sequence=action_index)
        except ActionRejected as exc:
            if record.failure_code != exc.code or record.applied_targets_rad != record.previous_targets_rad:
                fail("ACTION_TARGET_MISMATCH", "rejected action was changed or mislabeled", ref)
        else:
            if record.failure_code is not None or expected != record.applied_targets_rad:
                fail("ACTION_TARGET_MISMATCH", "targets bypassed the independent action guard", ref)
            accepted.append(record)
    if not trace.actions:
        fail("ACTION_SEQUENCE", "no low-level action record")
    held = trace.samples[0].joint_targets
    for raw in trace.samples[1:]:
        choices = [action for action in accepted if action.time_s < raw.stamp.capture_time_s - 1e-9]
        if not choices:
            fail("ACTION_SEQUENCE", "physics advanced without a recorded action", raw.stamp.record_id)
            continue
        record = choices[-1]
        if raw.stamp.capture_time_s > record.time_s + control.control_dt_s + 1e-9:
            fail("ACTION_SEQUENCE", "action duration did not cover physical advancement", raw.stamp.record_id)
        expected = list(held)
        for raw_index, target in zip(indexes, record.applied_targets_rad, strict=True):
            expected[raw_index] = target
        if any(abs(a-b) > 1e-6 for a, b in zip(raw.joint_targets, expected, strict=True)):
            fail("ACTION_TARGET_MISMATCH", "unlogged command or modification of a held body joint", raw.stamp.record_id)
    policy = EvaluationPolicy(maximum_sample_gap_s=physics_dt_s * 1.01, maximum_contact_force_n=maximum_force_n)
    completed = check_key_transitions(task, episode, policy, fail)
    keydowns = [event for event in episode.events if event.event_type == "keydown"]
    if len(keydowns) != 1 or keydowns[0].key != "a" or completed != 1:
        fail("PRIMITIVE_MISMATCH", "single-key acceptance requires exactly one complete 'a' press and release")
    texts = episode.text_observations
    if (len(texts) != 3 or texts[0].text != "" or texts[0].delivery != "snapshot"
            or texts[0].stamp.capture_time_s != episode.start_time_s or texts[0].caused_by_event_id is not None
            or texts[-1].text != "a" or texts[-1].delivery != "snapshot"
            or texts[-1].stamp.capture_time_s != episode.end_time_s or texts[-1].caused_by_event_id is not None
            or texts[1].text != "a" or texts[1].delivery != "applied"
            or not keydowns or texts[1].caused_by_event_id != keydowns[0].stamp.record_id
            or texts[1].stamp.capture_time_s != keydowns[0].stamp.capture_time_s):
        fail("TEXT_ORACLE_MISMATCH", "text-state oracle does not match the one physical KeyDown")
    if episode.end_time_s > task.time_budget_s + 1e-9:
        fail("TIME_LIMIT", "episode exceeded the frozen horizon")
    if episode.termination != "finish_requested" or trace.failure_codes:
        fail("INCOMPLETE_EPISODE", "episode stopped, truncated or failed")
    for code in trace.failure_codes:
        fail(code, "recorded runtime failure")
    if episode.verification_requests or episode.screen_observations:
        fail("UNSUPPORTED_SCREEN", "this primitive evaluator makes no desktop/camera claim")
    return result()
