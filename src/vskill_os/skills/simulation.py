"""Finite-horizon skills for the physical-computer-use simulator."""

from __future__ import annotations

from typing import Any, Mapping

from ..control import ControlPhase, RootVelocityCommand
from ..contracts import SkillContract
from ..domain import Evidence, EvidenceDomain, SkillExecution, SkillStatus, StateSnapshot
from ..registry import SkillRegistry
from ..simulation.environment import SimulatedWorld
from ..typing import TypingPlanner


class SimulationSkill:
    contract: SkillContract

    def __init__(self, world: SimulatedWorld) -> None:
        self.world = world

    def fault(self) -> str | None:
        return self.world.inject_for(self.contract.name)

    def control_evidence(self) -> Evidence:
        return Evidence(
            domain=EvidenceDomain.PHYSICAL,
            kind="control_stack",
            value=self.world.control.snapshot(),
            source="sim_robot",
        )

    def success(
        self,
        states: tuple[str, ...],
        *,
        domain: EvidenceDomain,
        source: str,
        message: str = "",
        extra_evidence: tuple[Evidence, ...] = (),
    ) -> SkillExecution:
        self.world.set_states(*states)
        return SkillExecution(
            status=SkillStatus.SUCCEEDED,
            claimed_states=states,
            evidence=self.world.evidence_for(
                list(states), domain=domain, source=source
            )
            + extra_evidence,
            message=message,
        )

    @staticmethod
    def failure(
        code: str,
        message: str,
        *,
        evidence: tuple[Evidence, ...] = (),
        invalidated_states: tuple[str, ...] = (),
    ) -> SkillExecution:
        return SkillExecution(
            status=SkillStatus.FAILED,
            failure_code=code,
            evidence=evidence,
            invalidated_states=invalidated_states,
            message=message,
        )


class NavigateToWorkstation(SimulationSkill):
    contract = SkillContract(
        name="navigate_to_workstation",
        version="0.2.0",
        preconditions=("task_received", "robot_safe"),
        local_postconditions=("workstation_reached",),
        resources=("locomotion", "head_camera"),
        failure_codes=("NAVIGATION_BLOCKED",),
        recoveries=("clear_navigation_path",),
    )

    async def execute(
        self, state: StateSnapshot, arguments: Mapping[str, Any]
    ) -> SkillExecution:
        del state
        command = RootVelocityCommand(
            vx_mps=float(arguments.get("vx_mps", 0.35)),
            vy_mps=float(arguments.get("vy_mps", 0.0)),
            yaw_rate_rps=float(arguments.get("yaw_rate_rps", 0.0)),
        )
        issued = self.world.control.issue_locomotion(command)
        command_evidence = Evidence(
            domain=EvidenceDomain.PHYSICAL,
            kind="bounded_root_velocity",
            value={
                "vx_mps": issued.vx_mps,
                "vy_mps": issued.vy_mps,
                "yaw_rate_rps": issued.yaw_rate_rps,
            },
            source="sim_robot",
        )
        if self.fault() == "NAVIGATION_BLOCKED":
            self.world.control.fail("path to workstation is blocked")
            return self.failure(
                "NAVIGATION_BLOCKED",
                "path to workstation is blocked",
                evidence=(command_evidence, self.control_evidence()),
            )
        self.world.control.reach_station()
        return self.success(
            ("workstation_reached",),
            domain=EvidenceDomain.PHYSICAL,
            source="sim_robot",
            extra_evidence=(command_evidence, self.control_evidence()),
        )


class ClearNavigationPath(SimulationSkill):
    contract = SkillContract(
        name="clear_navigation_path",
        version="0.2.0",
        preconditions=("task_received",),
        local_postconditions=("navigation_path_clear",),
        resources=("head_camera",),
    )

    async def execute(
        self, state: StateSnapshot, arguments: Mapping[str, Any]
    ) -> SkillExecution:
        del state, arguments
        self.world.control.recover(
            ControlPhase.LOCOMOTION, "navigation obstruction cleared"
        )
        return self.success(
            ("navigation_path_clear",),
            domain=EvidenceDomain.PHYSICAL,
            source="sim_robot",
            message="simulated obstacle cleared",
            extra_evidence=(self.control_evidence(),),
        )


