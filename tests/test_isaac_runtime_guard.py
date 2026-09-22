import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from vskill_os.adapters.isaac.runtime_guard import RuntimeCheck, RuntimeLease, is_simulator_process


class RuntimeGuardTests(unittest.TestCase):
    def test_real_kit_and_python_are_detected(self):
        self.assertTrue(is_simulator_process(['/opt/isaac/kit/kit', 'app.kit']))
        self.assertTrue(is_simulator_process(['python3', '-m', 'vskill_os.adapters.isaac.standing_scene']))
        self.assertTrue(is_simulator_process(['/opt/isaac-sim/kit/python/bin/python3', 'custom.py']))
        self.assertFalse(is_simulator_process(['bash', '-c', 'cat /opt/isaac/kit/kit']))
        self.assertFalse(is_simulator_process(['python3', '-m', 'vskill_os.adapters.isaac.runtime_guard']))
        self.assertFalse(is_simulator_process(['python3', '-c', 'print("isaac")']))

    def test_unknown_and_busy_fail_closed(self):
        self.assertFalse(RuntimeCheck('/sim', '6.0.1', True, False, (), ()).launch_allowed)
        self.assertFalse(RuntimeCheck('/sim', '6.0.1', True, True, ('kit',), ()).launch_allowed)
        self.assertFalse(RuntimeCheck('/sim', '5.1', False, True, (), ()).launch_allowed)
        self.assertTrue(RuntimeCheck('/sim', '6.0.1', True, True, (), ()).launch_allowed)

    def test_lease_releases_lock_even_after_refusal(self):
        bad = RuntimeCheck('/sim', '6.0.1', True, True, ('kit',), ('BUSY',))
        good = RuntimeCheck('/sim', '6.0.1', True, True, (), ())
        with patch('vskill_os.adapters.isaac.runtime_guard.inspect_runtime', return_value=bad):
            with self.assertRaises(RuntimeError):
                with RuntimeLease('/sim'):
                    self.fail('must not enter')
        with patch('vskill_os.adapters.isaac.runtime_guard.inspect_runtime', return_value=good):
            with RuntimeLease('/sim'):
                pass
