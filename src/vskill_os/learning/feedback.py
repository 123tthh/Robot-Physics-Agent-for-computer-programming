"""Request-gated feedback, bounded waiting and a strict actor DTO allowlist."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Literal, Protocol

from .contracts import JointRecord, PhysicalKeyRecord
from .environment import PolicyObservation
from .serialization import JsonRecord
from .text_task import EpisodeScope


@dataclass(frozen=True, slots=True)
class FeedbackConfig(JsonRecord):
    capture_delay_s: float = .02
    delivery_delay_s: float = .08
    request_timeout_s: float = .30
    maximum_age_s: float = .50
    minimum_confidence: float = .9
    maximum_requests: int = 3
    maximum_pending: int = 2
    verification_time_budget_s: float = .9
    horizon_s: float = 3.

    def __post_init__(self):
        JsonRecord.__post_init__(self)
        if (min(self.capture_delay_s, self.delivery_delay_s) < 0
                or min(self.request_timeout_s, self.maximum_age_s, self.verification_time_budget_s, self.horizon_s) <= 0
                or not 0 <= self.minimum_confidence <= 1 or min(self.maximum_requests, self.maximum_pending) < 1):
            raise ValueError("invalid feedback timing or budget")


@dataclass(frozen=True, slots=True)
class ObservationAccess(JsonRecord):
    joint_state: bool = True
    physical_keys: bool = True
    raw_key_events: Literal[False] = False


@dataclass(frozen=True, slots=True)
class ReadoutFault(JsonRecord):
    request_index: int
    missing: bool = False
    no_response: bool = False
    stale_by_s: float = 0.
    confidence: float = 1.
    text_override: str | None = None
    capture_delay_s: float | None = None
    delivery_delay_s: float | None = None

    def __post_init__(self):
        JsonRecord.__post_init__(self)
        if (self.request_index < 0 or self.stale_by_s < 0 or not 0 <= self.confidence <= 1
                or any(value is not None and value < 0 for value in (self.capture_delay_s, self.delivery_delay_s))):
            raise ValueError("invalid declared readout disturbance")


@dataclass(frozen=True, slots=True)
class VerificationReceipt(JsonRecord):
    request_id: str
    time_s: float
    purpose: Literal["intermediate", "final"]
    accepted: bool
    rejection: str | None


@dataclass(frozen=True, slots=True)
class CapturedReadout(JsonRecord):
    """Supervisory payload, not an image or proof of real screen perception."""
    request_id: str
    produced_time_s: float
    capture_time_s: float
    source: Literal["text_state_readout_v1"]
    text: str | None
    confidence: float | None
    missing: bool

    def __post_init__(self):
        JsonRecord.__post_init__(self)
        if not 0 <= self.capture_time_s <= self.produced_time_s:
            raise ValueError("readout capture cannot be in the future")
        if self.missing:
            if self.text is not None or self.confidence is not None:
                raise ValueError("missing readout is not empty text")
        elif self.text is None or self.confidence is None or not 0 <= self.confidence <= 1:
            raise ValueError("readable payload needs text and confidence")


@dataclass(frozen=True, slots=True)
class FeedbackDelivery(JsonRecord):
    request_id: str
    time_s: float
    status: Literal["returned", "timeout"]


@dataclass(frozen=True, slots=True)
class VisibleFeedback(JsonRecord):
    request_id: str
    purpose: Literal["intermediate", "final"]
    capture_time_s: float | None
    arrival_time_s: float
    age_s: float | None
    status: Literal["readable", "missing", "timeout", "stale", "low_confidence"]
    text: str | None
    confidence: float | None
    source: Literal["text_state_readout_v1"] = "text_state_readout_v1"

    def __post_init__(self):
        JsonRecord.__post_init__(self)
        if self.status == "readable":
            if self.text is None or self.confidence is None:
                raise ValueError("readable feedback requires a text estimate")
        elif self.text is not None or self.confidence is not None:
            raise ValueError("unusable feedback cannot carry a hidden text estimate")


@dataclass(frozen=True, slots=True)
class FeedbackBudget(JsonRecord):
    accepted_requests: int
    pending_requests: int
    remaining_requests: int
    elapsed_wait_s: float
    reserved_wait_s: float
    unreserved_time_s: float


@dataclass(frozen=True, slots=True)
class ActorObservation(JsonRecord):
    env_id: str
    episode_id: str
    time_s: float
    target_key: str
    joints: tuple[JointRecord, ...]
    keys: tuple[PhysicalKeyRecord, ...]
    previous_delta_rad: tuple[float, ...]
    feedback: tuple[VisibleFeedback, ...]
    budget: FeedbackBudget


class ReadoutProvider(Protocol):
    def capture(self, request: VerificationReceipt, time_s: float, request_index: int) -> CapturedReadout: ...


class FeedbackQueue:
    """Reserves one full timeout per accepted request, releasing unused time.

    Count/time include intermediate and final requests, missing responses and
    timeouts. Observing a cached response makes no new capture and has no charge.
    All clocks are episode-local and supplied by the supervisor, not the actor.
    """

    def __init__(self, scope: EpisodeScope, config: FeedbackConfig, faults: tuple[ReadoutFault, ...], provider: ReadoutProvider):
        if len({fault.request_index for fault in faults}) != len(faults):
            raise ValueError("duplicate readout fault index")
        self.scope, self.config, self.provider = scope, config, provider
        self.faults = {fault.request_index: fault for fault in faults}
        self.attempts: list[VerificationReceipt] = []
        self.captures: list[CapturedReadout] = []
        self.deliveries: list[FeedbackDelivery] = []
        self._jobs: dict[str, tuple[float, float, float, bool]] = {}
        self.time_s, self.closed = 0., False

    def request(self, purpose: Literal["intermediate", "final"]) -> VerificationReceipt:
        if self.closed:
            raise RuntimeError("feedback session is closed")
        if purpose not in {"intermediate", "final"}:
            raise ValueError("unknown verification purpose")
        budget = self.budget()
        accepted = [item for item in self.attempts if item.accepted]
        reason = None
        if any(item.purpose == "final" for item in accepted):
            reason = "FINAL_ALREADY_REQUESTED"
        elif self.time_s + self.config.request_timeout_s > self.config.horizon_s + 1e-9:
            reason = "INSUFFICIENT_HORIZON"
        elif budget.pending_requests >= self.config.maximum_pending:
            reason = "PENDING_LIMIT"
        elif budget.remaining_requests == 0 or budget.unreserved_time_s + 1e-9 < self.config.request_timeout_s:
            reason = "BUDGET_EXHAUSTED"
        receipt = VerificationReceipt(f"{self.scope.env_id}:{self.scope.episode_id}:verify:{len(self.attempts)}",
                                      self.time_s, purpose, reason is None, reason)
        self.attempts.append(receipt)
        if receipt.accepted:
            fault = self.faults.get(len(accepted), ReadoutFault(len(accepted)))
            capture = self.time_s + (self.config.capture_delay_s if fault.capture_delay_s is None else fault.capture_delay_s)
            arrival = capture + (self.config.delivery_delay_s if fault.delivery_delay_s is None else fault.delivery_delay_s)
            self._jobs[receipt.request_id] = (capture, arrival, self.time_s+self.config.request_timeout_s, fault.no_response)
            self.advance(self.time_s)
        return receipt

    def advance(self, time_s: float) -> None:
        if self.closed or not math.isfinite(time_s) or not self.time_s-1e-9 <= time_s <= self.config.horizon_s+1e-9:
            raise ValueError("feedback clock is closed, reversed or outside its horizon")
        requests = {item.request_id: item for item in self.attempts if item.accepted}
        captured = {item.request_id for item in self.captures}
        delivered = {item.request_id for item in self.deliveries}
        scheduled = []
        for request_id, (capture, arrival, deadline, suppress) in self._jobs.items():
            if request_id in delivered:
                continue
            if capture <= min(time_s, deadline)+1e-9 and request_id not in captured:
                scheduled.append((capture, 0, request_id))
            due = deadline if suppress or arrival > deadline+1e-9 else arrival
            if due <= time_s+1e-9:
                scheduled.append((due, 1, request_id))
        for due, kind, request_id in sorted(scheduled):
            if kind == 0:
                readout = self.provider.capture(requests[request_id], due, list(requests).index(request_id))
                if readout.request_id != request_id or abs(readout.produced_time_s-due) > 1e-9:
                    raise ValueError("unsolicited readout or changed production time")
                self.captures.append(readout)
            else:
                _, arrival, deadline, suppress = self._jobs[request_id]
                status = "timeout" if suppress or arrival > deadline+1e-9 else "returned"
                self.deliveries.append(FeedbackDelivery(request_id, due, status))
        self.time_s = time_s

    def budget(self) -> FeedbackBudget:
        accepted = [item for item in self.attempts if item.accepted]
        completed = {item.request_id: item for item in self.deliveries}
        elapsed = sum((completed[r.request_id].time_s if r.request_id in completed else self.time_s)-r.time_s for r in accepted)
        remaining_reservations = sum(max(0., self.config.request_timeout_s-(self.time_s-r.time_s)) for r in accepted if r.request_id not in completed)
        return FeedbackBudget(len(accepted), len(accepted)-len(completed), self.config.maximum_requests-len(accepted),
                              elapsed, 0. if self.closed else remaining_reservations,
                              max(0., self.config.verification_time_budget_s-elapsed-(0. if self.closed else remaining_reservations)))

    def close(self) -> tuple[str, ...]:
        self.closed = True
        completed = {item.request_id for item in self.deliveries}
        return tuple(item.request_id for item in self.attempts if item.accepted and item.request_id not in completed)


class ObservationFilter:
    def __init__(self, access: ObservationAccess):
        self.access = access

    def publish(self, base: PolicyObservation, queue: FeedbackQueue) -> ActorObservation:
        if (base.env_id, base.episode_id, base.time_s) != (queue.scope.env_id, queue.scope.episode_id, queue.time_s):
            raise ValueError("observation/feedback scope or time mismatch")
        captures = {item.request_id: item for item in queue.captures}
        deliveries = {item.request_id: item for item in queue.deliveries}
        visible = []
        for request in queue.attempts:
            delivery = deliveries.get(request.request_id)
            if delivery is None:
                continue
            sample = captures.get(request.request_id)
            if delivery.time_s > base.time_s+1e-9:
                raise ValueError("feedback was published before arrival")
            status = "readable"
            capture = age = None
            if delivery.status == "timeout":
                status = "timeout"
            else:
                if sample is None:
                    raise ValueError("returned feedback has no capture")
                capture, age = sample.capture_time_s, max(0., base.time_s-sample.capture_time_s)
                if capture < request.time_s-1e-9 or age > queue.config.maximum_age_s+1e-9:
                    status = "stale"
                elif sample.missing:
                    status = "missing"
                elif sample.confidence < queue.config.minimum_confidence:
                    status = "low_confidence"
            visible.append(VisibleFeedback(request.request_id, request.purpose, capture, delivery.time_s, age, status,
                                            sample.text if status == "readable" else None,
                                            sample.confidence if status == "readable" else None))
        return ActorObservation(base.env_id, base.episode_id, base.time_s, base.target_key,
                                base.joints if self.access.joint_state else (), base.keys if self.access.physical_keys else (),
                                base.previous_delta_rad, tuple(visible), queue.budget())
