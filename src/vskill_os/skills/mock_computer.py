"""Simulation-only skills used to validate the framework without robot hardware."""

from __future__ import annotations

from typing import Any, Mapping

from ..contracts import SkillContract
from ..domain import Evidence, EvidenceDomain, SkillExecution, SkillStatus, StateSnapshot
from ..registry import SkillRegistry


class PressChordSkill:
    contract = SkillContract(
        name="press_chord",
        version="0.1.0",
        preconditions=(
            "work_pose_stable",
            "keyboard_calibrated",
            "target_keys_reachable",
            "screen_visible",
        ),
        local_postconditions=("commanded_keys_pressed",),
        next_skill_readiness=("terminal_ready",),
        resources=("left_arm", "right_arm", "keyboard_camera"),
        failure_codes=("ALT_MISSED", "WRONG_KEY", "POSTURE_UNSTABLE"),
        recoveries=("recalibrate_keyboard", "restore_terminal_focus"),
    )

    async def execute(
        self,
        state: StateSnapshot,
        arguments: Mapping[str, Any],
    ) -> SkillExecution:
        keys = tuple(arguments.get("keys", ()))
        if keys != ("ctrl", "alt", "t"):
            return SkillExecution(
                status=SkillStatus.FAILED,
                failure_code="WRONG_KEY",
                message="demo skill accepts only ctrl+alt+t",
            )

        if state.values.pop("simulate_missed_alt", False):
            return SkillExecution(
                status=SkillStatus.FAILED,
                failure_code="ALT_MISSED",
                evidence=(
                    Evidence(
                        domain=EvidenceDomain.PHYSICAL,
                        kind="key_event",
                        value=["ctrl", "t"],
                        source="mock_keyboard",
                        confidence=1.0,
                    ),
                ),
                message="alt key was not triggered",
            )

        state.facts.update(
            {
                "commanded_keys_pressed",
                "terminal_visible",
                "terminal_focused",
                "fingers_released",
            }
        )
        return SkillExecution(
            status=SkillStatus.SUCCEEDED,
            claimed_states=("commanded_keys_pressed", "terminal_ready"),
            evidence=(
                Evidence(
                    domain=EvidenceDomain.PHYSICAL,
                    kind="key_event",
                    value=list(keys),
                    source="mock_keyboard",
                ),
                Evidence(
                    domain=EvidenceDomain.GUI,
                    kind="terminal_visible",
                    value=True,
                    source="mock_screen_verifier",
                    confidence=0.99,
                ),
            ),
            message="terminal chord executed",
        )


class RecalibrateKeyboardSkill:
    contract = SkillContract(
        name="recalibrate_keyboard",
        version="0.1.0",
        preconditions=("screen_visible",),
        local_postconditions=("keyboard_calibrated",),
        resources=("keyboard_camera",),
    )

    async def execute(
        self,
        state: StateSnapshot,
        arguments: Mapping[str, Any],
    ) -> SkillExecution:
        del arguments
        state.facts.add("keyboard_calibrated")
        return SkillExecution(
            status=SkillStatus.SUCCEEDED,
            claimed_states=("keyboard_calibrated",),
            evidence=(
                Evidence(
                    domain=EvidenceDomain.PHYSICAL,
                    kind="keyboard_pose",
                    value={"translation_mm": [0, 0, 0], "yaw_deg": 0},
                    source="mock_keyboard_camera",
                    confidence=0.98,
                ),
            ),
        )


class LaunchCodeAgentSkill:
    contract = SkillContract(
        name="launch_code_agent",
        version="0.1.0",
        preconditions=("terminal_ready",),
        local_postconditions=("code_agent_ready",),
        resources=("keyboard", "screen"),
    )

    async def execute(
        self,
        state: StateSnapshot,
        arguments: Mapping[str, Any],
    ) -> SkillExecution:
        del arguments
        state.facts.add("code_agent_ready")
        return SkillExecution(
            status=SkillStatus.SUCCEEDED,
            claimed_states=("code_agent_ready",),
            evidence=(
                Evidence(
                    domain=EvidenceDomain.GUI,
                    kind="code_agent_prompt_visible",
                    value=True,
                    source="mock_screen_verifier",
                    confidence=0.99,
                ),
            ),
        )


