"""CPU text/feedback contracts, including adversarial evidence edits."""

from dataclasses import replace
from types import SimpleNamespace
import unittest

from vskill_os.adapters.text_feedback import session_from_spec
from vskill_os.adapters.feedback_environment import FeedbackEnvironment
from vskill_os.adapters.isaac.learning.feedback_verify import check_physical_binding
from vskill_os.learning.contracts import JointRecord, PhysicalKeyRecord
from vskill_os.learning.feedback import ActorObservation, FeedbackConfig, ObservationAccess, ReadoutFault
from vskill_os.learning.feedback_demo import CASES, build_feedback_case, demo_event, demo_observation
from vskill_os.learning.feedback_evaluation import evaluate_feedback
from vskill_os.learning.feedback_session import FeedbackEpisode, FeedbackSpec
from vskill_os.learning.environment import CleanupRecord, EpisodeMetadata, JointControlSpec, JointTargetAction, ResetSpec, StepResult
from vskill_os.learning.text_task import DigitalInputPlan, EpisodeScope, FocusChange, TextTaskConfig, TextTaskState
from vskill_os.learning.replay import build_replay_case


SCOPE = EpisodeScope("env-0", "episode-0", "contract_seconds")


class TextStateTests(unittest.TestCase):
    def state(self, **kwargs):
        return TextTaskState(SCOPE, kwargs.get("task", TextTaskConfig()), kwargs.get("plan", DigitalInputPlan()))

    def type_keys(self, state, keys):
        for key in keys:
            t, i = state.time_s+.1, len(state.events)
            state.advance(t+.05, (demo_event(SCOPE, i, t, key), demo_event(SCOPE, i+1, t+.05, key, "keyup")))

    def test_cursor_insertion_backspace_delete_and_boundary_behavior(self):
        state = self.state(task=TextTaskConfig(initial_text="as"))
        self.type_keys(state, ("left", "backspace", "a", "home", "delete", "end", "s", "backspace"))
        self.assertEqual((state.history[-1].text, state.history[-1].cursor), ("s", 1))
        self.type_keys(state, ("home", "backspace", "end", "delete"))
        self.assertEqual(state.history[-1].text, "s")

    def test_repeated_characters_require_keyup_and_rejected_batch_is_atomic(self):
        state = self.state()
        down = demo_event(SCOPE, 0, .1)
        state.advance(.1, (down,))
        with self.assertRaises(ValueError):
            state.advance(.3, (demo_event(SCOPE, 1, .2),))
        self.assertEqual(len(state.events), 1)
        self.assertEqual(state.time_s, .1)
        state.advance(.2, (demo_event(SCOPE, 1, .2, kind="keyup"),))
        self.type_keys(state, ("a",))
        self.assertEqual(state.history[-1].text, "aa")

    def test_drop_focus_loss_and_keyup_are_separate_digital_effects(self):
        plan = DigitalInputPlan((0,), (FocusChange(.2, False), FocusChange(.4, True)))
        state = self.state(plan=plan)
        self.type_keys(state, ("a", "s", "a"))
        self.assertEqual([item.effect for item in state.transitions], ["software_drop", "focus_lost", "insert"])
        self.assertEqual(state.history[-1].text, "a")

    def test_focus_change_precedes_simultaneous_key_and_snapshot_is_historical(self):
        state = self.state(plan=DigitalInputPlan(focus_changes=(FocusChange(.1, False), FocusChange(.2, True))))
        state.advance(.3, (demo_event(SCOPE, 0, .1), demo_event(SCOPE, 1, .15, kind="keyup"),
                           demo_event(SCOPE, 2, .2), demo_event(SCOPE, 3, .3, kind="keyup")))
        self.assertEqual(state.snapshot_at(.1).text, "")
        self.assertEqual(state.snapshot_at(.2).text, "a")
        with self.assertRaises(ValueError):
            state.snapshot_at(.31)

    def test_scope_sequence_duplicate_past_and_future_events_fail_closed(self):
        event = demo_event(SCOPE, 0, .1)
        for stamp in (replace(event.stamp, env_id="other"), replace(event.stamp, episode_id="old"),
                      replace(event.stamp, clock_domain="wall"), replace(event.stamp, sequence=1),
                      replace(event.stamp, arrival_time_s=.3)):
            state = self.state()
            with self.subTest(stamp=stamp), self.assertRaises(ValueError):
                state.advance(.2, (replace(event, stamp=stamp),))
            self.assertFalse(state.events)
        state = self.state()
        state.advance(.2, (event,))
        with self.assertRaises(ValueError):
            state.advance(.3, (replace(event, stamp=replace(event.stamp, sequence=1, arrival_time_s=.25)),))
        with self.assertRaises(ValueError):
            state.advance(.3, (demo_event(SCOPE, 1, .2, kind="keyup"),))

    def test_maximum_text_length_is_enforced_without_disabling_editing(self):
        state = self.state(task=TextTaskConfig(maximum_length=1))
        self.type_keys(state, ("a", "s", "backspace", "s"))
        self.assertEqual(state.history[-1].text, "s")
        self.assertEqual(state.transitions[1].effect, "length_limit")


