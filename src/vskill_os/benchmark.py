"""Reproducible fault-injection benchmark for V-Skill OS."""

from __future__ import annotations

import asyncio
import math
import random
import statistics
import tempfile
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .domain import SkillStatus
from .simulation.app import build_simulation_app, project_root
from .simulation.environment import SimulationScenario


@dataclass(frozen=True, slots=True)
class BenchmarkConfig:
    episodes: int = 20
    seed: int = 7
    profile: str = "recovery"

    def __post_init__(self) -> None:
        if self.episodes <= 0:
            raise ValueError("benchmark episodes must be positive")
        if self.profile not in {"nominal", "recovery", "stress"}:
            raise ValueError("profile must be nominal, recovery, or stress")


def sample_scenario(rng: random.Random, profile: str, episode: int) -> SimulationScenario:
    if profile == "nominal":
        return SimulationScenario(
            name=f"nominal-{episode}",
            buggy_first_draft=False,
            faults={},
        )

    probability = 0.28 if profile == "recovery" else 0.65
    faults: dict[str, tuple[str, ...]] = {}
    candidates = {
        "navigate_to_workstation": "NAVIGATION_BLOCKED",
        "align_with_chair": "CHAIR_NOT_FOUND",
        "sit_and_prepare_for_typing": rng.choice(("CHAIR_SHIFTED", "POSTURE_UNSTABLE")),
        "reposition_while_seated": rng.choice(
            ("SEAT_CONTACT_LOST", "SEATED_POSTURE_UNSTABLE", "SEATED_TRACKING_FAILED")
        ),
        "calibrate_keyboard": "KEYBOARD_NOT_FOUND",
        "press_chord": rng.choice(("ALT_MISSED", "TERMINAL_NOT_FOCUSED")),
        "launch_code_agent": "NETWORK_INTERRUPTED",
    }
    for skill, code in candidates.items():
        if rng.random() < probability:
            faults[skill] = (code,)

    if profile == "stress" and rng.random() < 0.2:
        faults["press_chord"] = ("WRONG_KEY",)

    return SimulationScenario(
        name=f"{profile}-{episode}",
        buggy_first_draft=rng.random() < probability,
        faults=faults,
        max_recovery_attempts=2,
    )


async def run_benchmark(config: BenchmarkConfig) -> dict[str, object]:
    rng = random.Random(config.seed)
    episodes: list[dict[str, object]] = []
    durations: list[float] = []
    call_counts: list[int] = []
    recovery_counts: list[int] = []
    seated_path_lengths: list[float] = []
    seated_tracking_rmse: list[float] = []
    seated_contact_confidences: list[float] = []
    failure_codes: Counter[str] = Counter()
    success_count = 0
    false_completion_count = 0
    workflow = project_root() / "configs/workflows/create_hello.json"

    for episode in range(config.episodes):
        scenario = sample_scenario(rng, config.profile, episode)
        with tempfile.TemporaryDirectory(prefix="vskill-bench-") as directory:
            scenario_path = Path(directory) / "scenario.json"
            scenario_path.write_text(
                _scenario_json(scenario),
                encoding="utf-8",
            )
            app = build_simulation_app(
                scenario_path=scenario_path,
                workflow_path=workflow,
                workspace=Path(directory) / "workspace",
            )
            started = time.perf_counter()
            try:
                result = await app.run(task_id=f"benchmark-{config.seed}-{episode}")
            finally:
                elapsed = time.perf_counter() - started
                injected_count = app.world.faults.injected_count
                app.close()

        success = result.status is SkillStatus.SUCCEEDED
        success_count += int(success)
        calls = len(result.skill_results)
        recoveries = sum(
            1
            for item in result.skill_results
            if item.skill
            in {
                "clear_navigation_path",
                "realign_with_chair",
                "stabilize_work_pose",
                "recover_seated_contact",
                "recalibrate_keyboard",
                "restore_terminal_focus",
                "restore_code_agent_session",
                "request_fix",
            }
        )
        for item in result.skill_results:
            if item.failure_code:
                failure_codes[item.failure_code] += 1
        false_completion = (
            "task_reported" in result.final_state.facts
            and "test_passed" not in result.final_state.facts
        )
        false_completion_count += int(false_completion)
        seated_metrics = None
        for item in result.skill_results:
            if "seated_reposition_verified" not in item.achieved:
                continue
            seated_metrics = next(
                (
                    evidence.value
                    for evidence in item.evidence
                    if evidence.kind == "seated_locomotion_metrics"
                    and isinstance(evidence.value, dict)
                ),
                None,
            )
        if seated_metrics is not None:
            tracking = seated_metrics["tracking_rmse"]
            seated_path_lengths.append(float(seated_metrics["path_length_m"]))
            seated_tracking_rmse.append(
                math.hypot(float(tracking["vx_mps"]), float(tracking["vy_mps"]))
            )
            seated_contact_confidences.append(
                float(seated_metrics["minimum_seat_contact_confidence"])
            )
        durations.append(elapsed)
        call_counts.append(calls)
        recovery_counts.append(recoveries)
        episodes.append(
            {
                "episode": episode,
                "scenario": scenario.name,
                "status": result.status.value,
                "injected_faults": injected_count,
                "skill_calls": calls,
                "recoveries": recoveries,
                "failure_node": result.failure_node,
                "false_completion": false_completion,
                "seated_reposition_verified": (
                    "seated_reposition_verified" in result.final_state.facts
                ),
            }
        )

    return {
        "config": {
            "episodes": config.episodes,
            "seed": config.seed,
            "profile": config.profile,
        },
        "summary": {
            "success_count": success_count,
            "success_rate": success_count / config.episodes,
            "false_completion_count": false_completion_count,
            "seated_reposition_verified_count": len(seated_path_lengths),
            "mean_seated_path_length_m": (
                statistics.fmean(seated_path_lengths) if seated_path_lengths else None
            ),
            "mean_seated_planar_tracking_rmse_mps": (
                statistics.fmean(seated_tracking_rmse)
                if seated_tracking_rmse
                else None
            ),
            "mean_minimum_seat_contact_confidence": (
                statistics.fmean(seated_contact_confidences)
                if seated_contact_confidences
                else None
            ),
            "mean_skill_calls": statistics.fmean(call_counts),
            "mean_recoveries": statistics.fmean(recovery_counts),
            "mean_duration_seconds": statistics.fmean(durations),
            "failure_codes": dict(sorted(failure_codes.items())),
        },
        "episodes": episodes,
    }


def _scenario_json(scenario: SimulationScenario) -> str:
    import json

    return json.dumps(
        {
            "name": scenario.name,
            "expected_output": scenario.expected_output,
            "buggy_first_draft": scenario.buggy_first_draft,
            "max_recovery_attempts": scenario.max_recovery_attempts,
            "faults": {key: list(value) for key, value in scenario.faults.items()},
        },
        ensure_ascii=False,
        indent=2,
    )


def main() -> None:
    import json

    report = asyncio.run(run_benchmark(BenchmarkConfig()))
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
