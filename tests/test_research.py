from __future__ import annotations

import unittest

from vskill_os.research import ReferenceCatalog
from vskill_os.simulation.app import project_root


class ResearchCatalogTests(unittest.TestCase):
    def test_catalog_has_actionable_sources_for_each_requested_subsystem(self) -> None:
        catalog = ReferenceCatalog.from_json(
            project_root() / "configs/research/projects.json"
        )
        for subsystem in (
            "locomotion",
            "sitting",
            "dexterous_hand",
            "typing",
            "speech",
            "local_llm",
        ):
            self.assertTrue(catalog.for_subsystem(subsystem), subsystem)
        self.assertTrue(catalog.integration_targets())


if __name__ == "__main__":
    unittest.main()
