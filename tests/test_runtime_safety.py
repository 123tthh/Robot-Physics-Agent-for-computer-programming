from __future__ import annotations

import asyncio
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
from vskill_os.runtime import SkillRuntime
from vskill_os.verifier import build_default_verifier


class UnsupportedClaimSkill:
    contract = SkillContract(
        name="unsupported_claim",
        version="0.1.0",
        local_postconditions=("test_passed",),
    )

    async def execute(self, state, arguments):
        del state, arguments
        return SkillExecution(
            status=SkillStatus.SUCCEEDED,
            claimed_states=("test_passed",),
            evidence=(),
        )


class SlowSkill:
    contract = SkillContract(
        name="slow_skill",
        version="0.1.0",
        local_postconditions=("slow_finished",),
    )

    async def execute(self, state, arguments):
        del state, arguments
        await asyncio.sleep(0.05)
        return SkillExecution(
            status=SkillStatus.SUCCEEDED,
            claimed_states=("slow_finished",),
        )


class UnsupportedDerivedStateSkill:
    contract = SkillContract(
        name="unsupported_terminal_claim",
        version="0.1.0",
        local_postconditions=("terminal_ready",),
    )

    async def execute(self, state, arguments):
        del state, arguments
        return SkillExecution(
            status=SkillStatus.SUCCEEDED,
            claimed_states=(
                "terminal_visible",
                "terminal_focused",
                "fingers_released",
                "work_pose_ready",
                "terminal_ready",
            ),
            evidence=(),
        )


class StationarySeatedClaimSkill:
    contract = SkillContract(
        name="stationary_seated_claim",
        version="0.1.0",
        local_postconditions=("seated_reposition_verified",),
    )

    async def execute(self, state, arguments):
        del state, arguments
        return SkillExecution(
            status=SkillStatus.SUCCEEDED,
            claimed_states=("seated_reposition_verified",),
            evidence=(
                Evidence(
                    domain=EvidenceDomain.PHYSICAL,
                    kind="seated_locomotion_metrics",
                    value={
                        "issued_command": {
                            "vx_mps": 0.0,
                            "vy_mps": 0.18,
                            "yaw_rate_rps": 0.0,
                        },
                        "duration_s": 1.0,
                        "path_length_m": 0.0,
                        "absolute_yaw_rad": 0.0,
                        "tracking_rmse": {
                            "vx_mps": 0.0,
                            "vy_mps": 0.18,
                            "yaw_rate_rps": 0.0,
                        },
                        "pelvis_seat_rms_m": 0.01,
                        "minimum_seat_contact_confidence": 0.99,
                        "maximum_contact_loss_s": 0.0,
                        "maximum_torso_tilt_rad": 0.04,
                        "maximum_chair_tilt_rad": 0.04,
                        "left_foot_contact_fraction": 0.5,
                        "right_foot_contact_fraction": 0.5,
                        "settled": True,
                    },
                    source="sim_robot",
                ),
            ),
        )


class RuntimeSafetyTests(unittest.IsolatedAsyncioTestCase):
    def make_runtime(self, skill):
        registry = SkillRegistry()
        registry.register(skill)
        ledger = EventLedger()
        runtime = SkillRuntime(
            registry=registry,
            verifier=build_default_verifier(),
            ledger=ledger,
            recovery=build_default_recovery_manager(),
        )
        return runtime, ledger

    async def test_controller_claim_does_not_bypass_verifier(self) -> None:
        runtime, ledger = self.make_runtime(UnsupportedClaimSkill())
        result = await runtime.execute(
            SkillRequest(task_id="safety", skill="unsupported_claim"),
            StateSnapshot(),
        )
        ledger.close()

        self.assertEqual(result.status, SkillStatus.FAILED)
        self.assertEqual(result.failure_code, "POSTCONDITION_FAILED")
        self.assertIn("test_passed", result.missing)

    async def test_timeout_is_converted_to_structured_failure(self) -> None:
        runtime, ledger = self.make_runtime(SlowSkill())
        result = await runtime.execute(
            SkillRequest(
                task_id="timeout",
                skill="slow_skill",
                timeout_seconds=0.001,
            ),
            StateSnapshot(),
        )
        ledger.close()

        self.assertEqual(result.status, SkillStatus.FAILED)
        self.assertEqual(result.failure_code, "SKILL_TIMEOUT")

    async def test_unverified_atomic_claims_cannot_prove_derived_state(self) -> None:
        runtime, ledger = self.make_runtime(UnsupportedDerivedStateSkill())
        result = await runtime.execute(
            SkillRequest(task_id="derived", skill="unsupported_terminal_claim"),
            StateSnapshot(),
        )
        ledger.close()

        self.assertEqual(result.status, SkillStatus.FAILED)
        self.assertIn("terminal_ready", result.missing)
        self.assertNotIn("terminal_ready", result.achieved)

    async def test_stationary_policy_cannot_claim_seated_reposition(self) -> None:
        runtime, ledger = self.make_runtime(StationarySeatedClaimSkill())
        result = await runtime.execute(
            SkillRequest(task_id="stationary", skill="stationary_seated_claim"),
            StateSnapshot(
                facts={"robot_safe", "seated_stable", "work_pose_ready"}
            ),
        )
        ledger.close()

        self.assertEqual(result.status, SkillStatus.FAILED)
        self.assertEqual(result.failure_code, "POSTCONDITION_FAILED")
        self.assertIn("seated_reposition_verified", result.missing)


if __name__ == "__main__":
    unittest.main()
