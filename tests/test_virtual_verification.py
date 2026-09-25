"""Fail-closed gates: PTY text alone must never certify robot vision."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from verify_virtual_computer import verify


@unittest.skipUnless(importlib.util.find_spec('numpy') and importlib.util.find_spec('PIL'), 'optional pixel dependencies')
class VirtualVerificationTests(unittest.TestCase):
    def setUp(self):
        import numpy as np
        from PIL import Image
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        rgb = self.root / 'rgb.png'
        Image.new('RGBA', (8, 8), (10, 20, 30, 255)).save(rgb)
        depth = self.root / 'depth.npy'
        np.save(depth, np.ones((8, 8), dtype='float32'))
        digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
        frames = [{'label': label, 'rgb': str(rgb), 'rgb_sha256': digest(rgb),
                   'depth': str(depth), 'depth_sha256': digest(depth), 'simulation_time_s': t}
                  for label, t in [('04-start-hands', 18.), ('03-final', 40.)]]
        events = [{'key': key, 'event_type': kind, 'source': 'keyboard_event_bridge',
                   'reason': 'physical_threshold' if kind == 'keydown' else 'release_threshold', 'monotonic_time': 30. + i}
                  for i, key in enumerate(['l', 's', 'enter']) for kind in ['keydown', 'keyup']]
        self.record = {'typing': {'plan': {'text': 'ls\n'}, 'events': events},
            'virtual_computer': {'events': events, 'camera_frames': frames,
                'camera_mount': {'pose_source': 'measured_physx_link_pose_times_constant_mount'},
                'frames': [{'text': 'ls\nKEYBOARD_OK\n', 'png_path': str(rgb), 'png_sha256': digest(rgb),
                    'rgba_sha256': hashlib.sha256(Image.open(rgb).convert('RGBA').tobytes()).hexdigest()}]}}

    def evaluate(self, before='', after='KEYBOARD_OK'):
        path = self.root / 'report.json'
        path.write_text(json.dumps(self.record))
        with patch('verify_virtual_computer.verify_physics', return_value={'succeeded': True}), \
             patch('verify_virtual_computer.ocr', side_effect=[before, after]):
            return verify(path)

    def test_pty_success_cannot_replace_camera_reading(self):
        result = self.evaluate(after='')
        self.assertTrue(result['gates']['pty_contains_real_output'])
        self.assertFalse(result['succeeded'])
        self.assertFalse(result['camera_ocr_verified'])

    def test_preexisting_marker_rejected(self):
        self.assertFalse(self.evaluate(before='KEYBOARD_OK')['succeeded'])

    def test_modified_camera_evidence_rejected(self):
        self.record['virtual_computer']['camera_frames'][-1]['rgb_sha256'] = 'changed'
        self.assertFalse(self.evaluate()['succeeded'])

    def test_software_injected_events_rejected(self):
        self.record['virtual_computer']['events'][0]['source'] = 'unit_test_injection'
        self.assertFalse(self.evaluate()['succeeded'])

    def test_valid_record_consistency(self):
        self.assertTrue(self.evaluate()['succeeded'])


if __name__ == '__main__':
    unittest.main()
