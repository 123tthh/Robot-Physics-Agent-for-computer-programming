from __future__ import annotations

import unittest

from vskill_os.graph import SkillGraph, SkillNode


class SkillGraphTests(unittest.TestCase):
    def test_topological_order_and_ready_nodes(self) -> None:
        graph = SkillGraph(
            "example",
            [
                SkillNode("a", "first"),
                SkillNode("b", "second", depends_on=("a",)),
                SkillNode("c", "third", depends_on=("a",)),
            ],
        )
        self.assertEqual(graph.topological_order(), ("a", "b", "c"))
        self.assertEqual(
            [node.node_id for node in graph.ready_nodes({"a"})],
            ["b", "c"],
        )

    def test_cycle_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            SkillGraph(
                "cyclic",
                [
                    SkillNode("a", "first", depends_on=("b",)),
                    SkillNode("b", "second", depends_on=("a",)),
                ],
            )

    def test_unknown_dependency_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            SkillGraph("bad", [SkillNode("a", "first", depends_on=("missing",))])


if __name__ == "__main__":
    unittest.main()
