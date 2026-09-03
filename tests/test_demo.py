from __future__ import annotations

import unittest

from vskill_os.demo import run_demo


class DemoTests(unittest.IsolatedAsyncioTestCase):
    async def test_test_failure_is_repaired_and_retried(self) -> None:
        summary = await run_demo()
        self.assertTrue(summary["successful"])
        self.assertEqual(
            summary["executed_skills"],
            [
                "press_chord",
                "launch_code_agent",
                "submit_task",
                "run_test",
                "request_fix",
                "run_test",
            ],
        )
        self.assertIn("test_passed", summary["final_facts"])
        self.assertEqual(summary["event_count"], 12)

    async def test_physical_failure_uses_recovery_skill(self) -> None:
        summary = await run_demo(simulate_missed_alt=True)
        self.assertTrue(summary["successful"])
        self.assertEqual(summary["executed_skills"][:3], [
            "press_chord",
            "recalibrate_keyboard",
            "press_chord",
        ])


if __name__ == "__main__":
    unittest.main()
