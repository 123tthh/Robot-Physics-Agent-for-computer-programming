"""Request-gated camera evidence, separate from the T03 simulated text source.

Only standard-library records live here. Images, OCR, terminal state and physical
stepping are owned by optional adapters. Wall-clock OCR cost is recorded there;
this queue uses explicitly declared simulation time, never real-time claims.
"""
from dataclasses import dataclass
from typing import Literal

from .environment import PolicyObservation
from .feedback import FeedbackBudget, FeedbackConfig, FeedbackDelivery, VerificationReceipt
from .serialization import JsonRecord


@dataclass(frozen=True, slots=True)
class DesktopCameraSpec(JsonRecord):
    feedback: FeedbackConfig = FeedbackConfig(maximum_pending=1)
    # Frozen on a separate empty-screen/one-key calibration probe. This ROI is
    # specific to this fixed workstation and terminal layout, not a detector.
    input_roi: tuple[int, ...] = (436, 350, 830, 370)
    image_size: tuple[int, ...] = (1280, 720)
    ocr_scale: int = 4
    render_frames: int = 8
    ocr_timeout_s: float = 10.
    minimum_depth_fraction: float = .95
    mount_position: tuple[float, ...] = (.08, 0., .49)
    mount_quaternion: tuple[float, ...] = (.99875026, 0., .04997917, 0.)

    def __post_init__(self):
        JsonRecord.__post_init__(self)
        if (len(self.input_roi) != 4 or len(self.image_size) != 2
                or not 0 <= self.input_roi[0] < self.input_roi[2] <= self.image_size[0]
                or not 0 <= self.input_roi[1] < self.input_roi[3] <= self.image_size[1]
                or not 1 <= self.ocr_scale <= 8 or not 1 <= self.render_frames <= 32
                or self.ocr_timeout_s <= 0 or not 0 < self.minimum_depth_fraction <= 1
                or len(self.mount_position) != 3 or len(self.mount_quaternion) != 4):
            raise ValueError('invalid camera calibration or resource bounds')


@dataclass(frozen=True, slots=True)
class CameraReadout(JsonRecord):
    request_id: str
    produced_time_s: float
    capture_time_s: float
    frame_id: str | None
    text: str | None
    confidence: float | None
    missing: bool
    source: Literal['robot_camera_ocr_v1'] = 'robot_camera_ocr_v1'

    def __post_init__(self):
        JsonRecord.__post_init__(self)
        if not 0 <= self.capture_time_s <= self.produced_time_s:
            raise ValueError('camera capture cannot be in the future')
        if self.missing:
            if self.text is not None or self.confidence is not None:
                raise ValueError('missing camera feedback cannot reveal text')
        elif (self.frame_id is None or self.text is None or self.confidence is None
              or not 0 <= self.confidence <= 1):
            raise ValueError('camera readout requires an identified frame and OCR confidence')


@dataclass(frozen=True, slots=True)
class CameraFeedback(JsonRecord):
    request_id: str
    purpose: Literal['intermediate', 'final']
    capture_time_s: float | None
    arrival_time_s: float
    age_s: float | None
    status: Literal['readable', 'missing', 'timeout', 'stale', 'low_confidence']
    text: str | None
    confidence: float | None
    source: Literal['robot_camera_ocr_v1'] = 'robot_camera_ocr_v1'

    def __post_init__(self):
        JsonRecord.__post_init__(self)
        if self.status == 'readable':
            if self.text is None or self.confidence is None:
                raise ValueError('readable camera feedback requires OCR text')
        elif self.text is not None or self.confidence is not None:
            raise ValueError('unusable camera feedback cannot reveal OCR text')


@dataclass(frozen=True, slots=True)
class CameraActorObservation(JsonRecord):
    physical: PolicyObservation
    feedback: tuple[CameraFeedback, ...]
    budget: FeedbackBudget


