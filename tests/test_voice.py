from __future__ import annotations

import unittest

from vskill_os.simulation.app import project_root
from vskill_os.voice import (
    AgentToolPolicy,
    LocalModelStack,
    PlannedToolCall,
    RuleBasedToolPlanner,
    ScriptedASR,
    VoiceCommandPipeline,
)


class VoiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_chinese_code_goal_maps_to_bounded_l3_skills(self) -> None:
        policy = AgentToolPolicy(
            frozenset(
                {"prepare_computer", "ask_code_agent", "verify_software_task"}
            )
        )
        pipeline = VoiceCommandPipeline(
            asr=ScriptedASR("创建 Python 程序并运行测试"),
            planner=RuleBasedToolPlanner(),
            policy=policy,
        )
        result = await pipeline.handle_audio(b"simulated-pcm")
        self.assertTrue(result.accepted)
        self.assertEqual(
            [call.skill for call in result.calls],
            ["prepare_computer", "ask_code_agent", "verify_software_task"],
        )

    async def test_policy_rejects_low_level_and_excess_calls(self) -> None:
        policy = AgentToolPolicy(frozenset({"safe_skill"}), max_calls_per_turn=1)
        accepted, rejected = policy.partition(
            (
                PlannedToolCall("joint_torque_command"),
                PlannedToolCall("safe_skill"),
            )
        )
        self.assertFalse(accepted)
        self.assertEqual(len(rejected), 2)

    async def test_model_stack_configs_load_without_model_dependencies(self) -> None:
        root = project_root()
        edge = LocalModelStack.from_json(root / "configs/models/robot_edge.json")
        workstation = LocalModelStack.from_json(
            root / "configs/models/workstation.json"
        )
        self.assertEqual(edge.llm.runtime, "llama.cpp")
        self.assertEqual(workstation.asr.runtime, "FunASR")


if __name__ == "__main__":
    unittest.main()
