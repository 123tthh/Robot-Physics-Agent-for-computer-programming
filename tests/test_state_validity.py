from __future__ import annotations

import unittest

from vskill_os.contracts import SkillContract
from vskill_os.domain import (
    Evidence,
    EvidenceDomain,
    SkillExecution,
    SkillRequest,
    SkillStatus,
    StateSnapshot,
)
from vskill_os.ledger import EventLedger
from vskill_os.recovery import build_default_recovery_manager
from vskill_os.registry import SkillRegistry
from vskill_os.runtime import SkillRuntime, StateValidityPolicy
from vskill_os.verifier import build_default_verifier


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


class _Calibrate:
    contract = SkillContract(
        name="calibrate",
        version="1.0",
        preconditions=("work_pose_ready",),
        local_postconditions=("keyboard_calibrated",),
    )

    async def execute(self, state, arguments):
        del state, arguments
        return SkillExecution(
            status=SkillStatus.SUCCEEDED,
            claimed_states=("keyboard_calibrated",),
            evidence=(
                Evidence(
                    EvidenceDomain.PHYSICAL,
                    "state_observation",
                    {"state": "keyboard_calibrated", "present": True},
                    "sim_keyboard",
                ),
            ),
        )


class _UseKeyboard:
    contract = SkillContract(
        name="use_keyboard",
        version="1.0",
        preconditions=("keyboard_calibrated",),
    )

    async def execute(self, state, arguments):
        del state, arguments
        return SkillExecution(status=SkillStatus.SUCCEEDED)


class StateValidityTests(unittest.IsolatedAsyncioTestCase):
    def make_runtime(self, clock: _Clock):
        registry = SkillRegistry()
        registry.register(_Calibrate())
        registry.register(_UseKeyboard())
        ledger = EventLedger()
        runtime = SkillRuntime(
            registry=registry,
            verifier=build_default_verifier(),
            ledger=ledger,
            recovery=build_default_recovery_manager(),
            clock=clock,
            state_validity={
                "keyboard_calibrated": StateValidityPolicy(
                    ttl_seconds=5.0,
                    dependencies=("work_pose_ready",),
                )
            },
        )
        return runtime, ledger

    async def test_expired_precondition_blocks_controller(self) -> None:
        clock = _Clock()
        runtime, ledger = self.make_runtime(clock)
        state = StateSnapshot(facts={"work_pose_ready"})
        calibrated = await runtime.execute(
            SkillRequest(task_id="ttl", skill="calibrate"), state
        )
        clock.now += 5.0
        rejected = await runtime.execute(
            SkillRequest(task_id="ttl", skill="use_keyboard"), state
        )
        events = ledger.list_events("ttl")
        ledger.close()

        self.assertEqual(calibrated.status, SkillStatus.SUCCEEDED)
        self.assertEqual(rejected.status, SkillStatus.REJECTED)
        self.assertNotIn("keyboard_calibrated", state.facts)
        self.assertTrue(any(event.kind == "state_invalidated" for event in events))

    async def test_dependency_invalidation_propagates(self) -> None:
        clock = _Clock()
        runtime, ledger = self.make_runtime(clock)
        state = StateSnapshot(facts={"work_pose_ready"})
        await runtime.execute(SkillRequest(task_id="deps", skill="calibrate"), state)

        removed = state.invalidate({"work_pose_ready"})
        ledger.close()

        self.assertEqual(removed, ("keyboard_calibrated", "work_pose_ready"))
        self.assertFalse(state.facts)

    def test_validity_metadata_round_trips_through_checkpoint_shape(self) -> None:
        state = StateSnapshot(facts={"pose"})
        state.mark_verified(
            "calibration",
            verified_at=7.5,
            ttl_seconds=2.0,
            dependencies=("pose",),
        )
        restored = StateSnapshot.from_dict(state.to_dict())

        self.assertEqual(restored.to_dict(), state.to_dict())

    def test_already_missing_dependency_still_invalidates_dependent_fact(self) -> None:
        state = StateSnapshot(facts={"terminal_ready"})
        state.mark_verified(
            "terminal_ready",
            verified_at=1.0,
            dependencies=("terminal_focused",),
        )

        removed = state.invalidate({"terminal_focused"})

        self.assertEqual(removed, ("terminal_ready",))


if __name__ == "__main__":
    unittest.main()
