from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from vskill_os.domain import SkillStatus
from vskill_os.control import ControlPhase
from vskill_os.simulation.app import build_simulation_app, project_root


class SimulationTests(unittest.IsolatedAsyncioTestCase):
    async def test_nominal_graph_completes_without_recovery(self) -> None:
        scenario = project_root() / "configs/scenarios/nominal.json"
        app = build_simulation_app(scenario_path=scenario)
        try:
            result = await app.run(task_id="nominal")
            recovery_events = [
                event
                for event in app.ledger.list_events("nominal")
                if event.kind == "recovery_selected"
            ]
        finally:
            app.close()

        self.assertEqual(result.status, SkillStatus.SUCCEEDED)
        self.assertFalse(recovery_events)
        self.assertIn("task_reported", result.final_state.facts)
        self.assertEqual(app.world.control.phase, ControlPhase.TYPING)

    async def test_checkpoint_can_resume_in_a_new_app(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger = root / "task.db"
            workspace = root / "workspace"
            scenario = project_root() / "configs/scenarios/software_recovery.json"

            first = build_simulation_app(
                scenario_path=scenario,
                ledger_path=ledger,
                workspace=workspace,
            )
            try:
                partial = await first.run(task_id="resume-task", stop_after_nodes=7)
            finally:
                first.close()

            second = build_simulation_app(
                scenario_path=scenario,
                ledger_path=ledger,
                workspace=workspace,
            )
            try:
                resumed = await second.run(task_id="resume-task", resume=True)
            finally:
                second.close()

        self.assertEqual(partial.status, SkillStatus.CANCELED)
        self.assertEqual(len(partial.completed_nodes), 7)
        self.assertTrue(resumed.resumed)
        self.assertEqual(resumed.status, SkillStatus.SUCCEEDED)
        self.assertIn("test_passed", resumed.final_state.facts)

    async def test_mixed_faults_are_recovered(self) -> None:
        scenario = project_root() / "configs/scenarios/mixed_faults.json"
        app = build_simulation_app(scenario_path=scenario)
        try:
            result = await app.run(task_id="mixed")
            recovery_events = [
                event.payload["recovery_skill"]
                for event in app.ledger.list_events("mixed")
                if event.kind == "recovery_selected"
            ]
        finally:
            app.close()

        self.assertEqual(result.status, SkillStatus.SUCCEEDED)
        self.assertIn("recalibrate_keyboard", recovery_events)
        self.assertIn("request_fix", recovery_events)
        self.assertNotIn("code_agent_claimed_complete", result.final_state.facts)

    async def test_posture_recovery_does_not_repeat_the_contact_transition(self) -> None:
        scenario = project_root() / "configs/scenarios/posture_recovery.json"
        app = build_simulation_app(scenario_path=scenario)
        try:
            result = await app.run(task_id="posture-recovery")
            sit_calls = [
                item
                for item in result.skill_results
                if item.skill == "sit_and_prepare_for_typing"
            ]
        finally:
            app.close()

        self.assertEqual(result.status, SkillStatus.SUCCEEDED)
        self.assertEqual(len(sit_calls), 2)
        self.assertIn("already restored", sit_calls[-1].message)
        self.assertEqual(app.world.control.phase, ControlPhase.TYPING)


if __name__ == "__main__":
    unittest.main()
