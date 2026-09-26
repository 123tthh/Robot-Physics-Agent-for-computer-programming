"""Bind the text/feedback replay to independently checked raw physical evidence."""

import hashlib
import json
from pathlib import Path

from ....learning.contracts import EpisodeRecord
from ....learning.environment import PolicyObservation, ResetSpec
from ....learning.feedback_evaluation import evaluate_feedback
from ....learning.feedback_session import FeedbackEpisode, FeedbackSpec
from ....learning.physical import PhysicsTrace
from ....learning.serialization import _unique_object
from ....learning.text_task import EpisodeScope
from .config import SingleHandConfig
from .feedback_env import FEEDBACK_FILES, FEEDBACK_SOURCES
from .single_hand_env import ROOT, file_sha256
from .verify import verify_bundle


def check_physical_binding(feedback: FeedbackEpisode, physical: EpisodeRecord, trace: PhysicsTrace) -> list[str]:
    errors = []
    if (feedback.source != "isaac_physx_feedback" or feedback.physical_episode_sha256 != physical.sha256()
            or feedback.scope != EpisodeScope(physical.env_id, physical.episode_id, physical.clock_domain)
            or feedback.events != physical.events or feedback.termination != physical.termination
            or abs(feedback.end_time_s-physical.end_time_s) > 1e-9):
        errors.append("PHYSICAL_EPISODE_BINDING")
    frames = {round(frame.stamp.capture_time_s, 9): frame for frame in physical.observations}
    for item in feedback.publications:
        time_s = item.base.time_s
        frame = frames.get(round(time_s, 9))
        actions = [action for action in trace.actions if action.failure_code is None and action.time_s < time_s-1e-9]
        previous = tuple(a-b for a, b in zip(actions[-1].applied_targets_rad, actions[-1].previous_targets_rad, strict=True)) if actions else (0.,)*len(trace.control_spec.joint_names)
        if frame is None or item.base != PolicyObservation(physical.env_id, physical.episode_id, time_s, "a", frame.joints, frame.keys, previous):
            errors.append("PHYSICAL_OBSERVATION_BINDING")
            break
    return errors


def verify_feedback_bundle(directory: Path, *, expected: FeedbackSpec, physics_config: SingleHandConfig) -> dict:
    report = dict(schema_version="vf-feedback-evaluation/1", recording=str(directory.resolve()), binding_passed=False,
                  isolation_passed=False, text_task_passed=False, physical_primitive_passed=False,
                  end_to_end_passed=False, camera_verified=False, desktop_verified=False, learned_policy=False,
                  readout_source="text_state_readout_v1", findings=[])
    try:
        manifest = json.loads((directory / "feedback_manifest.json").read_text(), object_pairs_hook=_unique_object)
        if manifest["schema_version"] != "vf-feedback-bundle/1" or set(manifest["files"]) != set(FEEDBACK_FILES):
            raise ValueError("invalid feedback manifest schema or file set")
        if (manifest["sources"] != {name: file_sha256(ROOT / name) for name in FEEDBACK_SOURCES}
                or manifest["files"] != {name: file_sha256(directory / name) for name in FEEDBACK_FILES}
                or manifest["physics_manifest_sha256"] != file_sha256(directory / "manifest.json")):
            raise ValueError("FEEDBACK_HASH: source, sidecar or physical bundle differs")
        if FeedbackSpec.read(directory / "feedback_spec.json") != expected:
            raise ValueError("FROZEN_FEEDBACK_SPEC_MISMATCH")
        physical = EpisodeRecord.read(directory / "episode.json")
        trace = PhysicsTrace.read(directory / "trace.json")
        feedback = FeedbackEpisode.read(directory / "feedback_episode.json")
        reset = ResetSpec.read(directory / "reset.json")
        if abs(reset.maximum_time_s-expected.config.horizon_s) > 1e-9:
            raise ValueError("physical and feedback horizons differ")
        errors = check_physical_binding(feedback, physical, trace)
        evaluation = evaluate_feedback(feedback, expected=expected,
                                       scope=EpisodeScope(physical.env_id, physical.episode_id, physical.clock_domain))
        physics = verify_bundle(directory, expected_config=physics_config)
        errors += evaluation["findings"]
        report.update(binding_passed=not errors, isolation_passed=not errors and evaluation["isolation_passed"],
                      text_task_passed=not errors and evaluation["text_task_passed"],
                      physical_primitive_passed=physics["physical_primitive_passed"],
                      end_to_end_passed=not errors and evaluation["text_task_passed"] and physics["physical_primitive_passed"],
                      findings=errors, feedback=evaluation, physics=physics,
                      feedback_episode_sha256=feedback.sha256(), feedback_spec_sha256=expected.sha256(),
                      feedback_manifest_sha256=file_sha256(directory / "feedback_manifest.json"),
                      evaluator_sha256=hashlib.sha256(Path(__file__).read_bytes()
                          + (ROOT / "src/vskill_os/learning/feedback_evaluation.py").read_bytes()).hexdigest())
    except (OSError, ValueError, KeyError, TypeError, IndexError, StopIteration) as exc:
        report["findings"].append("INVALID_FEEDBACK_BUNDLE: " + str(exc))
    return report