class AlignWithChair(SimulationSkill):
    contract = SkillContract(
        name="align_with_chair",
        version="0.2.0",
        preconditions=("workstation_reached",),
        local_postconditions=("chair_aligned",),
        resources=("locomotion", "head_camera"),
        failure_codes=("CHAIR_NOT_FOUND",),
        recoveries=("realign_with_chair",),
    )

    async def execute(
        self, state: StateSnapshot, arguments: Mapping[str, Any]
    ) -> SkillExecution:
        del state, arguments
        self.world.control.begin_alignment()
        if self.fault() == "CHAIR_NOT_FOUND":
            self.world.control.fail("chair pose is not observable")
            return self.failure(
                "CHAIR_NOT_FOUND",
                "chair pose is not observable",
                evidence=(self.control_evidence(),),
            )
        return self.success(
            ("chair_aligned",),
            domain=EvidenceDomain.PHYSICAL,
            source="sim_robot",
            extra_evidence=(self.control_evidence(),),
        )


class RealignWithChair(SimulationSkill):
    contract = SkillContract(
        name="realign_with_chair",
        version="0.2.0",
        preconditions=("workstation_reached",),
        local_postconditions=("chair_aligned",),
        resources=("locomotion", "head_camera"),
    )

    async def execute(
        self, state: StateSnapshot, arguments: Mapping[str, Any]
    ) -> SkillExecution:
        del state, arguments
        self.world.control.recover(
            ControlPhase.STATION_ALIGNMENT, "chair pose reacquired"
        )
        self.world.control.begin_alignment()
        return self.success(
            ("chair_aligned",),
            domain=EvidenceDomain.PHYSICAL,
            source="sim_robot",
            extra_evidence=(self.control_evidence(),),
        )


class SitAndPrepareForTyping(SimulationSkill):
    contract = SkillContract(
        name="sit_and_prepare_for_typing",
        version="0.2.0",
        preconditions=("chair_aligned", "robot_safe"),
        local_postconditions=(
            "seated_stable",
            "screen_visible",
            "hands_reachable",
        ),
        next_skill_readiness=("work_pose_ready",),
        resources=("whole_body", "head_camera"),
        failure_codes=("CHAIR_SHIFTED", "POSTURE_UNSTABLE"),
        recoveries=("realign_with_chair", "stabilize_work_pose"),
    )

    async def execute(
        self, state: StateSnapshot, arguments: Mapping[str, Any]
    ) -> SkillExecution:
        del arguments
        if state.has_all(
            {"seated_stable", "screen_visible", "hands_reachable", "work_pose_ready"}
        ) and self.world.control.phase is ControlPhase.UPPER_BODY_OPERATION:
            return self.success(
                ("seated_stable", "screen_visible", "hands_reachable", "work_pose_ready"),
                domain=EvidenceDomain.PHYSICAL,
                source="sim_robot",
                message="recovery skill already restored the verified work pose",
                extra_evidence=(self.control_evidence(),),
            )
        self.world.control.begin_sit_transition()
        fault = self.fault()
        if fault == "CHAIR_SHIFTED":
            self.world.control.fail("chair moved during contact transition")
            self.world.clear_states("chair_aligned")
            return self.failure(
                "CHAIR_SHIFTED",
                "chair moved during contact transition",
                evidence=(self.control_evidence(),),
                invalidated_states=("chair_aligned",),
            )
        if fault == "POSTURE_UNSTABLE":
            self.world.control.fail("seated posture did not stabilize")
            return self.failure(
                "POSTURE_UNSTABLE",
                "seated posture did not stabilize",
                evidence=(self.control_evidence(),),
            )
        self.world.control.run_nominal_sit()
        return self.success(
            ("seated_stable", "screen_visible", "hands_reachable", "work_pose_ready"),
            domain=EvidenceDomain.PHYSICAL,
            source="sim_robot",
            extra_evidence=(self.control_evidence(),),
        )


class StabilizeWorkPose(SimulationSkill):
    contract = SkillContract(
        name="stabilize_work_pose",
        version="0.2.0",
        preconditions=("chair_aligned",),
        local_postconditions=("seated_stable", "screen_visible", "hands_reachable"),
        next_skill_readiness=("work_pose_ready",),
        resources=("whole_body", "head_camera"),
    )

    async def execute(
        self, state: StateSnapshot, arguments: Mapping[str, Any]
    ) -> SkillExecution:
        del state, arguments
        self.world.control.restore_work_pose()
        return self.success(
            ("seated_stable", "screen_visible", "hands_reachable", "work_pose_ready"),
            domain=EvidenceDomain.PHYSICAL,
            source="sim_robot",
            extra_evidence=(self.control_evidence(),),
        )


