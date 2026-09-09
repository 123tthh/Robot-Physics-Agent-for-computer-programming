from __future__ import annotations

import unittest

from vskill_os.config import load_skill_contract, validate_contract_matches
from vskill_os.contracts import SkillContract
from vskill_os.registry import SkillRegistry
from vskill_os.skills.mock_computer import PressChordSkill
from vskill_os.skills.simulation import PressChord, RepositionWhileSeated
from vskill_os.simulation.app import project_root


class ContractAndRegistryTests(unittest.TestCase):
    def test_contract_rejects_duplicate_entries(self) -> None:
        with self.assertRaises(ValueError):
            SkillContract(
                name="bad_skill",
                version="0.1.0",
                resources=("left_arm", "left_arm"),
            )

    def test_registry_rejects_duplicate_skill_name(self) -> None:
        registry = SkillRegistry()
        registry.register(PressChordSkill())
        with self.assertRaises(ValueError):
            registry.register(PressChordSkill())

    def test_registry_lists_contracts(self) -> None:
        registry = SkillRegistry()
        registry.register(PressChordSkill())
        contracts = registry.contracts()
        self.assertEqual(contracts[0]["name"], "press_chord")
        self.assertIn("terminal_ready", contracts[0]["next_skill_readiness"])

    def test_configured_press_chord_matches_simulation_contract(self) -> None:
        configured = load_skill_contract(
            project_root() / "configs/skills/press_chord.json"
        )
        validate_contract_matches(PressChord.contract, configured)

    def test_configured_seated_reposition_matches_simulation_contract(self) -> None:
        configured = load_skill_contract(
            project_root() / "configs/skills/reposition_while_seated.json"
        )
        validate_contract_matches(RepositionWhileSeated.contract, configured)


if __name__ == "__main__":
    unittest.main()
