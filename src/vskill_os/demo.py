"""Runnable end-to-end simulation demonstration."""

from __future__ import annotations

import asyncio
import json

from .simulation.app import build_simulation_app, project_root


async def run_demo(*, scenario_name: str = "software_recovery") -> dict[str, object]:
    scenario_path = project_root() / "configs/scenarios" / f"{scenario_name}.json"
    app = build_simulation_app(scenario_path=scenario_path)
    try:
        result = await app.run(task_id=f"demo-{scenario_name}")
        return {
            "task_id": result.task_id,
            "scenario": scenario_name,
            "status": result.status.value,
            "successful": result.status.value == "succeeded",
            "completed_nodes": list(result.completed_nodes),
            "executed_skills": [item.skill for item in result.skill_results],
            "recoveries": [
                item.skill
                for item in result.skill_results
                if item.skill
                in {
                    "clear_navigation_path",
                    "realign_with_chair",
                    "stabilize_work_pose",
                    "recalibrate_keyboard",
                    "restore_terminal_focus",
                    "restore_code_agent_session",
                    "request_fix",
                }
            ],
            "final_facts": sorted(result.final_state.facts),
            "event_count": len(app.ledger.list_events(result.task_id)),
        }
    finally:
        app.close()


def main() -> None:
    summary = asyncio.run(run_demo())
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
