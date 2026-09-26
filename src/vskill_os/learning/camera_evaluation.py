"""Independent replay of camera request admission, visibility and budgets.

Does not call CameraFeedbackQueue or its observation publisher. Pixel evidence
and binding to raw physical records are checked by the optional desktop adapter.
"""
import math

from .camera_feedback import CameraActorObservation, CameraReadout
from .feedback import FeedbackDelivery, VerificationReceipt


def check_camera_feedback(record, config, control_dt):
    errors = []
    def check(condition, code):
        if not condition and code not in errors:
            errors.append(code)
    def close(a, b):
        return abs(a-b) <= 1e-8
    def tick_at_or_after(t):
        return math.ceil((t-1e-9)/control_dt)*control_dt
    try:
        attempts = [VerificationReceipt.from_dict(r) for r in record['attempts']]
        captures = [CameraReadout.from_dict(r) for r in record['captures']]
        deliveries = [FeedbackDelivery.from_dict(r) for r in record['deliveries']]
        check(len({r.request_id for r in captures}) == len(captures), 'DUPLICATE_CAMERA_CAPTURE')
        check(len({d.request_id for d in deliveries}) == len(deliveries), 'DUPLICATE_CAMERA_DELIVERY')
        captured = {r.request_id: r for r in captures}
        delivered = {d.request_id: d for d in deliveries}
        accepted = []
        def budget(now, selected):
            done = {r.request_id: delivered[r.request_id] for r in selected
                    if r.request_id in delivered and delivered[r.request_id].time_s <= now+1e-9}
            elapsed = sum((done[r.request_id].time_s if r.request_id in done else now)-r.time_s for r in selected)
            reserve = sum(max(0., config.request_timeout_s-now+r.time_s) for r in selected if r.request_id not in done)
            return dict(accepted_requests=len(selected), pending_requests=len(selected)-len(done),
                        remaining_requests=config.maximum_requests-len(selected), elapsed_wait_s=elapsed,
                        reserved_wait_s=reserve, unreserved_time_s=max(0., config.verification_time_budget_s-elapsed-reserve))
        for index, request in enumerate(attempts):
            check(request.request_id == f"{record['env_id']}:{record['episode_id']}:camera:{index}", 'CAMERA_REQUEST_ID')
            check(0 <= request.time_s <= record['end_time_s'] and close(request.time_s, tick_at_or_after(request.time_s)), 'CAMERA_REQUEST_CLOCK')
            check(index == 0 or request.time_s >= attempts[index-1].time_s, 'CAMERA_REQUEST_ORDER')
            b = budget(request.time_s, accepted)
            reason = None
            if any(r.purpose == 'final' for r in accepted): reason = 'FINAL_ALREADY_REQUESTED'
            elif request.time_s+config.request_timeout_s > config.horizon_s+1e-9: reason = 'INSUFFICIENT_HORIZON'
            elif b['pending_requests'] >= config.maximum_pending: reason = 'PENDING_LIMIT'
            elif not b['remaining_requests'] or b['unreserved_time_s']+1e-9 < config.request_timeout_s: reason = 'BUDGET_EXHAUSTED'
            check(request.accepted == (reason is None) and request.rejection == reason, 'CAMERA_BUDGET_ADMISSION')
            if request.accepted: accepted.append(request)
        ids = {r.request_id for r in accepted}
        check(set(captured) <= ids and set(delivered) <= ids, 'UNSOLICITED_CAMERA_FEEDBACK')
        for request in accepted:
            sample, delivery = captured.get(request.request_id), delivered.get(request.request_id)
            # Captures run at the next physical control boundary. A zero-delay
            # request still cannot invoke rendering without a physical step.
            capture_tick = max(request.time_s+control_dt, tick_at_or_after(request.time_s+config.capture_delay_s))
            deadline = request.time_s+config.request_timeout_s
            should_capture = capture_tick <= min(record['end_time_s'], deadline)+1e-9
            check((sample is not None) == should_capture, 'CAMERA_CAPTURE_COVERAGE')
            if sample:
                check(close(sample.produced_time_s, capture_tick), 'CAMERA_CAPTURE_BACKDATED')
            due = sample.produced_time_s+config.delivery_delay_s if sample else float('inf')
            status = 'returned' if due <= deadline+1e-9 else 'timeout'
            deliver_tick = tick_at_or_after(min(due, deadline))
            check((delivery is not None) == (deliver_tick <= record['end_time_s']+1e-9), 'CAMERA_DELIVERY_COVERAGE')
            if delivery:
                check(close(delivery.time_s, deliver_tick) and delivery.status == status, 'CAMERA_DELIVERY_CLOCK')
        expected_cancelled = [r.request_id for r in accepted if r.request_id not in delivered]
        check(record['cancelled_request_ids'] == expected_cancelled, 'CAMERA_CANCELLED_REQUESTS')
        previous_time = -1.
        for item in record['publications']:
            observation = CameraActorObservation.from_dict(item['observation'])
            base, visible = observation.physical, observation.feedback
            now = base.time_s
            check((base.env_id, base.episode_id) == (record['env_id'], record['episode_id'])
                  and previous_time <= now <= record['end_time_s'], 'CAMERA_PUBLICATION_SCOPE')
            previous_time = now
            count = item['attempt_count']
            check(0 <= count <= len(attempts) and all(r.time_s <= now+1e-9 for r in attempts[:count]), 'CAMERA_ATTEMPT_VISIBILITY')
            selected = [r for r in attempts[:count] if r.accepted]
            visible_requests = [r for r in selected if r.request_id in delivered and delivered[r.request_id].time_s <= now+1e-9]
            check([v.request_id for v in visible] == [r.request_id for r in visible_requests], 'CAMERA_EARLY_OR_MISSING_FEEDBACK')
            for view, request in zip(visible, visible_requests):
                sample, delivery = captured.get(request.request_id), delivered[request.request_id]
                capture = age = None
                if delivery.status == 'timeout': status = 'timeout'
                else:
                    capture, age = sample.capture_time_s, max(0., now-sample.capture_time_s)
                    if capture < request.time_s-1e-9 or age > config.maximum_age_s+1e-9: status = 'stale'
                    elif sample.missing: status = 'missing'
                    elif sample.confidence < config.minimum_confidence: status = 'low_confidence'
                    else: status = 'readable'
                expected = dict(request_id=request.request_id, purpose=request.purpose, capture_time_s=capture,
                    arrival_time_s=delivery.time_s, age_s=age, status=status,
                    text=sample.text if status == 'readable' else None,
                    confidence=sample.confidence if status == 'readable' else None, source='robot_camera_ocr_v1')
                check(view.to_dict() == expected, 'CAMERA_FEEDBACK_LEAK_OR_REWRITE')
            wanted = budget(now, selected)
            check(all(close(observation.budget.to_dict()[k], v) for k, v in wanted.items()), 'CAMERA_VISIBLE_BUDGET')
        check(bool(record['publications']) and close(previous_time, record['end_time_s']), 'CAMERA_FINAL_OBSERVATION_MISSING')
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError) as exc:
        errors.append('INVALID_CAMERA_RECORD: '+str(exc))
    return errors