class FeedbackIsolationTests(unittest.TestCase):
    def session(self, **kwargs):
        self.spec = FeedbackSpec(**kwargs)
        return session_from_spec(self.spec, scope=SCOPE)

    def view(self, session):
        return session.observe(demo_observation(SCOPE, session.queue.time_s))

    def test_no_request_no_truth_and_no_delivery_before_arrival(self):
        session = self.session()
        self.assertFalse(self.view(session).feedback)
        session.request("intermediate")
        session.advance(.05)
        self.assertEqual(session.queue.captures[0].text, "")
        self.assertFalse(self.view(session).feedback)
        session.advance(.15, (demo_event(SCOPE, 0, .06), demo_event(SCOPE, 1, .07, kind="keyup")))
        view = self.view(session)
        self.assertEqual(session.text_state.history[-1].text, "a")
        self.assertEqual(view.feedback[0].text, "")
        self.assertAlmostEqual(view.feedback[0].capture_time_s, .02)
        self.assertAlmostEqual(view.feedback[0].arrival_time_s, .1)
        self.assertEqual(view.budget.accepted_requests, 1)

    def test_empty_missing_stale_and_low_confidence_are_distinct(self):
        for fault, status, text in ((None, "readable", ""), (ReadoutFault(0, missing=True), "missing", None),
                                   (ReadoutFault(0, stale_by_s=.2), "stale", None),
                                   (ReadoutFault(0, confidence=.1), "low_confidence", None)):
            session = self.session(readout_faults=() if fault is None else (fault,))
            session.advance(.1)
            session.request("final")
            session.advance(.25)
            item = self.view(session).feedback[-1]
            self.assertEqual((item.status, item.text), (status, text))
            if text is None:
                self.assertIsNone(item.confidence)

    def test_out_of_order_arrival_is_correlated_and_old_frame_never_refreshed(self):
        session = self.session(readout_faults=(ReadoutFault(0, delivery_delay_s=.2), ReadoutFault(1, delivery_delay_s=0.)))
        first = session.request("intermediate")
        session.advance(.05)
        second = session.request("intermediate")
        session.advance(.1)
        self.assertEqual([item.request_id for item in self.view(session).feedback], [second.request_id])
        session.advance(.25)
        self.assertEqual([item.request_id for item in self.view(session).feedback], [first.request_id, second.request_id])
        capture = self.view(session).feedback[0].capture_time_s
        session.advance(.6)
        self.assertEqual(self.view(session).feedback[0].status, "stale")
        self.assertEqual(self.view(session).feedback[0].capture_time_s, capture)
        self.assertTrue(evaluate_feedback(session.close(), expected=self.spec, scope=SCOPE)["isolation_passed"])

    def test_timeout_hides_captured_payload_and_late_response_cannot_reappear(self):
        session = self.session(readout_faults=(ReadoutFault(0, delivery_delay_s=.5),))
        session.request("final")
        session.advance(.4)
        item = self.view(session).feedback[0]
        self.assertEqual(item.status, "timeout")
        self.assertIsNone(item.text)
        self.assertIsNone(item.capture_time_s)
        self.assertAlmostEqual(self.view(session).budget.elapsed_wait_s, .3)
        session.advance(.8)
        self.assertEqual(self.view(session).feedback[0].status, "timeout")
        self.assertEqual(len(session.queue.deliveries), 1)

    def test_pending_reservations_prevent_overspending_then_release_unused_time(self):
        session = self.session(config=FeedbackConfig(verification_time_budget_s=.5))
        self.assertTrue(session.request("intermediate").accepted)
        self.assertEqual(session.request("intermediate").rejection, "BUDGET_EXHAUSTED")
        self.assertAlmostEqual(self.view(session).budget.reserved_wait_s, .3)
        session.advance(.15)
        self.assertTrue(session.request("final").accepted)
        session.advance(.3)
        self.assertAlmostEqual(self.view(session).budget.elapsed_wait_s, .2)
        self.assertEqual(session.request("intermediate").rejection, "FINAL_ALREADY_REQUESTED")
        self.assertTrue(evaluate_feedback(session.close(), expected=self.spec, scope=SCOPE)["isolation_passed"])

    def test_pending_count_request_count_and_remaining_horizon_limits(self):
        session = self.session(config=FeedbackConfig(maximum_pending=1, maximum_requests=1))
        self.assertTrue(session.request().accepted)
        self.assertEqual(session.request().rejection, "PENDING_LIMIT")
        session.advance(.2)
        self.assertEqual(session.request().rejection, "BUDGET_EXHAUSTED")
        session.advance(2.8)
        self.assertEqual(session.request("final").rejection, "INSUFFICIENT_HORIZON")

    def test_allowlist_has_no_oracle_debug_fault_or_success_fields(self):
        session = self.session(access=ObservationAccess(joint_state=False, physical_keys=False))
        base = replace(demo_observation(SCOPE, 0.), joints=(JointRecord("joint", 0., 0., 0.),),
                       keys=(PhysicalKeyRecord("a", 0., False, 0.),))
        visible = session.observe(base)
        self.assertFalse(visible.joints or visible.keys)
        for key in ("text_oracle", "info", "success", "focus", "event_log", "readout_faults", "pending_payload"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                ActorObservation.from_dict({**visible.to_dict(), key: "hidden"})
        with self.assertRaises(ValueError):
            ObservationAccess(raw_key_events=True)

    def test_close_cancels_without_free_final_read_and_old_session_is_unusable(self):
        session = self.session()
        receipt = session.request("final")
        session.advance(.05)
        episode = session.close()
        self.assertEqual(episode.cancelled_request_ids, (receipt.request_id,))
        self.assertFalse(episode.deliveries)
        self.assertIs(session.close(), episode)
        for call in (lambda: session.request(), lambda: session.advance(.2), lambda: self.view(session)):
            with self.assertRaises((ValueError, RuntimeError)):
                call()
        fresh = session_from_spec(self.spec, scope=replace(SCOPE, episode_id="new"))
        fresh.advance(.2)
        view = fresh.observe(demo_observation(fresh.scope, .2))
        self.assertFalse(view.feedback)
        self.assertEqual(view.budget.accepted_requests, 0)

    def test_two_sessions_do_not_share_text_requests_or_budgets(self):
        left = self.session()
        other_scope = replace(SCOPE, env_id="env-1")
        right = session_from_spec(self.spec, scope=other_scope)
        left.request("final")
        right.advance(.2, (demo_event(other_scope, 0, .1), demo_event(other_scope, 1, .2, kind="keyup")))
        left.advance(.2)
        self.assertEqual(self.view(left).feedback[0].text, "")
        self.assertEqual(right.text_state.history[-1].text, "a")
        self.assertFalse(right.observe(demo_observation(other_scope, .2)).feedback)


class IndependentFeedbackReplayTests(unittest.TestCase):
    def test_all_positive_and_negative_contract_cases_and_json_roundtrip(self):
        for name in CASES:
            with self.subTest(name=name):
                spec, scope, episode = build_feedback_case(name)
                restored = FeedbackEpisode.from_json(episode.to_json())
                self.assertEqual(restored.sha256(), episode.sha256())
                result = evaluate_feedback(restored, expected=spec, scope=scope)
                self.assertTrue(result["isolation_passed"], result)
                self.assertEqual(result["text_task_passed"], name in {"verified_after_wait", "confirmed_retry"}, result)
                self.assertFalse(result["physical_success_claimed"] or result["camera_verified"])

    def test_tampered_text_delivery_budget_capture_and_visibility_fail_closed(self):
        spec, scope, episode = build_feedback_case("verified_after_wait")
        last = episode.publications[-1]
        forged = (
            (replace(episode, final_text_state=replace(episode.final_text_state, text="s")), "FINAL_TEXT_MISMATCH"),
            (replace(episode, captures=(*episode.captures[:-1], replace(episode.captures[-1], text="s"))), "CAPTURE_MISMATCH"),
            (replace(episode, deliveries=(replace(episode.deliveries[0], time_s=.01), *episode.deliveries[1:])), "DELIVERY_MISMATCH"),
            (replace(episode, config=replace(spec.config, maximum_requests=99)), "EXPERIMENT_MISMATCH"),
            (replace(episode, publications=(*episode.publications[:-1], replace(last, visible=replace(last.visible,
                budget=replace(last.visible.budget, elapsed_wait_s=0.))))), "OBSERVATION_LEAK_OR_MISMATCH"),
            (replace(episode, publications=(replace(episode.publications[0], visible=replace(episode.publications[0].visible,
                feedback=last.visible.feedback)), *episode.publications[1:])), "OBSERVATION_LEAK_OR_MISMATCH"),
            (replace(episode, attempts=(replace(episode.attempts[0], accepted=False, rejection="invented"), *episode.attempts[1:])), "REQUEST_ADMISSION"),
        )
        for changed, code in forged:
            with self.subTest(code=code):
                result = evaluate_feedback(changed, expected=spec, scope=scope)
                self.assertFalse(result["isolation_passed"] or result["text_task_passed"], result)
                self.assertIn(code, result["findings"])

    def test_final_feedback_before_later_input_cannot_certify_even_same_text(self):
        spec = FeedbackSpec()
        session = session_from_spec(spec, scope=SCOPE)
        session.advance(.2, (demo_event(SCOPE, 0, .1), demo_event(SCOPE, 1, .2, kind="keyup")))
        session.request("final")
        session.advance(.35)
        session.observe(demo_observation(SCOPE, .35))
        session.advance(.55, (demo_event(SCOPE, 2, .4, "backspace"), demo_event(SCOPE, 3, .45, "backspace", "keyup"),
                             demo_event(SCOPE, 4, .5), demo_event(SCOPE, 5, .55, kind="keyup")))
        session.observe(demo_observation(SCOPE, .55))
        result = evaluate_feedback(session.close(), expected=spec, scope=SCOPE)
        self.assertTrue(result["isolation_passed"])
        self.assertEqual(result["actual_text"], "a")
        self.assertFalse(result["text_task_passed"])

    def test_physical_binding_checks_raw_events_time_and_unfiltered_observation(self):
        # Synthetic fixture tests only the binding predicate. Source labels do
        # not make this a physical run; verify_bundle still requires raw assets.
        physical = build_replay_case("correct_press").episode
        _, _, feedback = build_feedback_case("verified_after_wait")
        frame = physical.observations[0]
        names = tuple(j.name for j in frame.joints)
        trace = SimpleNamespace(actions=(), control_spec=SimpleNamespace(joint_names=names))
        scope = EpisodeScope(physical.env_id, physical.episode_id, physical.clock_domain)
        publication = feedback.publications[0]
        base = replace(publication.base, env_id=scope.env_id, episode_id=scope.episode_id,
                       time_s=frame.stamp.capture_time_s, joints=frame.joints, keys=frame.keys,
                       previous_delta_rad=(0.,)*len(names))
        bound = replace(feedback, source="isaac_physx_feedback", scope=scope, events=physical.events,
                        physical_episode_sha256=physical.sha256(), end_time_s=physical.end_time_s,
                        termination=physical.termination, publications=(replace(publication, base=base),))
        self.assertEqual(check_physical_binding(bound, physical, trace), [])
        for changed in (replace(bound, physical_episode_sha256="0"*64), replace(bound, events=()),
                        replace(bound, end_time_s=physical.end_time_s+.1), replace(bound, source="contract_feedback")):
            self.assertIn("PHYSICAL_EPISODE_BINDING", check_physical_binding(changed, physical, trace))
        forged = replace(bound.publications[0], base=replace(base, previous_delta_rad=(1.,)*len(names)))
        self.assertIn("PHYSICAL_OBSERVATION_BINDING", check_physical_binding(replace(bound, publications=(forged,)), physical, trace))


class RecordingBackend:
    """No physics claim: count physical-step calls and inject a safety stop."""
    def __init__(self):
        self.calls, self.stop_next = 0, False
        self.control = JointControlSpec(("finger",), (-1.,), (1.,), (.3,), ("finger",), .1)
        self.closed = False

    def reset(self, reset):
        self.reset_spec, self.events, self.termination = reset, [], "finish_requested"
        self.observation = demo_observation(replace(SCOPE, episode_id=reset.episode_id), 0.)
        return self.observation, EpisodeMetadata(SCOPE.env_id, reset.episode_id, reset.seed, reset.episode_id,
                                                self.control, "contract_fixture", "fixed_test")

    def step(self, action):
        self.calls += 1
        self.observation = replace(self.observation, time_s=self.observation.time_s+.1)
        if self.stop_next:
            self.termination = "safety_stop"
        elif self.observation.time_s >= self.reset_spec.maximum_time_s-1e-9:
            self.termination = "time_limit"
        return StepResult(self.observation, (), self.termination == "safety_stop", self.termination == "time_limit",
                          ("CONTACT_FORCE",) if self.stop_next else (), ("private-record-ref",))

    def finish(self):
        pass

    def close(self):
        self.closed = True
        return CleanupRecord(SCOPE.env_id, self.reset_spec.episode_id, ())


class FeedbackPortTests(unittest.TestCase):
    def supervisor(self):
        self.backend = RecordingBackend()
        return FeedbackEnvironment(self.backend, clock_domain=SCOPE.clock_domain, source="contract_feedback")

    def action(self, observation, metadata):
        return JointTargetAction(metadata.control_spec.joint_names, (0.,), .1, observation.time_s+.1, metadata.owner_token, 0)

    def test_reset_invalidates_old_port_and_discards_pending_feedback(self):
        supervisor = self.supervisor()
        old, _, _ = supervisor.reset(ResetSpec("old"), FeedbackSpec())
        old.request_verification("final")
        old.finish()
        self.assertEqual(len(supervisor.episode.cancelled_request_ids), 1)
        new, initial, metadata = supervisor.reset(ResetSpec("new"), FeedbackSpec())
        self.assertFalse(initial.feedback)
        for call in (old.observe, old.request_verification, old.finish, lambda: old.step(self.action(initial, metadata))):
            with self.assertRaisesRegex(RuntimeError, "stale or closed"):
                call()
        self.assertEqual(self.backend.calls, 0)
        visible = new.step(self.action(initial, metadata)).observation
        self.assertFalse(visible.feedback)
        self.assertEqual(visible.budget.accepted_requests, 0)
        supervisor.close()

    def test_wait_advances_backend_and_cannot_bypass_safety(self):
        supervisor = self.supervisor()
        port, initial, metadata = supervisor.reset(ResetSpec("safety"), FeedbackSpec())
        port.request_verification("final")
        self.backend.stop_next = True
        result = port.step(self.action(initial, metadata))
        self.assertTrue(result.terminated)
        self.assertEqual(result.failure_codes, ("CONTACT_FORCE",))
        self.assertEqual(self.backend.calls, 1)
        self.assertNotIn("record_refs", result.to_dict())
        self.assertEqual(supervisor.episode.termination, "safety_stop")
        with self.assertRaises(RuntimeError):
            port.observe()

    def test_reset_requires_finish_and_horizon_matches_and_truncation_is_preserved(self):
        supervisor = self.supervisor()
        with self.assertRaises(ValueError):
            supervisor.reset(ResetSpec("bad", maximum_time_s=.1), FeedbackSpec())
        spec = FeedbackSpec(config=FeedbackConfig(horizon_s=.1))
        port, initial, metadata = supervisor.reset(ResetSpec("limit", maximum_time_s=.1), spec)
        with self.assertRaises(RuntimeError):
            supervisor.reset(ResetSpec("running"), FeedbackSpec())
        result = port.step(self.action(initial, metadata))
        self.assertTrue(result.truncated)
        self.assertFalse(result.terminated)
        self.assertEqual(supervisor.episode.end_time_s, .1)
        self.assertEqual(supervisor.episode.termination, "time_limit")


if __name__ == "__main__":
    unittest.main()