class CalibrateKeyboard(SimulationSkill):
    contract = SkillContract(
        name="calibrate_keyboard",
        version="0.2.0",
        preconditions=("work_pose_ready",),
        local_postconditions=("keyboard_calibrated", "target_keys_reachable"),
        resources=("keyboard_camera",),
        failure_codes=("KEYBOARD_NOT_FOUND",),
        recoveries=("recalibrate_keyboard",),
    )

    async def execute(
        self, state: StateSnapshot, arguments: Mapping[str, Any]
    ) -> SkillExecution:
        del state, arguments
        self.world.control.begin_keyboard_calibration()
        if self.fault() == "KEYBOARD_NOT_FOUND":
            self.world.control.fail("keyboard detector returned no pose")
            return self.failure(
                "KEYBOARD_NOT_FOUND",
                "keyboard detector returned no pose",
                evidence=(self.control_evidence(),),
            )
        pose = Evidence(
            domain=EvidenceDomain.PHYSICAL,
            kind="keyboard_pose",
            value={"translation_mm": [0.0, 0.0, 0.0], "yaw_deg": 0.0},
            source="sim_keyboard",
            confidence=0.99,
        )
        return self.success(
            ("keyboard_calibrated", "target_keys_reachable"),
            domain=EvidenceDomain.PHYSICAL,
            source="sim_keyboard",
            extra_evidence=(pose, self.control_evidence()),
        )


class RecalibrateKeyboard(CalibrateKeyboard):
    contract = SkillContract(
        name="recalibrate_keyboard",
        version="0.2.0",
        preconditions=("work_pose_ready",),
        local_postconditions=("keyboard_calibrated", "target_keys_reachable"),
        resources=("keyboard_camera",),
    )

    async def execute(
        self, state: StateSnapshot, arguments: Mapping[str, Any]
    ) -> SkillExecution:
        self.world.control.recover(
            ControlPhase.UPPER_BODY_OPERATION, "keyboard pose reacquisition requested"
        )
        return await super().execute(state, arguments)


class PressChord(SimulationSkill):
    contract = SkillContract(
        name="press_chord",
        version="0.2.0",
        preconditions=(
            "work_pose_ready",
            "keyboard_calibrated",
            "target_keys_reachable",
            "screen_visible",
        ),
        local_postconditions=("commanded_keys_pressed", "fingers_released"),
        next_skill_readiness=("terminal_ready",),
        resources=("left_arm", "right_arm", "keyboard_camera"),
        failure_codes=("ALT_MISSED", "WRONG_KEY", "TERMINAL_NOT_FOCUSED"),
        recoveries=("recalibrate_keyboard", "restore_terminal_focus"),
    )

    async def execute(
        self, state: StateSnapshot, arguments: Mapping[str, Any]
    ) -> SkillExecution:
        del state
        keys = tuple(arguments.get("keys", ()))
        if keys != ("ctrl", "alt", "t"):
            return self.failure("WRONG_KEY", f"unsupported chord: {keys!r}")

        self.world.control.begin_typing()
        planned = TypingPlanner().plan_chord(list(keys))
        plan_evidence = Evidence(
            domain=EvidenceDomain.PHYSICAL,
            kind="dex3_key_objective",
            value=[
                {
                    "key": stroke.key,
                    "modifiers": list(stroke.modifiers),
                    "hand": stroke.hand.value,
                    "finger": stroke.finger.value,
                }
                for stroke in planned
            ],
            source="sim_keyboard",
        )
        fault = self.fault()
        if fault == "ALT_MISSED":
            self.world.control.fail("left alt contact was not registered")
            return self.failure(
                "ALT_MISSED",
                "left alt contact was not registered",
                evidence=(
                    Evidence(
                        domain=EvidenceDomain.PHYSICAL,
                        kind="key_event",
                        value=["ctrl", "t"],
                        source="sim_keyboard",
                    ),
                    plan_evidence,
                    self.control_evidence(),
                ),
            )
        if fault == "WRONG_KEY":
            self.world.control.fail("an adjacent key was triggered")
            return self.failure(
                "WRONG_KEY",
                "an adjacent key was triggered",
                evidence=(plan_evidence, self.control_evidence()),
            )
        if fault == "TERMINAL_NOT_FOCUSED":
            self.world.control.fail("terminal opened without keyboard focus")
            self.world.set_states("terminal_visible", "fingers_released")
            self.world.clear_states("terminal_focused", "terminal_ready")
            return self.failure(
                "TERMINAL_NOT_FOCUSED",
                "terminal opened without keyboard focus",
                evidence=self.world.evidence_for(
                    ["terminal_visible", "fingers_released"],
                    domain=EvidenceDomain.GUI,
                    source="sim_screen",
                )
                + (plan_evidence, self.control_evidence()),
                invalidated_states=("terminal_focused", "terminal_ready"),
            )

        states = (
            "commanded_keys_pressed",
            "terminal_visible",
            "terminal_focused",
            "fingers_released",
            "terminal_ready",
        )
        key_event = Evidence(
            domain=EvidenceDomain.PHYSICAL,
            kind="key_event",
            value=list(keys),
            source="sim_keyboard",
        )
        return self.success(
            states,
            domain=EvidenceDomain.GUI,
            source="sim_screen",
            extra_evidence=(key_event, plan_evidence, self.control_evidence()),
        )


