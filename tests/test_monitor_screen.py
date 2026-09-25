from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from vskill_os.adapters.isaac.monitor_screen import CameraFrameGate, FrameReceiver, ScreenFrameError, render_marker, verify_robot_camera_text
from vskill_os.simulation.screen_feedback import PixelFrame, publish_frame


class MonitorScreenTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def publish(self, frame_id=1, *, timestamp=100.0, event_count=2, source='pygame_desktop'):
        frame = PixelFrame(frame_id, timestamp, 2, 2, bytes([255, 0, 0] * 4), source,
                           event_count, (0, 0, 2, 1), (0, 1, 2, 2))
        publish_frame(frame, self.directory)
        return frame

    def mutate(self, **changes):
        path = self.directory / 'frame.json'
        metadata = json.loads(path.read_text())
        metadata.update(changes)
        path.write_text(json.dumps(metadata))

    def test_publisher_receiver_roundtrip_and_event_barrier(self):
        published = self.publish()
        receiver = FrameReceiver(self.directory, clock=lambda: 100.1)
        frame = receiver.receive(minimum_event_count=2)
        self.assertEqual(frame.pixel_sha256, published.pixel_sha256)
        self.assertEqual(frame.source, 'pygame_desktop')
        self.assertEqual(frame.consumed_event_count, 2)
        self.assertNotIn('png_bytes', frame.metadata())
        with self.assertRaisesRegex(ScreenFrameError, 'out-of-order'):
            receiver.receive()
        self.publish(2, timestamp=100.2, event_count=4, source='pillow_desktop')
        self.assertEqual(receiver.receive(now_s=100.3, minimum_event_count=4).frame_id, 2)

    def test_stale_future_and_regressed_event_frames_rejected(self):
        receiver = FrameReceiver(self.directory, clock=lambda: 100.1, max_age_s=1)
        for timestamp in (98.0, 101.0, float('nan')):
            self.publish(timestamp=100.0)
            self.mutate(captured_at_s=timestamp)
            with self.subTest(timestamp=timestamp), self.assertRaises(ScreenFrameError):
                receiver.receive()
        self.publish()
        with self.assertRaisesRegex(ScreenFrameError, 'keyboard events'):
            receiver.receive(minimum_event_count=3)
        receiver.receive(minimum_event_count=2)
        self.publish(2, timestamp=100.05, event_count=1)
        with self.assertRaisesRegex(ScreenFrameError, 'keyboard events'):
            receiver.receive()

    def test_version_dimensions_and_oracle_provenance_rejected(self):
        changes = [dict(schema_version=True), dict(schema_version=2), dict(pixel_size=[True, 2]),
                   dict(pixel_size=[40000, 40000]), dict(pixel_size=[3, 2]),
                   dict(provenance={'capture': 'rendered_rgb_pixels', 'clock': 'local_monotonic', 'text_oracle': True}),
                   dict(source='privileged_editor_buffer')]
        for change in changes:
            self.publish()
            self.mutate(**change)
            with self.subTest(change=change), self.assertRaises(ScreenFrameError):
                FrameReceiver(self.directory, clock=lambda: 100.1).receive()

    def test_path_traversal_and_symlink_rejected(self):
        self.publish()
        for filename in ('../frame.png', '/tmp/frame.png', 'nested/frame.png', '..', 'nested\\frame.png'):
            self.mutate(file=filename)
            with self.subTest(filename=filename), self.assertRaises(ScreenFrameError):
                FrameReceiver(self.directory, clock=lambda: 100.1).receive()
        original = self.directory / 'frame-00000001.png'
        (self.directory / 'linked.png').symlink_to(original)
        self.mutate(file='linked.png')
        with self.assertRaises(ScreenFrameError):
            FrameReceiver(self.directory, clock=lambda: 100.1).receive()
        manifest = (self.directory / 'frame.json').read_bytes()
        (self.directory / 'manifest-real.json').write_bytes(manifest)
        (self.directory / 'frame.json').unlink()
        (self.directory / 'frame.json').symlink_to(self.directory / 'manifest-real.json')
        with self.assertRaises(ScreenFrameError):
            FrameReceiver(self.directory, clock=lambda: 100.1).receive()

    def test_corruption_is_not_accepted_even_after_png_hash_is_changed(self):
        self.publish()
        png = self.directory / 'frame-00000001.png'
        damaged = bytearray(png.read_bytes())
        damaged[40] ^= 1
        png.write_bytes(damaged)
        receiver = FrameReceiver(self.directory, clock=lambda: 100.1)
        with self.assertRaisesRegex(ScreenFrameError, 'PNG digest'):
            receiver.receive()
        self.mutate(sha256=hashlib.sha256(damaged).hexdigest())
        with self.assertRaises(ScreenFrameError):
            receiver.receive()
        png.unlink()
        self.publish()
        self.mutate(pixel_sha256='0' * 64)
        with self.assertRaisesRegex(ScreenFrameError, 'RGB digest'):
            receiver.receive()

    def test_camera_requires_new_render_and_keeps_texture_submission_unverified(self):
        self.publish()
        desktop = FrameReceiver(self.directory, clock=lambda: 100.1).receive()
        gate = CameraFrameGate()
        with self.assertRaises(ScreenFrameError):
            gate.validate('robot', rendering_frame=3, rendering_time_s=1)
        gate.texture_applied(desktop, rendering_frames={'robot': 3, 'overview': 8}, simulation_time_s=1)
        for frame, timestamp in ((3, 1.1), (4, .9)):
            with self.subTest(frame=frame), self.assertRaises(ScreenFrameError):
                gate.validate('robot', rendering_frame=frame, rendering_time_s=timestamp)
        evidence = gate.validate('robot', rendering_frame=4, rendering_time_s=1.1)
        self.assertFalse(evidence['display_content_verified'])
        self.assertFalse(evidence['screen_text_from_privileged_state'])
        self.assertEqual(evidence['texture_consumed_event_count'], 2)
        with self.assertRaises(ScreenFrameError):
            gate.validate('robot', rendering_frame=4, rendering_time_s=1.1)
        self.assertEqual(gate.validate('overview', rendering_frame=9, rendering_time_s=1.1)['texture_submission_frame_id'], 1)
        with self.assertRaises(ScreenFrameError):
            gate.texture_applied(desktop, rendering_frames={'robot': 4}, simulation_time_s=1.2)

    def test_import_does_not_load_optional_simulation_or_image_modules(self):
        result = subprocess.run([sys.executable, '-c',
            'import sys; import vskill_os.adapters.isaac.monitor_screen; '
            'assert not any(name in sys.modules for name in ("isaacsim", "pxr", "numpy", "PIL"))'],
            capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_isaac6_fabric_reference_dictionary_is_compared_as_a_rational(self):
        self.publish()
        desktop = FrameReceiver(self.directory, clock=lambda: 100.1).receive()
        gate = CameraFrameGate()
        gate.texture_applied(desktop, rendering_frames={'robot': {'referenceTimeNumerator': 100, 'referenceTimeDenominator': 100}}, simulation_time_s=1)
        # Larger numerator alone is not a newer rendered reference time.
        with self.assertRaises(ScreenFrameError):
            gate.validate('robot', rendering_frame={'referenceTimeNumerator': 200, 'referenceTimeDenominator': 400}, rendering_time_s=1.1)
        gate.validate('robot', rendering_frame={'referenceTimeNumerator': 101, 'referenceTimeDenominator': 100}, rendering_time_s=1.01)
        for value in ({}, {'referenceTimeNumerator': 1, 'referenceTimeDenominator': 0}, True):
            with self.subTest(value=value), self.assertRaises(ScreenFrameError):
                render_marker(value)

    def test_desktop_or_unmounted_camera_cannot_be_robot_vision(self):
        for metadata in ({'source': 'pygame_desktop'},
                         {'source': 'isaac_camera', 'evidence_type': 'rendered_camera_rgb',
                          'camera_mount_prim_path': None, 'screen_text_from_privileged_state': False}):
            with self.subTest(metadata=metadata), self.assertRaises(ScreenFrameError):
                verify_robot_camera_text(metadata, expected_text='afjk', focus_region=(0, 0, 2, 1), text_region=(0, 1, 2, 2))


if __name__ == '__main__':
    unittest.main()