class CameraFeedbackQueue:
    """Actual capture/arrival ticks, with count, pending and wait budgets.

    The caller advances physics first, then this queue. Capture cannot be
    backdated to a requested timestamp. Invalid or missing OCR is never exposed
    as a successful empty-text observation. No hidden terminal truth enters it.
    """
    def __init__(self, env_id, episode_id, config: FeedbackConfig):
        self.env_id, self.episode_id, self.config = env_id, episode_id, config
        self.time_s, self.closed = 0., False
        self.attempts, self.captures, self.deliveries, self.publications = [], [], [], []

    def budget(self):
        accepted = [r for r in self.attempts if r.accepted]
        done = {d.request_id: d for d in self.deliveries}
        elapsed = sum((done[r.request_id].time_s if r.request_id in done else self.time_s)-r.time_s for r in accepted)
        reserved = 0. if self.closed else sum(max(0., self.config.request_timeout_s-self.time_s+r.time_s)
                                            for r in accepted if r.request_id not in done)
        return FeedbackBudget(len(accepted), len(accepted)-len(done), self.config.maximum_requests-len(accepted),
                              elapsed, reserved, max(0., self.config.verification_time_budget_s-elapsed-reserved))

    def request(self, purpose='intermediate'):
        if self.closed or purpose not in ('intermediate', 'final'):
            raise RuntimeError('closed camera queue or invalid purpose')
        budget = self.budget()
        reason = None
        if any(r.accepted and r.purpose == 'final' for r in self.attempts):
            reason = 'FINAL_ALREADY_REQUESTED'
        elif self.time_s+self.config.request_timeout_s > self.config.horizon_s+1e-9:
            reason = 'INSUFFICIENT_HORIZON'
        elif budget.pending_requests >= self.config.maximum_pending:
            reason = 'PENDING_LIMIT'
        elif not budget.remaining_requests or budget.unreserved_time_s+1e-9 < self.config.request_timeout_s:
            reason = 'BUDGET_EXHAUSTED'
        receipt = VerificationReceipt(f'{self.env_id}:{self.episode_id}:camera:{len(self.attempts)}',
                                      self.time_s, purpose, reason is None, reason)
        self.attempts.append(receipt)
        return receipt

    def advance(self, now: float, capture):
        if self.closed or not self.time_s-1e-9 <= now <= self.config.horizon_s+1e-9:
            raise ValueError('camera clock is closed, reversed or outside horizon')
        for request in self.attempts:
            if not request.accepted or any(d.request_id == request.request_id for d in self.deliveries):
                continue
            deadline = request.time_s+self.config.request_timeout_s
            sample = next((s for s in self.captures if s.request_id == request.request_id), None)
            if sample is None and request.time_s+self.config.capture_delay_s <= now+1e-9 and now <= deadline+1e-9:
                sample = capture(request, now)
                if (not isinstance(sample, CameraReadout) or sample.request_id != request.request_id
                        or abs(sample.produced_time_s-now) > 1e-9):
                    raise ValueError('unsolicited or backdated camera readout')
                self.captures.append(sample)
            due = sample.produced_time_s+self.config.delivery_delay_s if sample else float('inf')
            if due <= now+1e-9 and due <= deadline+1e-9:
                self.deliveries.append(FeedbackDelivery(request.request_id, now, 'returned'))
            elif now >= deadline-1e-9:
                self.deliveries.append(FeedbackDelivery(request.request_id, now, 'timeout'))
        self.time_s = now

    def observe(self, base: PolicyObservation):
        if self.closed or (base.env_id, base.episode_id, base.time_s) != (self.env_id, self.episode_id, self.time_s):
            raise ValueError('camera observation scope/time differs')
        feedback = []
        for delivery in self.deliveries:
            request = next(r for r in self.attempts if r.request_id == delivery.request_id)
            sample = next((s for s in self.captures if s.request_id == delivery.request_id), None)
            capture = age = None
            if delivery.status == 'timeout':
                status = 'timeout'
            else:
                capture, age = sample.capture_time_s, max(0., self.time_s-sample.capture_time_s)
                if capture < request.time_s-1e-9 or age > self.config.maximum_age_s+1e-9:
                    status = 'stale'
                elif sample.missing:
                    status = 'missing'
                elif sample.confidence < self.config.minimum_confidence:
                    status = 'low_confidence'
                else:
                    status = 'readable'
            feedback.append(CameraFeedback(request.request_id, request.purpose, capture, delivery.time_s, age, status,
                                           sample.text if status == 'readable' else None,
                                           sample.confidence if status == 'readable' else None))
        result = CameraActorObservation(base, tuple(feedback), self.budget())
        self.publications.append(dict(attempt_count=len(self.attempts), observation=result.to_dict()))
        return result

    def close(self):
        self.closed = True
        return tuple(r.request_id for r in self.attempts if r.accepted and not any(d.request_id == r.request_id for d in self.deliveries))