class RestoreTerminalFocus(SimulationSkill):
    contract = SkillContract(
        name="restore_terminal_focus",
        version="0.2.0",
        preconditions=("work_pose_ready",),
        local_postconditions=("terminal_visible", "terminal_focused", "fingers_released"),
        next_skill_readiness=("terminal_ready",),
        resources=("right_arm", "keyboard_camera"),
    )

    async def execute(
        self, state: StateSnapshot, arguments: Mapping[str, Any]
    ) -> SkillExecution:
        del state, arguments
        self.world.control.recover(
            ControlPhase.TYPING, "terminal focus restored without leaving typing pose"
        )
        return self.success(
            ("terminal_visible", "terminal_focused", "fingers_released", "terminal_ready"),
            domain=EvidenceDomain.GUI,
            source="sim_screen",
            extra_evidence=(self.control_evidence(),),
        )


class LaunchCodeAgent(SimulationSkill):
    contract = SkillContract(
        name="launch_code_agent",
        version="0.2.0",
        preconditions=("terminal_ready",),
        local_postconditions=("code_agent_ready",),
        resources=("keyboard", "screen", "code_agent_sandbox"),
        failure_codes=("NETWORK_INTERRUPTED",),
        recoveries=("restore_code_agent_session",),
    )

    async def execute(
        self, state: StateSnapshot, arguments: Mapping[str, Any]
    ) -> SkillExecution:
        del state, arguments
        if self.fault() == "NETWORK_INTERRUPTED":
            return self.failure("NETWORK_INTERRUPTED", "code agent session unavailable")
        return self.success(
            ("code_agent_ready",),
            domain=EvidenceDomain.GUI,
            source="sim_screen",
        )


class RestoreCodeAgentSession(SimulationSkill):
    contract = SkillContract(
        name="restore_code_agent_session",
        version="0.2.0",
        preconditions=("terminal_ready",),
        local_postconditions=("code_agent_ready",),
        resources=("keyboard", "screen", "code_agent_sandbox"),
    )

    async def execute(
        self, state: StateSnapshot, arguments: Mapping[str, Any]
    ) -> SkillExecution:
        del state, arguments
        return self.success(
            ("code_agent_ready",),
            domain=EvidenceDomain.GUI,
            source="sim_screen",
        )


class SubmitTask(SimulationSkill):
    contract = SkillContract(
        name="submit_task",
        version="0.2.0",
        preconditions=("code_agent_ready",),
        local_postconditions=("artifact_generated",),
        resources=("keyboard", "screen", "code_agent_sandbox"),
        failure_codes=("EMPTY_TASK", "NETWORK_INTERRUPTED"),
        recoveries=("restore_code_agent_session",),
    )

    async def execute(
        self, state: StateSnapshot, arguments: Mapping[str, Any]
    ) -> SkillExecution:
        del state
        prompt = str(arguments.get("prompt", ""))
        if not prompt.strip():
            return self.failure("EMPTY_TASK", "software task prompt is empty")
        if self.fault() == "NETWORK_INTERRUPTED":
            return self.failure("NETWORK_INTERRUPTED", "task submission was interrupted")

        path = self.world.sandbox.generate(
            self.world.scenario.expected_output,
            buggy=self.world.scenario.buggy_first_draft,
        )
        self.world.set_states("artifact_generated", "code_agent_claimed_complete")
        artifact = Evidence(
            domain=EvidenceDomain.SOFTWARE,
            kind="artifact_path",
            value=str(path),
            source="sim_code_agent",
        )
        return self.success(
            ("artifact_generated",),
            domain=EvidenceDomain.SOFTWARE,
            source="sim_code_agent",
            extra_evidence=(artifact,),
            message="code agent reported completion; independent test is still required",
        )