class SubmitTaskSkill:
    contract = SkillContract(
        name="submit_task",
        version="0.1.0",
        preconditions=("code_agent_ready",),
        local_postconditions=("artifact_generated",),
        resources=("keyboard", "screen", "code_agent_sandbox"),
    )

    async def execute(
        self,
        state: StateSnapshot,
        arguments: Mapping[str, Any],
    ) -> SkillExecution:
        prompt = str(arguments.get("prompt", ""))
        if not prompt.strip():
            return SkillExecution(
                status=SkillStatus.FAILED,
                failure_code="EMPTY_TASK",
                message="a non-empty software task is required",
            )
        state.facts.add("artifact_generated")
        return SkillExecution(
            status=SkillStatus.SUCCEEDED,
            claimed_states=("artifact_generated",),
            evidence=(
                Evidence(
                    domain=EvidenceDomain.SOFTWARE,
                    kind="artifact_path",
                    value="/sandbox/hello.py",
                    source="mock_code_agent",
                ),
            ),
        )


class RunTestSkill:
    contract = SkillContract(
        name="run_test",
        version="0.1.0",
        preconditions=("artifact_generated",),
        local_postconditions=("test_passed",),
        resources=("code_agent_sandbox",),
        failure_codes=("TEST_FAILED",),
        recoveries=("request_fix",),
    )

    async def execute(
        self,
        state: StateSnapshot,
        arguments: Mapping[str, Any],
    ) -> SkillExecution:
        del arguments
        if "artifact_fixed" not in state.facts:
            return SkillExecution(
                status=SkillStatus.FAILED,
                failure_code="TEST_FAILED",
                evidence=(
                    Evidence(
                        domain=EvidenceDomain.SOFTWARE,
                        kind="process_exit_code",
                        value=1,
                        source="mock_test_runner",
                    ),
                    Evidence(
                        domain=EvidenceDomain.SOFTWARE,
                        kind="stderr",
                        value="AssertionError: expected 'hello, world'",
                        source="mock_test_runner",
                    ),
                ),
                message="independent test rejected the generated artifact",
            )

        return SkillExecution(
            status=SkillStatus.SUCCEEDED,
            claimed_states=("test_passed",),
            evidence=(
                Evidence(
                    domain=EvidenceDomain.SOFTWARE,
                    kind="process_exit_code",
                    value=0,
                    source="mock_test_runner",
                ),
            ),
            message="independent test passed",
        )


class RequestFixSkill:
    contract = SkillContract(
        name="request_fix",
        version="0.1.0",
        preconditions=("artifact_generated",),
        local_postconditions=("artifact_fixed",),
        resources=("keyboard", "screen", "code_agent_sandbox"),
    )

    async def execute(
        self,
        state: StateSnapshot,
        arguments: Mapping[str, Any],
    ) -> SkillExecution:
        del arguments
        state.facts.add("artifact_fixed")
        return SkillExecution(
            status=SkillStatus.SUCCEEDED,
            claimed_states=("artifact_fixed",),
            evidence=(
                Evidence(
                    domain=EvidenceDomain.SOFTWARE,
                    kind="patch_applied",
                    value=True,
                    source="mock_code_agent",
                ),
            ),
        )


def build_demo_registry() -> SkillRegistry:
    registry = SkillRegistry()
    for skill in (
        PressChordSkill(),
        RecalibrateKeyboardSkill(),
        LaunchCodeAgentSkill(),
        SubmitTaskSkill(),
        RunTestSkill(),
        RequestFixSkill(),
    ):
        registry.register(skill)
    return registry
