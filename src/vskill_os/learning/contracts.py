"""VF-01-T01 v1 supervisory records, distinct from future actor observations.

All times are seconds in one declared, episode-local monotonic clock domain.
Cross-clock alignment and real sensor provenance require a later adapter. These
records intentionally contain privileged text; never hand an EpisodeRecord or
an ObservationFrame directly to a learned policy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .serialization import JsonRecord

SCHEMA_VERSION = "vf-evidence/1"


def _nonempty(*values: str) -> None:
    if any(not value.strip() for value in values):
        raise ValueError("identifiers must be nonempty")


def _unique(values: tuple[str, ...], name: str) -> None:
    if not values or len(set(values)) != len(values):
        raise ValueError(f"{name} must be nonempty and unique")
    _nonempty(*values)


def _sha256(value: str) -> None:
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError("expected a lowercase SHA-256 digest")


@dataclass(frozen=True, slots=True)
class KeyBinding(JsonRecord):
    key: str
    character: str

    def __post_init__(self) -> None:
        JsonRecord.__post_init__(self)
        _nonempty(self.key)
        if len(self.character) != 1 or not self.character.isprintable():
            raise ValueError("a character binding must be one printable character")


@dataclass(frozen=True, slots=True)
class TaskSpec(JsonRecord):
    schema_version: Literal["vf-evidence/1"]
    task_id: str
    target_text: str
    initial_text: str
    character_keys: tuple[KeyBinding, ...]
    backspace_key: str
    monitored_keys: tuple[str, ...]
    joint_names: tuple[str, ...]
    hand: Literal["left", "right"]
    support_mode: str
    sensor_manifest: tuple[str, ...]
    time_budget_s: float
    max_verifications: int
    verification_time_budget_s: float

    def __post_init__(self) -> None:
        JsonRecord.__post_init__(self)
        _nonempty(self.task_id, self.backspace_key, self.support_mode)
        if not self.target_text:
            raise ValueError("target_text cannot be empty")
        _unique(self.monitored_keys, "monitored_keys")
        _unique(self.joint_names, "joint_names")
        _unique(self.sensor_manifest, "sensor_manifest")
        _unique(tuple(binding.key for binding in self.character_keys), "character keys")
        key_names = {binding.key for binding in self.character_keys}
        characters = {binding.character for binding in self.character_keys}
        if self.backspace_key in key_names or not (key_names | {self.backspace_key}) <= set(self.monitored_keys):
            raise ValueError("all input keys must be monitored, with a separate Backspace key")
        if not set(self.target_text) <= characters:
            raise ValueError("target_text contains characters outside the declared key bindings")
        if self.time_budget_s <= 0 or self.verification_time_budget_s <= 0 or self.max_verifications < 1:
            raise ValueError("time and final-verification budgets must be positive")


@dataclass(frozen=True, slots=True)
class RunManifest(JsonRecord):
    run_id: str
    backend: str
    evidence_scope: str
    code_revision: str
    generator_sha256: str
    seed: int
    initialization_provenance: str

    def __post_init__(self) -> None:
        JsonRecord.__post_init__(self)
        _nonempty(self.run_id, self.backend, self.evidence_scope, self.code_revision,
                  self.generator_sha256, self.initialization_provenance)
        _sha256(self.generator_sha256)


@dataclass(frozen=True, slots=True)
class RecordStamp(JsonRecord):
    record_id: str
    env_id: str
    episode_id: str
    sequence: int
    clock_domain: str
    capture_time_s: float
    arrival_time_s: float
    source: str

    def __post_init__(self) -> None:
        JsonRecord.__post_init__(self)
        _nonempty(self.record_id, self.env_id, self.episode_id, self.clock_domain, self.source)
        if self.sequence < 0 or self.capture_time_s < 0 or self.arrival_time_s < self.capture_time_s:
            raise ValueError("record sequence/time must be nonnegative; arrival cannot precede capture")


@dataclass(frozen=True, slots=True)
class PhysicalKeyRecord(JsonRecord):
    key: str
    travel_m: float
    in_contact: bool
    normal_force_n: float

    def __post_init__(self) -> None:
        JsonRecord.__post_init__(self)
        _nonempty(self.key)
        if self.travel_m < 0 or self.normal_force_n < 0:
            raise ValueError("key travel and normal force cannot be negative")


@dataclass(frozen=True, slots=True)
class JointRecord(JsonRecord):
    name: str
    position_rad: float
    velocity_rad_s: float
    commanded_target_rad: float


@dataclass(frozen=True, slots=True)
class ObservationFrame(JsonRecord):
    stamp: RecordStamp
    keys: tuple[PhysicalKeyRecord, ...]
    joints: tuple[JointRecord, ...]
    safety_stopped: bool


@dataclass(frozen=True, slots=True)
class KeyEventRecord(JsonRecord):
    stamp: RecordStamp
    key: str
    event_type: Literal["keydown", "keyup"]
    reason: str
    sample_id: str | None


@dataclass(frozen=True, slots=True)
class TextObservation(JsonRecord):
    """Privileged append/backspace oracle; a dropped delivery must be explicit."""

    stamp: RecordStamp
    text: str
    caused_by_event_id: str | None
    delivery: Literal["snapshot", "applied", "dropped"]


@dataclass(frozen=True, slots=True)
class VerificationRequest(JsonRecord):
    stamp: RecordStamp
    purpose: Literal["intermediate", "final"]


@dataclass(frozen=True, slots=True)
class ScreenObservation(JsonRecord):
    stamp: RecordStamp
    request_id: str
    frame_sha256: str | None
    text: str | None
    confidence: float | None
    missing: bool

    def __post_init__(self) -> None:
        JsonRecord.__post_init__(self)
        if self.missing:
            if any(value is not None for value in (self.frame_sha256, self.text, self.confidence)):
                raise ValueError("a missing frame must not masquerade as an empty or readable screen")
        elif (self.text is None or self.confidence is None or not 0 <= self.confidence <= 1
              or self.frame_sha256 is None):
            raise ValueError("a readable frame needs text, confidence in [0,1] and a SHA-256 reference")
        else:
            _sha256(self.frame_sha256)


@dataclass(frozen=True, slots=True)
class EpisodeRecord(JsonRecord):
    schema_version: Literal["vf-evidence/1"]
    env_id: str
    episode_id: str
    clock_domain: str
    task_sha256: str
    manifest: RunManifest
    start_time_s: float
    end_time_s: float
    termination: Literal["finish_requested", "time_limit", "safety_stop", "cancelled"]
    observations: tuple[ObservationFrame, ...]
    events: tuple[KeyEventRecord, ...]
    text_observations: tuple[TextObservation, ...]
    verification_requests: tuple[VerificationRequest, ...]
    screen_observations: tuple[ScreenObservation, ...]

    def __post_init__(self) -> None:
        JsonRecord.__post_init__(self)
        _nonempty(self.env_id, self.episode_id, self.clock_domain, self.task_sha256)
        _sha256(self.task_sha256)
        if self.start_time_s < 0 or self.end_time_s <= self.start_time_s:
            raise ValueError("episode end must be later than its nonnegative start")
