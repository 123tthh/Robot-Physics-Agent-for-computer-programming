"""Camera gate contracts. Synthetic fixtures never count as physical evidence."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from vskill_os.learning.camera_evaluation import check_camera_feedback
from vskill_os.learning.camera_feedback import CameraActorObservation, CameraFeedbackQueue, CameraReadout, DesktopCameraSpec
from vskill_os.learning.environment import PolicyObservation
from vskill_os.learning.feedback import FeedbackConfig


def base(now, episode='unit'):
    return PolicyObservation('unit-env', episode, now, 'a', (), (), ())


def capture(request, now):
    return CameraReadout(request.request_id, now, now, 'synthetic-test-frame', 'b', .95, False)


def record(queue):
    return dict(env_id=queue.env_id, episode_id=queue.episode_id, end_time_s=queue.time_s,
                attempts=[r.to_dict() for r in queue.attempts], captures=[r.to_dict() for r in queue.captures],
                deliveries=[r.to_dict() for r in queue.deliveries], publications=deepcopy(queue.publications),
                cancelled_request_ids=list(queue.close()))


class CameraFeedbackTests(unittest.TestCase):
    def setUp(self):
        self.queue = CameraFeedbackQueue('unit-env', 'unit', FeedbackConfig(maximum_pending=1))

    def test_camera_sources_and_hidden_fields_are_strict(self):
        request = self.queue.request()
        sample = capture(request, .1)
        with self.assertRaises(ValueError):
            CameraReadout.from_dict({**sample.to_dict(), 'source': 'text_state_readout_v1'})
        ob = self.queue.observe(base(0.))
        with self.assertRaises(ValueError):
            CameraActorObservation.from_dict({**ob.to_dict(), 'terminal_text': 'a'})
        with self.assertRaises(ValueError):
            CameraReadout(request.request_id, .1, .2, 'f', 'a', .95, False)

    def test_capture_and_delivery_require_physics_clock_progress(self):
        self.queue.request()
        self.assertFalse(self.queue.observe(base(0.)).feedback)
        self.assertFalse(self.queue.captures)
        self.queue.advance(.1, capture)
        self.assertFalse(self.queue.observe(base(.1)).feedback)
        self.queue.advance(.2, capture)
        ob = self.queue.observe(base(.2))
        self.assertEqual(ob.feedback[0].text, 'b')
        self.assertEqual(ob.feedback[0].source, 'robot_camera_ocr_v1')
        self.assertFalse(hasattr(ob.feedback[0], 'frame_id'))
        self.assertAlmostEqual(ob.budget.elapsed_wait_s, .2)
        self.assertEqual(check_camera_feedback(record(self.queue), self.queue.config, .1), [])

    def test_pending_and_final_requests_are_not_free_or_repeated(self):
        first = self.queue.request()
        self.assertTrue(first.accepted)
        self.assertEqual(self.queue.request().rejection, 'PENDING_LIMIT')
        for t in (.1, .2): self.queue.advance(t, capture)
        final = self.queue.request('final')
        self.assertTrue(final.accepted)
        self.assertEqual(self.queue.request().rejection, 'FINAL_ALREADY_REQUESTED')
        for t in (.3, .4): self.queue.advance(t, capture)
        self.queue.observe(base(.4))
        evidence = record(self.queue)
        self.assertEqual(check_camera_feedback(evidence, self.queue.config, .1), [])

    def test_missing_and_low_confidence_never_publish_hidden_text(self):
        for missing in (True, False):
            queue = CameraFeedbackQueue('unit-env', 'unit', FeedbackConfig())
            queue.request()
            def provider(r, now):
                return CameraReadout(r.request_id, now, now, None if missing else 'unit-frame',
                                     None if missing else 'SECRET', None if missing else .2, missing)
            for t in (.1, .2): queue.advance(t, provider)
            feedback = queue.observe(base(.2)).feedback[0]
            self.assertEqual(feedback.status, 'missing' if missing else 'low_confidence')
            self.assertIsNone(feedback.text)
            self.assertIsNone(feedback.confidence)

    def test_old_frame_is_rejected_even_with_high_ocr_confidence(self):
        self.queue.advance(.2, capture)
        self.queue.request('final')
        def old_frame(r, now):
            return CameraReadout(r.request_id, now, .1, 'old-unit-frame', 'a', 1., False)
        for t in (.3, .4): self.queue.advance(t, old_frame)
        feedback = self.queue.observe(base(.4)).feedback[0]
        self.assertEqual(feedback.status, 'stale')
        self.assertIsNone(feedback.text)
        self.assertEqual(check_camera_feedback(record(self.queue), self.queue.config, .1), [])

    def test_timeout_cannot_create_or_return_a_late_frame(self):
        queue = CameraFeedbackQueue('unit-env', 'unit', FeedbackConfig(capture_delay_s=.5))
        queue.request()
        def forbidden(*args):
            raise AssertionError('capture happened after deadline')
        for t in (.1, .2, .3, .4, .5): queue.advance(t, forbidden)
        feedback = queue.observe(base(.5)).feedback[0]
        self.assertEqual(feedback.status, 'timeout')
        self.assertFalse(queue.captures)
        self.assertEqual(check_camera_feedback(record(queue), queue.config, .1), [])

    def test_finish_cancels_pending_request_without_capturing(self):
        request = self.queue.request('final')
        self.queue.observe(base(0.))
        evidence = record(self.queue)
        self.assertEqual(evidence['cancelled_request_ids'], [request.request_id])
        self.assertFalse(evidence['captures'])
        self.assertEqual(check_camera_feedback(evidence, self.queue.config, .1), [])
        with self.assertRaises(RuntimeError): self.queue.request()

    def test_unsolicited_or_backdated_provider_output_is_rejected(self):
        self.queue.request()
        with self.assertRaises(ValueError):
            self.queue.advance(.1, lambda r, now: replace(capture(r, now), request_id='other-episode'))
        with self.assertRaises(ValueError):
            self.queue.advance(.1, lambda r, now: replace(capture(r, now), produced_time_s=.2))
        self.assertFalse(self.queue.captures)

    def test_independent_replay_rejects_forged_visibility_and_budget(self):
        self.queue.request('final')
        self.queue.observe(base(0.))
        for t in (.1, .2):
            self.queue.advance(t, capture)
            self.queue.observe(base(t))
        evidence = record(self.queue)
        forged = deepcopy(evidence)
        forged['publications'][-1]['observation']['feedback'][0]['text'] = 'a'
        self.assertIn('CAMERA_FEEDBACK_LEAK_OR_REWRITE', check_camera_feedback(forged, self.queue.config, .1))
        forged = deepcopy(evidence)
        forged['publications'][0]['observation']['feedback'] = deepcopy(forged['publications'][-1]['observation']['feedback'])
        self.assertIn('CAMERA_EARLY_OR_MISSING_FEEDBACK', check_camera_feedback(forged, self.queue.config, .1))
        forged = deepcopy(evidence)
        forged['publications'][-1]['observation']['budget']['elapsed_wait_s'] = 0.
        self.assertIn('CAMERA_VISIBLE_BUDGET', check_camera_feedback(forged, self.queue.config, .1))

    def test_old_episode_port_cannot_read_new_camera_feedback(self):
        from vskill_os.adapters.isaac.learning.desktop_env import CameraPolicyPort, DesktopCameraEnvironment
        owner = DesktopCameraEnvironment.__new__(DesktopCameraEnvironment)
        owner.closed, owner.generation, owner.queue = False, 2, self.queue
        with self.assertRaisesRegex(RuntimeError, 'stale'):
            CameraPolicyPort(owner, 1).observe()

    def test_calibration_rejects_outside_image_and_invalid_bounds(self):
        for change in (dict(input_roi=(1, 2, 1400, 20)), dict(input_roi=(3, 2, 1, 4)),
                       dict(ocr_scale=0), dict(render_frames=100), dict(ocr_timeout_s=float('nan'))):
            with self.subTest(change=change), self.assertRaises(ValueError): DesktopCameraSpec(**change)


class CameraPixelAdapterTests(unittest.TestCase):
    def test_ocr_preserves_different_text_and_never_substitutes_goal(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest('optional learning-desktop Pillow')
        from vskill_os.adapters.camera_ocr import recognize
        header = 'level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n'
        payload = header+'5\t1\t1\t1\t1\t1\t0\t0\t20\t20\t95\tb\n'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'synthetic-test.png'
            Image.new('RGB', (1280, 720), 'white').save(path)
            with patch('vskill_os.adapters.camera_ocr.subprocess.run', return_value=SimpleNamespace(returncode=0, stdout=payload)) as process:
                result = recognize(path, DesktopCameraSpec())
                self.assertEqual(result['text'], 'b')
                self.assertAlmostEqual(result['confidence'], .95)
                self.assertNotIn('whitelist', repr(process.call_args))
            with patch('vskill_os.adapters.camera_ocr.subprocess.run', return_value=SimpleNamespace(returncode=0, stdout=header)):
                result = recognize(path, DesktopCameraSpec())
                self.assertTrue(result['missing'])
                self.assertIsNone(result['text'])


if __name__ == '__main__':
    unittest.main()
