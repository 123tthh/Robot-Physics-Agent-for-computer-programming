"""Offline reference replay; deliberately does not call the live text/queue/filter.

The runner supplies the frozen experiment specification and scope. A trace may
not relax its own budget, visibility, disturbances or target to obtain a pass.
This evaluator establishes simulated text/feedback correctness, not OCR quality
or physical release. The Isaac verifier separately binds and checks raw physics.
"""

from __future__ import annotations

from typing import Any

from .feedback_session import FeedbackEpisode, FeedbackSpec
from .text_task import EpisodeScope

EPS = 1e-8


def _same(actual: Any, expected: Any) -> bool:
    if isinstance(actual, (float, int)) and not isinstance(actual, bool):
        return isinstance(expected, (float, int)) and not isinstance(expected, bool) and abs(actual-expected) <= EPS
    if isinstance(actual, (list, tuple)) and isinstance(expected, (list, tuple)):
        return len(actual) == len(expected) and all(_same(a, b) for a, b in zip(actual, expected))
    if type(actual) is not type(expected):
        return False
    if isinstance(actual, dict):
        return actual.keys() == expected.keys() and all(_same(actual[k], expected[k]) for k in actual)
    return actual == expected


def evaluate_feedback(episode: FeedbackEpisode, *, expected: FeedbackSpec, scope: EpisodeScope) -> dict:
    findings: list[str] = []

    def check(condition: bool, code: str) -> None:
        if not condition and code not in findings:
            findings.append(code)

    check(episode.scope == scope, "SCOPE_MISMATCH")
    check(all(getattr(episode, field) == getattr(expected, field)
              for field in ("task", "config", "access", "input_plan", "readout_faults")), "EXPERIMENT_MISMATCH")
    task, cfg, plan = expected.task, expected.config, expected.input_plan
    end = episode.end_time_s
    check(0 <= end <= cfg.horizon_s+EPS, "EPISODE_TIME")
    text, cursor, focused = task.initial_text, len(task.initial_text), task.initial_focus
    history = [dict(time_s=0., text=text, cursor=cursor, focused=focused)]
    transitions = []
    bindings = {item.key: item.character for item in task.character_keys}
    focus_index, down_index, last_time = 0, 0, 0.
    seen, held = set(), set()

    def focus_until(time_s: float) -> None:
        nonlocal focus_index, focused
        while focus_index < len(plan.focus_changes) and plan.focus_changes[focus_index].time_s <= time_s+EPS:
            change = plan.focus_changes[focus_index]
            focused = change.focused
            history.append(dict(time_s=change.time_s, text=text, cursor=cursor, focused=focused))
            focus_index += 1

    focus_until(0.)
    for index, event in enumerate(episode.events):
        stamp = event.stamp
        check((stamp.env_id, stamp.episode_id, stamp.clock_domain) == (scope.env_id, scope.episode_id, scope.clock_domain)
              and stamp.sequence == index and stamp.record_id not in seen
              and 0 < stamp.arrival_time_s <= end+EPS and stamp.arrival_time_s >= last_time-EPS, "EVENT_IDENTITY")
        seen.add(stamp.record_id)
        last_time = stamp.arrival_time_s
        focus_until(last_time)
        if event.event_type == "keyup":
            held.discard(event.key)
            continue
        check(event.reason == "physical_threshold" and event.key not in held, "INVALID_KEYDOWN")
        held.add(event.key)
        before = history[-1]
        effect = "ignored_key"
        if down_index in plan.dropped_keydown_indices:
            effect = "software_drop"
        elif not focused:
            effect = "focus_lost"
        elif event.key in bindings:
            if len(text) == task.maximum_length:
                effect = "length_limit"
            else:
                text = text[:cursor] + bindings[event.key] + text[cursor:]
                cursor += 1
                effect = "insert"
        elif event.key == "backspace":
            effect = "backspace"
            if cursor:
                text = text[:cursor-1] + text[cursor:]
                cursor -= 1
        elif event.key == "delete":
            text = text[:cursor] + text[cursor+1:]
            effect = "delete"
        elif event.key in ("left", "right", "home", "end"):
            if event.key == "left":
                cursor = max(0, cursor-1)
            elif event.key == "right":
                cursor = min(len(text), cursor+1)
            else:
                cursor = 0 if event.key == "home" else len(text)
            effect = "move_cursor"
        after = dict(time_s=last_time, text=text, cursor=cursor, focused=focused)
        transitions.append(dict(event_id=stamp.record_id, keydown_index=down_index, time_s=last_time,
                                effect=effect, before=before, after=after))
        history.append(after)
        down_index += 1
    focus_until(end)
    check(_same([item.to_dict() for item in episode.text_transitions], transitions), "TEXT_TRANSITION_MISMATCH")
    final = dict(time_s=end, text=text, cursor=cursor, focused=focused)
    check(_same(episode.final_text_state.to_dict(), final), "FINAL_TEXT_MISMATCH")

    faults = {fault.request_index: fault for fault in expected.readout_faults}
    jobs: list[dict] = []
    expected_captures, expected_deliveries = {}, {}
    attempt_time = 0.

    def budget_at(time_s: float, selected: list[dict]) -> dict:
        spent = sum(min(time_s, job["due"])-job["request"].time_s for job in selected)
        pending = [job for job in selected if job["due"] > time_s+EPS]
        reserve = sum(max(0., job["deadline"]-time_s) for job in pending)
        return dict(accepted_requests=len(selected), pending_requests=len(pending),
                    remaining_requests=cfg.maximum_requests-len(selected), elapsed_wait_s=spent,
                    reserved_wait_s=reserve, unreserved_time_s=max(0., cfg.verification_time_budget_s-spent-reserve))

    for index, request in enumerate(episode.attempts):
        check(request.request_id == f"{scope.env_id}:{scope.episode_id}:verify:{index}"
              and attempt_time-EPS <= request.time_s <= end+EPS, "REQUEST_IDENTITY")
        attempt_time = request.time_s
        budget = budget_at(request.time_s, jobs)
        rejection = None
        if any(job["request"].purpose == "final" for job in jobs):
            rejection = "FINAL_ALREADY_REQUESTED"
        elif request.time_s + cfg.request_timeout_s > cfg.horizon_s+EPS:
            rejection = "INSUFFICIENT_HORIZON"
        elif budget["pending_requests"] >= cfg.maximum_pending:
            rejection = "PENDING_LIMIT"
        elif budget["remaining_requests"] <= 0 or budget["unreserved_time_s"] < cfg.request_timeout_s-EPS:
            rejection = "BUDGET_EXHAUSTED"
        check(request.accepted == (rejection is None) and request.rejection == rejection, "REQUEST_ADMISSION")
        # Replay admission from the frozen rules even if the receipt was forged.
        if rejection is not None:
            continue
        fault = faults.get(len(jobs))
        capture_delay = cfg.capture_delay_s if fault is None or fault.capture_delay_s is None else fault.capture_delay_s
        delivery_delay = cfg.delivery_delay_s if fault is None or fault.delivery_delay_s is None else fault.delivery_delay_s
        capture, deadline = request.time_s+capture_delay, request.time_s+cfg.request_timeout_s
        arrival = capture + delivery_delay
        timeout = (fault is not None and fault.no_response) or arrival > deadline+EPS
        due = deadline if timeout else arrival
        job = dict(request=request, capture=capture, deadline=deadline, due=due, timeout=timeout, attempt_index=index)
        jobs.append(job)
        if capture <= min(end, deadline)+EPS:
            acquired = max(0., capture-(0. if fault is None else fault.stale_by_s))
            document = next(item for item in reversed(history) if item["time_s"] <= acquired+EPS)
            missing = fault is not None and fault.missing
            estimate = document["text"] if fault is None or fault.text_override is None else fault.text_override
            expected_captures[request.request_id] = dict(request_id=request.request_id, produced_time_s=capture,
                capture_time_s=acquired, source="text_state_readout_v1", text=None if missing else estimate,
                confidence=None if missing else (1. if fault is None else fault.confidence), missing=missing)
        if due <= end+EPS:
            expected_deliveries[request.request_id] = dict(request_id=request.request_id, time_s=due,
                                                          status="timeout" if timeout else "returned")

    def compare_stream(records: tuple, expected_records: dict, time_field: str, code: str) -> None:
        check(len(records) == len({item.request_id for item in records})
              and _same({item.request_id: item.to_dict() for item in records}, expected_records)
              and all(getattr(a, time_field) <= getattr(b, time_field)+EPS for a, b in zip(records, records[1:])), code)

    compare_stream(episode.captures, expected_captures, "produced_time_s", "CAPTURE_MISMATCH")
    compare_stream(episode.deliveries, expected_deliveries, "time_s", "DELIVERY_MISMATCH")
    cancelled = tuple(job["request"].request_id for job in jobs if job["due"] > end+EPS)
    check(episode.cancelled_request_ids == cancelled, "CANCELLATION_MISMATCH")
    publication_time, previous_count = 0., 0
    last_expected_visible: list[dict] = []
    for publication in episode.publications:
        base, count = publication.base, publication.attempt_count
        now = base.time_s
        check(base.env_id == scope.env_id and base.episode_id == scope.episode_id
              and publication_time-EPS <= now <= end+EPS and previous_count <= count <= len(episode.attempts), "PUBLICATION_IDENTITY")
        check(all(r.time_s <= now+EPS for r in episode.attempts[:count])
              and all(r.time_s >= now-EPS for r in episode.attempts[count:]), "PUBLICATION_REQUEST_ORDER")
        publication_time, previous_count = now, count
        selected = [job for job in jobs if job["attempt_index"] < count]
        visible = []
        for job in selected:
            if job["due"] > now+EPS:
                continue
            request = job["request"]
            sample = expected_captures.get(request.request_id)
            captured = age = None
            estimate = confidence = None
            status = "timeout"
            if not job["timeout"] and sample is not None:
                captured = sample["capture_time_s"]
                age = max(0., now-captured)
                if captured < request.time_s-EPS or age > cfg.maximum_age_s+EPS:
                    status = "stale"
                elif sample["missing"]:
                    status = "missing"
                elif sample["confidence"] < cfg.minimum_confidence:
                    status = "low_confidence"
                else:
                    status, estimate, confidence = "readable", sample["text"], sample["confidence"]
            visible.append(dict(request_id=request.request_id, purpose=request.purpose, capture_time_s=captured,
                                arrival_time_s=job["due"], age_s=age, status=status, text=estimate,
                                confidence=confidence, source="text_state_readout_v1"))
        wanted = dict(env_id=scope.env_id, episode_id=scope.episode_id, time_s=now, target_key=base.target_key,
                      joints=[j.to_dict() for j in base.joints] if expected.access.joint_state else [],
                      keys=[k.to_dict() for k in base.keys] if expected.access.physical_keys else [],
                      previous_delta_rad=list(base.previous_delta_rad), feedback=visible, budget=budget_at(now, selected))
        check(_same(publication.visible.to_dict(), wanted), "OBSERVATION_LEAK_OR_MISMATCH")
        last_expected_visible = visible

    failures = []
    if episode.termination != "finish_requested":
        failures.append("NOT_FINISHED")
    if text != task.target_text:
        failures.append("TEXT_INCORRECT")
    if held:
        failures.append("DIGITAL_KEYS_HELD")
    finals = [item for item in last_expected_visible if item["purpose"] == "final" and item["status"] == "readable"]
    last_input = max([0.] + [item["time_s"] for item in transitions]
                     + [change.time_s for change in plan.focus_changes if change.time_s <= end+EPS])
    if (not finals or finals[-1]["text"] != task.target_text or finals[-1]["capture_time_s"] < last_input-EPS
            or not episode.publications or abs(episode.publications[-1].base.time_s-end) > EPS):
        failures.append("FINAL_FEEDBACK_UNCONFIRMED")
    if finals and end-finals[-1]["capture_time_s"] > cfg.maximum_age_s+EPS:
        failures.append("FINAL_FEEDBACK_EXPIRED")
    if findings:
        failures.append("INVALID_EVIDENCE")
    return dict(scope=episode.source, isolation_passed=not findings, text_task_passed=not failures,
                findings=findings, task_failures=failures, target_text=task.target_text, actual_text=text,
                accepted_requests=len(jobs), rejected_requests=len(episode.attempts)-len(jobs),
                completed_requests=len(expected_deliveries), cancelled_requests=len(cancelled),
                verification_wait_s=budget_at(end, jobs)["elapsed_wait_s"],
                physical_success_claimed=False, camera_verified=False, learned_policy=False)
