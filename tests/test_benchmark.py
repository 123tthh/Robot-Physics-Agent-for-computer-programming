from __future__ import annotations

import unittest

from vskill_os.benchmark import BenchmarkConfig, run_benchmark


class BenchmarkTests(unittest.IsolatedAsyncioTestCase):
    async def test_nominal_benchmark_is_fully_successful(self) -> None:
        report = await run_benchmark(
            BenchmarkConfig(episodes=3, seed=11, profile="nominal")
        )
        summary = report["summary"]
        self.assertEqual(summary["success_rate"], 1.0)
        self.assertEqual(summary["false_completion_count"], 0)
        self.assertEqual(summary["mean_recoveries"], 0.0)

    async def test_recovery_benchmark_is_reproducible(self) -> None:
        config = BenchmarkConfig(episodes=3, seed=19, profile="recovery")
        first = await run_benchmark(config)
        second = await run_benchmark(config)
        first_episodes = [
            (item["status"], item["injected_faults"], item["recoveries"])
            for item in first["episodes"]
        ]
        second_episodes = [
            (item["status"], item["injected_faults"], item["recoveries"])
            for item in second["episodes"]
        ]
        self.assertEqual(first_episodes, second_episodes)


if __name__ == "__main__":
    unittest.main()
