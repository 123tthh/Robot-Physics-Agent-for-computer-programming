from __future__ import annotations

import unittest

from vskill_os.contracts import SkillContract
from vskill_os.registry import SkillRegistry
from vskill_os.skills.mock_computer import PressChordSkill


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


if __name__ == "__main__":
    unittest.main()
