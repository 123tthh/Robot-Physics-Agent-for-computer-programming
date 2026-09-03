from __future__ import annotations

import unittest

from vskill_os.demo import run_demo


class DemoTests(unittest.IsolatedAsyncioTestCase):
    async def test_test_failure_is_repaired_and_retried(self) -> None:
        summary = await run_demo()
        self.assertTrue(summary["successful"])
        self.assertEqual(summary["executed_skills"].count("run_test"), 2)
        self.assertIn("request_fix", summary["recoveries"])
        self.assertIn("test_passed", summary["final_facts"])
        self.assertIn("task_reported", summary["final_facts"])

    async def test_physical_failure_uses_recovery_skill(self) -> None:
        summary = await run_demo(scenario_name="mixed_faults")
        self.assertTrue(summary["successful"])
        self.assertIn("clear_navigation_path", summary["recoveries"])
        self.assertIn("realign_with_chair", summary["recoveries"])
        self.assertIn("recalibrate_keyboard", summary["recoveries"])
        self.assertIn("restore_code_agent_session", summary["recoveries"])


if __name__ == "__main__":
    unittest.main()
