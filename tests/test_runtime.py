from __future__ import annotations

import unittest

from vskill_os.domain import SkillRequest, SkillStatus, StateSnapshot
from vskill_os.ledger import EventLedger
from vskill_os.recovery import build_default_recovery_manager
from vskill_os.runtime import SkillRuntime
from vskill_os.skills import build_demo_registry
from vskill_os.verifier import build_default_verifier


def build_runtime() -> tuple[SkillRuntime, EventLedger]:
    ledger = EventLedger()
    runtime = SkillRuntime(
        registry=build_demo_registry(),
        verifier=build_default_verifier(),
        ledger=ledger,
        recovery=build_default_recovery_manager(),
    )
    return runtime, ledger


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_rejects_skill_when_preconditions_are_missing(self) -> None:
        runtime, ledger = build_runtime()
        result = await runtime.execute(
            SkillRequest(
                task_id="t1",
                skill="press_chord",
                arguments={"keys": ["ctrl", "alt", "t"]},
            ),
            StateSnapshot(),
        )
        ledger.close()

        self.assertEqual(result.status, SkillStatus.REJECTED)
        self.assertEqual(result.failure_code, "PRECONDITION_FAILED")
        self.assertIn("keyboard_calibrated", result.missing)

    async def test_verifies_terminal_readiness(self) -> None:
        runtime, ledger = build_runtime()
        state = StateSnapshot(
            facts={
                "work_pose_stable",
                "keyboard_calibrated",
                "target_keys_reachable",
                "screen_visible",
            }
        )
        result = await runtime.execute(
            SkillRequest(
                task_id="t2",
                skill="press_chord",
                arguments={"keys": ["ctrl", "alt", "t"]},
                required_postconditions=("terminal_ready",),
            ),
            state,
        )
        ledger.close()

        self.assertEqual(result.status, SkillStatus.SUCCEEDED)
        self.assertIn("terminal_ready", state.facts)
        self.assertIn("terminal_ready", result.achieved)


if __name__ == "__main__":
    unittest.main()