class RunTest(SimulationSkill):
    contract = SkillContract(
        name="run_test",
        version="0.2.0",
        preconditions=("artifact_generated",),
        local_postconditions=("test_passed",),
        resources=("code_agent_sandbox",),
        failure_codes=("TEST_FAILED", "TEST_TIMEOUT"),
        recoveries=("request_fix",),
    )

    async def execute(
        self, state: StateSnapshot, arguments: Mapping[str, Any]
    ) -> SkillExecution:
        del state, arguments
        fault = self.fault()
        if fault == "TEST_TIMEOUT":
            return self.failure("TEST_TIMEOUT", "test runner exceeded its deadline")

        outcome = self.world.sandbox.verify_output(self.world.scenario.expected_output)
        evidence = (
            Evidence(
                domain=EvidenceDomain.SOFTWARE,
                kind="process_exit_code",
                value=outcome.exit_code,
                source="sim_test_runner",
            ),
            Evidence(
                domain=EvidenceDomain.SOFTWARE,
                kind="stdout",
                value=outcome.stdout,
                source="sim_test_runner",
            ),
            Evidence(
                domain=EvidenceDomain.SOFTWARE,
                kind="stderr",
                value=outcome.stderr,
                source="sim_test_runner",
            ),
        )
        if outcome.exit_code != 0:
            self.world.clear_states("test_passed")
            return self.failure(
                "TEST_FAILED",
                "independent test rejected the code-agent artifact",
                evidence=evidence,
                invalidated_states=("test_passed",),
            )

        self.world.set_states("test_passed")
        return SkillExecution(
            status=SkillStatus.SUCCEEDED,
            claimed_states=("test_passed",),
            evidence=evidence,
            message="independent test passed",
        )


class RequestFix(SimulationSkill):
    contract = SkillContract(
        name="request_fix",
        version="0.2.0",
        preconditions=("artifact_generated", "code_agent_ready"),
        local_postconditions=("artifact_fixed", "artifact_generated"),
        resources=("keyboard", "screen", "code_agent_sandbox"),
    )

    async def execute(
        self, state: StateSnapshot, arguments: Mapping[str, Any]
    ) -> SkillExecution:
        del state, arguments
        path = self.world.sandbox.fix(self.world.scenario.expected_output)
        artifact = Evidence(
            domain=EvidenceDomain.SOFTWARE,
            kind="artifact_path",
            value=str(path),
            source="sim_code_agent",
        )
        return self.success(
            ("artifact_fixed", "artifact_generated"),
            domain=EvidenceDomain.SOFTWARE,
            source="sim_code_agent",
            extra_evidence=(artifact,),
        )


class ReportTask(SimulationSkill):
    contract = SkillContract(
        name="report_task",
        version="0.2.0",
        preconditions=("test_passed",),
        local_postconditions=("task_reported",),
        resources=("screen",),
    )

    async def execute(
        self, state: StateSnapshot, arguments: Mapping[str, Any]
    ) -> SkillExecution:
        del state, arguments
        return self.success(
            ("task_reported",),
            domain=EvidenceDomain.SYSTEM,
            source="sim_code_agent",
            message="task completion reported with verified test evidence",
        )


def build_simulation_registry(world: SimulatedWorld) -> SkillRegistry:
    registry = SkillRegistry()
    for skill_type in (
        NavigateToWorkstation,
        ClearNavigationPath,
        AlignWithChair,
        RealignWithChair,
        SitAndPrepareForTyping,
        StabilizeWorkPose,
        CalibrateKeyboard,
        RecalibrateKeyboard,
        PressChord,
        RestoreTerminalFocus,
        LaunchCodeAgent,
        RestoreCodeAgentSession,
        SubmitTask,
        RunTest,
        RequestFix,
        ReportTask,
    ):
        registry.register(skill_type(world))
    return registry
