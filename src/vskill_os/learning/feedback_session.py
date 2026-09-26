"""Episode orchestration and supervisory records, separate from actor DTOs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal

from .contracts import KeyEventRecord
from .environment import PolicyObservation
from .feedback import (
    ActorObservation, CapturedReadout, FeedbackConfig, FeedbackDelivery, FeedbackQueue,
    ObservationAccess, ObservationFilter, ReadoutFault, ReadoutProvider, VerificationReceipt,
)
from .serialization import JsonRecord
from .text_task import DigitalInputPlan, EpisodeScope, TextSnapshot, TextTaskConfig, TextTaskState, TextTransition


@dataclass(frozen=True, slots=True)
class ObservationPublication(JsonRecord):
    base: PolicyObservation
    visible: ActorObservation
    attempt_count: int


@dataclass(frozen=True, slots=True)
class FeedbackSpec(JsonRecord):
    """Frozen by the experiment runner, independently of the recorded episode."""
    task: TextTaskConfig = TextTaskConfig()
    config: FeedbackConfig = FeedbackConfig()
    access: ObservationAccess = ObservationAccess()
    input_plan: DigitalInputPlan = DigitalInputPlan()
    readout_faults: tuple[ReadoutFault, ...] = ()

    def __post_init__(self):
        JsonRecord.__post_init__(self)
        if len({fault.request_index for fault in self.readout_faults}) != len(self.readout_faults):
            raise ValueError("duplicate readout fault index")


@dataclass(frozen=True, slots=True)
class FeedbackEpisode(JsonRecord):
    schema_version: Literal["vf-feedback/1"]
    source: Literal["contract_feedback", "isaac_physx_feedback"]
    scope: EpisodeScope
    task: TextTaskConfig
    config: FeedbackConfig
    access: ObservationAccess
    input_plan: DigitalInputPlan
    readout_faults: tuple[ReadoutFault, ...]
    end_time_s: float
    termination: Literal["finish_requested", "time_limit", "safety_stop", "cancelled"]
    events: tuple[KeyEventRecord, ...]
    text_transitions: tuple[TextTransition, ...]
    final_text_state: TextSnapshot
    attempts: tuple[VerificationReceipt, ...]
    captures: tuple[CapturedReadout, ...]
    deliveries: tuple[FeedbackDelivery, ...]
    publications: tuple[ObservationPublication, ...]
    cancelled_request_ids: tuple[str, ...]
    physical_episode_sha256: str | None


class FeedbackSession:
    """Supervisor-only object. A policy receives a port and immutable DTOs.

    Digital events at a timestamp are consumed before a capture at that same
    timestamp. Waiting uses the caller's physical/logical clock, never wall time.
    """

    def __init__(self, *, scope: EpisodeScope, task: TextTaskConfig, config: FeedbackConfig,
                 access: ObservationAccess, input_plan: DigitalInputPlan, readout_faults: tuple[ReadoutFault, ...],
                 provider_factory: Callable[[TextTaskState], ReadoutProvider],
                 source: Literal["contract_feedback", "isaac_physx_feedback"]):
        self.scope, self.task, self.config, self.access = scope, task, config, access
        self.input_plan, self.readout_faults, self.source = input_plan, readout_faults, source
        self.text_state = TextTaskState(scope, task, input_plan)
        self.text_state.advance(0.)
        self.queue = FeedbackQueue(scope, config, readout_faults, provider_factory(self.text_state))
        self.filter = ObservationFilter(access)
        self.publications: list[ObservationPublication] = []
        self.closed = False
        self._sealed: FeedbackEpisode | None = None

    def advance(self, time_s: float, events: tuple[KeyEventRecord, ...] = ()) -> None:
        if self.closed:
            raise RuntimeError("feedback session is closed")
        if time_s < self.queue.time_s-1e-9 or time_s > self.config.horizon_s+1e-9:
            raise ValueError("feedback session clock is outside the episode")
        self.text_state.advance(time_s, events)
        self.queue.advance(time_s)

    def request(self, purpose: Literal["intermediate", "final"] = "intermediate") -> VerificationReceipt:
        return self.queue.request(purpose)

    def observe(self, base: PolicyObservation) -> ActorObservation:
        if self.closed:
            raise RuntimeError("feedback session is closed")
        observation = self.filter.publish(base, self.queue)
        self.publications.append(ObservationPublication(base, observation, len(self.queue.attempts)))
        return observation

    def close(self, termination: str = "finish_requested", *, physical_episode_sha256: str | None = None) -> FeedbackEpisode:
        if self._sealed is not None:
            return self._sealed
        cancelled = self.queue.close()
        final = self.text_state.snapshot_at(self.queue.time_s)
        self._sealed = FeedbackEpisode(
            "vf-feedback/1", self.source, self.scope, self.task, self.config, self.access, self.input_plan, self.readout_faults,
            self.queue.time_s, termination, tuple(self.text_state.events), tuple(self.text_state.transitions),
            TextSnapshot(self.queue.time_s, final.text, final.cursor, final.focused),
            tuple(self.queue.attempts), tuple(self.queue.captures), tuple(self.queue.deliveries),
            tuple(self.publications), cancelled, physical_episode_sha256,
        )
        self.closed = True
        return self._sealed
