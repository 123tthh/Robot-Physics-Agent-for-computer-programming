"""Runnable end-to-end framework demonstration."""

from __future__ import annotations

import asyncio
import json

from .domain import SkillStatus, StateSnapshot
from .ledger import EventLedger
from .orchestrator import SupervisorAgent, WorkflowStep
from .recovery import build_default_recovery_manager
from .runtime import SkillRuntime
from .skills import build_demo_registry
from .verifier import build_default_verifier


async def run_demo(*, simulate_missed_alt: bool = False) -> dict[str, object]:
    ledger = EventLedger()
    state = StateSnapshot(
        facts={
            "work_pose_stable",
            "keyboard_calibrated",
            "target_keys_reachable",
            "screen_visible",
        },
        values={"simulate_missed_alt": simulate_missed_alt},
    )
    runtime = SkillRuntime(
        registry=build_demo_registry(),
        verifier=build_default_verifier(),
        ledger=ledger,
        recovery=build_default_recovery_manager(),
    )
    supervisor = SupervisorAgent(runtime)
    task_id = "demo-create-hello"
    result = await supervisor.run(
        task_id=task_id,
        state=state,
        plan=(
            WorkflowStep(
                "press_chord",
                {"keys": ["ctrl", "alt", "t"]},
                ("terminal_ready",),
            ),
            WorkflowStep("launch_code_agent"),
            WorkflowStep(
                "submit_task",
                {"prompt": "Create hello.py that prints 'hello, world'."},
            ),
            WorkflowStep("run_test", required_postconditions=("test_passed",)),
        ),
    )
    events = ledger.list_events(task_id)
    ledger.close()
    return {
        "task_id": task_id,
        "status": result.status.value,
        "successful": result.status is SkillStatus.SUCCEEDED,
        "executed_skills": [item.skill for item in result.steps],
        "final_facts": list(result.final_facts),
        "event_count": len(events),
    }


def main() -> None:
    summary = asyncio.run(run_demo())
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
