"""Explicit text-state readout adapter for early feedback experiments.

This is a delayed simulated readout, not OCR, a camera frame or desktop proof.
Only the supervisor can select software/recognition disturbances.
"""

from __future__ import annotations

from ..learning.feedback import CapturedReadout, ReadoutFault, VerificationReceipt
from ..learning.feedback_session import FeedbackSession
from ..learning.text_task import TextTaskState


class TextStateReadout:
    def __init__(self, state: TextTaskState, faults: tuple[ReadoutFault, ...]):
        self._state = state
        self._faults = {fault.request_index: fault for fault in faults}

    def capture(self, request: VerificationReceipt, time_s: float, request_index: int) -> CapturedReadout:
        fault = self._faults.get(request_index, ReadoutFault(request_index))
        captured = max(0., time_s-fault.stale_by_s)
        snapshot = self._state.snapshot_at(captured)
        text = snapshot.text if fault.text_override is None else fault.text_override
        return CapturedReadout(request.request_id, time_s, captured, "text_state_readout_v1",
                               None if fault.missing else text, None if fault.missing else fault.confidence, fault.missing)


def text_feedback_session(**kwargs) -> FeedbackSession:
    faults = kwargs["readout_faults"]
    return FeedbackSession(**kwargs, provider_factory=lambda state: TextStateReadout(state, faults))


def session_from_spec(spec, *, scope, source="contract_feedback") -> FeedbackSession:
    return text_feedback_session(scope=scope, source=source, task=spec.task, config=spec.config,
                                 access=spec.access, input_plan=spec.input_plan, readout_faults=spec.readout_faults)
