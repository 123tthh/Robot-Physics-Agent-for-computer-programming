"""Compose physical reset/step with a separate, request-gated text supervisor.

The policy port is an API/data boundary for trusted policy code, not a sandbox
against arbitrary Python reflection. Backend state, fault plans and output paths
belong only to the experiment supervisor. Agent/L2 skill APIs are unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

from ..learning.contracts import KeyEventRecord
from ..learning.environment import EpisodeMetadata, JointTargetAction, PolicyObservation, ResetSpec, StepResult
from ..learning.feedback import ActorObservation, VerificationReceipt
from ..learning.feedback_session import FeedbackEpisode, FeedbackSpec
from ..learning.serialization import JsonRecord
from ..learning.text_task import EpisodeScope
from .text_feedback import session_from_spec


class PhysicalFeedbackBackend(Protocol):
    events: list[KeyEventRecord]
    termination: str

    def reset(self, spec: ResetSpec) -> tuple[PolicyObservation, EpisodeMetadata]: ...
    def step(self, action: JointTargetAction) -> StepResult: ...
    def finish(self) -> None: ...
    def close(self): ...


@dataclass(frozen=True, slots=True)
class ActorStep(JsonRecord):
    observation: ActorObservation
    terminated: bool
    truncated: bool
    failure_codes: tuple[str, ...]


class FeedbackPolicyPort:
    """Low-level policy adapter only; has no clock setter, reset or text oracle."""
    __slots__ = ("__owner", "__generation")

    def __init__(self, owner, generation: int):
        self.__owner, self.__generation = owner, generation

    def observe(self) -> ActorObservation:
        return self.__owner.observe(self.__generation)

    def request_verification(self, purpose="intermediate") -> VerificationReceipt:
        return self.__owner.request(self.__generation, purpose)

    def step(self, action: JointTargetAction) -> ActorStep:
        return self.__owner.step(self.__generation, action)

    def finish(self) -> None:
        self.__owner.finish(self.__generation)


class FeedbackEnvironment:
    """Supervisor owns this object; pass only the returned port to the policy."""

    def __init__(self, backend: PhysicalFeedbackBackend, *, clock_domain: str, source: str,
                 on_seal: Callable[[FeedbackEpisode, FeedbackSpec], None] | None = None):
        self.backend, self.clock_domain, self.source, self.on_seal = backend, clock_domain, source, on_seal
        self.session, self.episode, self.spec = None, None, None
        self._generation, self._event_count, self._base = 0, 0, None
        self.closed = False

    def reset(self, reset: ResetSpec, spec: FeedbackSpec) -> tuple[FeedbackPolicyPort, ActorObservation, EpisodeMetadata]:
        if self.closed or self.session is not None and not self.session.closed:
            raise RuntimeError("finish or terminate before reset; closed environments cannot reset")
        if abs(reset.maximum_time_s-spec.config.horizon_s) > 1e-9:
            raise ValueError("physical and feedback horizons must agree")
        base, metadata = self.backend.reset(reset)
        if self.backend.events or base.time_s != 0.:
            raise ValueError("reset must start with a released, event-free keyboard at time zero")
        self._generation += 1
        self._event_count, self._base = 0, base
        self.spec, self.episode = spec, None
        self.session = session_from_spec(spec, scope=EpisodeScope(base.env_id, base.episode_id, self.clock_domain), source=self.source)
        port = FeedbackPolicyPort(self, self._generation)
        return port, port.observe(), metadata

    def _active(self, generation: int) -> None:
        if self.closed or generation != self._generation or self.session is None or self.session.closed:
            raise RuntimeError("stale or closed feedback policy port")

    def observe(self, generation: int) -> ActorObservation:
        self._active(generation)
        return self.session.observe(self._base)

    def request(self, generation: int, purpose: str) -> VerificationReceipt:
        self._active(generation)
        return self.session.request(purpose)

    def step(self, generation: int, action: JointTargetAction) -> ActorStep:
        self._active(generation)
        # Waiting is implemented by physical hold actions. Independent safety
        # and all physics substeps run even while a readout is pending.
        step = self.backend.step(action)
        events = tuple(self.backend.events[self._event_count:])
        self.session.advance(step.observation.time_s, events)
        self._event_count = len(self.backend.events)
        self._base = step.observation
        visible = self.observe(generation)
        if step.terminated or step.truncated:
            self._seal()
        return ActorStep(visible, step.terminated, step.truncated, step.failure_codes)

    def finish(self, generation: int) -> None:
        self._active(generation)
        self.backend.finish()
        self.observe(generation)
        self._seal()

    def _seal(self) -> None:
        if self.session is not None and not self.session.closed:
            self.episode = self.session.close(self.backend.termination)
            if self.on_seal is not None:
                self.on_seal(self.episode, self.spec)

    def close(self):
        cleanup = self.backend.close()
        self._seal()
        self.closed = True
        return cleanup
