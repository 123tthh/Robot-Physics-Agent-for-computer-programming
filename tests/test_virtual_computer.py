"""Real PTY and independent test process integration, optional pixels."""
import importlib.util
import sys
import tempfile
import time
import unittest
from pathlib import Path

from vskill_os.adapters.virtual_computer import IndependentTestExecutor, VirtualComputer
from vskill_os.adapters.local_code_agent import LocalTestRunnerAdapter
from vskill_os.simulation.keyboard_bridge import KeyEvent, KeyEventType


class ExecutorTests(unittest.TestCase):
    def test_stdout_claim_cannot_override_failed_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            result = IndependentTestExecutor().run([sys.executable, '-c', 'print("all passed"); raise SystemExit(3)'], Path(directory))
        self.assertFalse(result['passed'])
        self.assertEqual(result['exit_code'], 3)

    def test_timeout_is_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            result = IndependentTestExecutor().run([sys.executable, '-c', 'import time; time.sleep(5)'], Path(directory), timeout_s=.05)
        self.assertEqual(result['failure'], 'TEST_TIMEOUT')


class TestRunnerProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_runner_produces_exit_evidence_independently(self):
        with tempfile.TemporaryDirectory() as directory:
            result = await LocalTestRunnerAdapter(Path(directory)).run(
                {'argv': [sys.executable, '-c', 'raise SystemExit(2)']})
        self.assertEqual(result[0].kind, 'process_exit_code')
        self.assertEqual(result[0].value, 2)
        self.assertEqual(result[0].source, 'independent_process_exit')


@unittest.skipUnless(importlib.util.find_spec('pygame') and importlib.util.find_spec('pyte'), 'optional virtual-computer extra')
class DesktopPixelsTests(unittest.TestCase):
    def test_key_events_produce_real_shell_output_and_changed_pixels(self):
        with tempfile.TemporaryDirectory() as directory:
            computer = VirtualComputer(Path(directory))
            try:
                marker = 'PTY_TEST_OK'
                (computer.desktop.workspace / marker).touch()
                initial, _ = computer.snapshot(0.)
                # Deliberately labelled software test events, never physical evidence.
                for key in ('l', 's', 'enter'):
                    for kind in (KeyEventType.KEYDOWN, KeyEventType.KEYUP):
                        computer.handle_event(KeyEvent(kind, key, time.monotonic(), 'unit_test_injection'))
                deadline = time.monotonic() + 3.
                while time.monotonic() < deadline:
                    pixels, _ = computer.snapshot(1.)
                    if marker in computer.renderer.previous_text:
                        break
                    time.sleep(.02)
                self.assertIn(marker, computer.renderer.previous_text)
                self.assertNotEqual(initial.tobytes(), pixels.tobytes())
                self.assertEqual(len(computer.events), 6)
                self.assertIn(marker, '\n'.join(computer.renderer.terminal.display))
            finally:
                result = computer.close()
            self.assertFalse(result['camera_ocr_verified'])


if __name__ == '__main__':
    unittest.main()
