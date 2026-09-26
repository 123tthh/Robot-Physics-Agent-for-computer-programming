"""Isaac composition and immutable sidecar recording for VF-01-T03."""

from dataclasses import replace

from ....learning.contracts import EpisodeRecord
from ...feedback_environment import FeedbackEnvironment
from .single_hand_env import ROOT, file_sha256, write_json

FEEDBACK_SOURCES = (
    "src/vskill_os/learning/text_task.py",
    "src/vskill_os/learning/feedback.py",
    "src/vskill_os/learning/feedback_session.py",
    "src/vskill_os/learning/feedback_demo.py",
    "src/vskill_os/adapters/text_feedback.py",
    "src/vskill_os/adapters/feedback_environment.py",
    "src/vskill_os/adapters/isaac/learning/feedback_env.py",
    "src/vskill_os/adapters/isaac/learning/feedback_smoke.py",
)
FEEDBACK_FILES = ("feedback_spec.json", "feedback_episode.json")


def isaac_feedback_environment(backend) -> FeedbackEnvironment:
    sources = {name: file_sha256(ROOT / name) for name in FEEDBACK_SOURCES}

    def persist(episode, spec):
        if sources != {name: file_sha256(ROOT / name) for name in FEEDBACK_SOURCES}:
            raise ValueError("FEEDBACK_SOURCE_CHANGED: collector changed while running")
        directory = backend.episode_dir
        physical = EpisodeRecord.read(directory / "episode.json")
        bound = replace(episode, physical_episode_sha256=physical.sha256())
        bound.write(directory / "feedback_episode.json")
        spec.write(directory / "feedback_spec.json")
        write_json(directory / "feedback_manifest.json", {
            "schema_version": "vf-feedback-bundle/1", "sources": sources,
            "files": {name: file_sha256(directory / name) for name in FEEDBACK_FILES},
            "physics_manifest_sha256": file_sha256(directory / "manifest.json"),
        })

    return FeedbackEnvironment(backend, clock_domain="isaac_sim_seconds", source="isaac_physx_feedback", on_seal=persist)
