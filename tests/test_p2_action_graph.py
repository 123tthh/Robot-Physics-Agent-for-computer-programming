from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from vskill_os.action_graph import (
    ActionExecution,
    ActionGraph,
    ActionGraphModule,
    ActionGraphRunner,
    ActionKind,
    ActionModuleCatalog,
    ActionNode,
    ActionStatus,
    EvidenceRequirement,
    IndependentActionVerifier,
    KeyboardActionCompiler,
)
from vskill_os.domain import Evidence, EvidenceDomain
from vskill_os.simulation.app import project_root
from vskill_os.simulation.hybrid import HybridSimulationHarness
from vskill_os.simulation.p2 import P2SimulationActionAdapter, run_p2_smoke
from vskill_os.typing import (
    ChordPlan,
    Finger,
    FingerConflictError,
    HandSide,
    KeyAssignment,
    KeyRole,
    MultiplePrimaryKeysError,
    NoPrimaryKeyError,
    PlannedKeyEventType,
    TypingPlanner,
    UnreachableKeyError,
)


class ActionGraphModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = project_root()
        self.catalog = ActionModuleCatalog.from_directory(
            self.root / "configs/action_graphs/modules"
        )
        self.compiler = KeyboardActionCompiler(self.catalog)

    def test_modules_have_versioned_interfaces_and_namespaced_composition(self) -> None:
        self.assertEqual(
            self.catalog.versions,
            {
                "bimanual_prepose": "1.1.0",
                "key_prepose": "1.0.0",
                "key_down": "1.1.0",
                "key_up": "1.0.1",
                "modifier_release_group": "1.0.0",
                "screen_gate": "1.0.0",
            },
        )
        graph = self.compiler.compile_chord(
            TypingPlanner().plan_chord_detailed(["ctrl", "alt", "t"])
        )
        self.assertEqual(graph.contract_version, "1.0.0")
        self.assertFalse(graph.contains_low_level_commands)
        self.assertEqual(
            graph.parallel_layers()[0],
            ("prepose.left_prepose", "prepose.right_prepose"),
        )
        self.assertEqual(len(graph.modules), 8)

    def test_schema_rejects_joint_and_torque_commands(self) -> None:
        for field in ("joint_targets", "joint_velocity", "torque_commands"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                ActionNode(
                    node_id="unsafe",
                    kind=ActionKind.CARTESIAN_MOVE,
                    executor="left.index",
                    parameters={field: [0.0]},
                )

    def test_module_rejects_undeclared_input_placeholders(self) -> None:
        with self.assertRaises(ValueError):
            ActionGraphModule.from_dict(
                {
                    "name": "bad",
                    "version": "1.0.0",
                    "interface": {
                        "inputs": [],
                        "outputs": [{"name": "done", "type": "state", "node": "a"}],
                    },
                    "nodes": [
                        {
                            "id": "a",
                            "kind": "barrier",
                            "executor": "${missing}",
                        }
                    ],
                }
            )

    def test_action_graph_round_trip_keeps_module_provenance(self) -> None:
        original = self.compiler.compile_chord(
            TypingPlanner().plan_chord_detailed(["ctrl", "alt", "t"])
        )
        restored = ActionGraph.from_dict(original.to_dict())
        self.assertEqual(restored.topological_order(), original.topological_order())
        self.assertEqual(restored.modules, original.modules)


class ChordPlanTests(unittest.TestCase):
    def test_ctrl_alt_t_assigns_every_key_to_unique_bimanual_executor(self) -> None:
        plan = TypingPlanner().plan_chord_detailed(["ctrl", "alt", "t"])
        self.assertEqual(len(plan.assignments), 3)
        self.assertEqual(len({item.executor for item in plan.assignments}), 3)
        self.assertEqual({item.hand for item in plan.assignments}, {HandSide.LEFT, HandSide.RIGHT})
        self.assertEqual(plan.press_order[-1], "t")
        self.assertEqual(plan.release_order[0], "t")
        self.assertEqual(plan.expected_events[0].event_type, PlannedKeyEventType.KEYDOWN)
        self.assertEqual(plan.expected_events[-1].event_type, PlannedKeyEventType.KEYUP)
        self.assertFalse(plan.contains_joint_commands)

    def test_no_primary_multiple_primary_and_unreachable_are_localized(self) -> None:
        planner = TypingPlanner()
        with self.assertRaises(NoPrimaryKeyError):
            planner.plan_chord_detailed(["ctrl", "alt"])
        with self.assertRaises(MultiplePrimaryKeysError):
            planner.plan_chord_detailed(["a", "b"])
        with self.assertRaises(UnreachableKeyError):
            planner.plan_chord_detailed(["ctrl", "t"], reachable_keys={"left_ctrl"})

    def test_overlapping_finger_assignment_is_rejected(self) -> None:
        duplicate_executor = (
            KeyAssignment("ctrl", "left_ctrl", KeyRole.MODIFIER, HandSide.LEFT, Finger.INDEX),
            KeyAssignment("t", "t", KeyRole.PRIMARY, HandSide.LEFT, Finger.INDEX),
        )
        with self.assertRaises(FingerConflictError):
            ChordPlan(
                logical_keys=("ctrl", "t"),
                assignments=duplicate_executor,
                press_order=("left_ctrl", "t"),
                release_order=("t", "left_ctrl"),
                held_while_primary=("left_ctrl",),
                expected_events=(),
            )


class _ControllerOnlyAdapter:
    async def execute(self, node: ActionNode) -> ActionExecution:
        return ActionExecution(
            True,
            (
                Evidence(
                    domain=EvidenceDomain.PHYSICAL,
                    kind="barrier_state",
                    value={"ready": True},
                    source="action_controller",
                ),
            ),
        )

    async def emergency_release(self, reason: str) -> tuple[Evidence, ...]:
        return ()


class ActionGraphRuntimeTests(unittest.TestCase):
    def test_controller_claim_cannot_verify_an_action(self) -> None:
        graph = ActionGraph(
            name="controller_only",
            version="1.0.0",
            contract_version="1.0.0",
            nodes=(
                ActionNode(
                    node_id="gate",
                    kind=ActionKind.BARRIER,
                    executor="coordinator",
                    evidence=(
                        EvidenceRequirement(EvidenceDomain.PHYSICAL, "barrier_state"),
                    ),
                ),
            ),
        )
        result = asyncio.run(
            ActionGraphRunner(
                adapter=_ControllerOnlyAdapter(), verifier=IndependentActionVerifier()
            ).run(graph)
        )
        self.assertEqual(result.status, ActionStatus.FAILED)
        self.assertEqual(result.failure_code, "EVIDENCE_MISSING")

    def test_actuation_requires_verified_travel_not_only_key_event(self) -> None:
        node = ActionNode(
            node_id="actuate",
            kind=ActionKind.KEY_ACTUATE,
            executor="left.middle",
            parameters={
                "target_key": "t",
                "event_type": "keydown",
                "target_travel_m": 0.002,
                "maximum_contact_force_n": 8.0,
            },
            evidence=(
                EvidenceRequirement(EvidenceDomain.PHYSICAL, "keycap_travel"),
                EvidenceRequirement(EvidenceDomain.PHYSICAL, "key_event"),
            ),
            failure_codes=("INSUFFICIENT_TRAVEL",),
        )
        execution = ActionExecution(
            True,
            (
                Evidence(
                    EvidenceDomain.PHYSICAL,
                    "keycap_travel",
                    {"target_key": "t", "travel_m": 0.001, "force_n": 1.0},
                    "independent_keycap_sensor",
                ),
                Evidence(
                    EvidenceDomain.PHYSICAL,
                    "key_event",
                    {"event_type": "keydown", "key": "t"},
                    "keyboard_event_bridge",
                ),
            ),
        )
        verification = IndependentActionVerifier().verify(node, execution)
        self.assertFalse(verification.accepted)
        self.assertEqual(verification.failure_code, "INSUFFICIENT_TRAVEL")

    def _run_fault(self, node_id: str, failure: str):
        root = project_root()
        with tempfile.TemporaryDirectory() as workspace:
            harness = HybridSimulationHarness(
                project_root=root,
                workspace=workspace,
                manifest_path=root / "configs/simulation/g1_29dof_5010_p1.json",
            )
            harness.start()
            try:
                adapter = P2SimulationActionAdapter(
                    harness, faults={node_id: (failure,)}
                )
                graph = KeyboardActionCompiler(
                    ActionModuleCatalog.from_directory(
                        root / "configs/action_graphs/modules"
                    )
                ).compile_chord(
                    TypingPlanner().plan_chord_detailed(["ctrl", "alt", "t"])
                )
                result = asyncio.run(
                    ActionGraphRunner(
                        adapter=adapter, verifier=IndependentActionVerifier()
                    ).run(graph)
                )
                return result, harness.bridge.keys_down
            finally:
                harness.stop()

    def test_alt_missed_is_localized_and_all_modifiers_are_released(self) -> None:
        result, keys_down = self._run_fault(
            "down_001_right_alt.actuate", "ALT_MISSED"
        )
        self.assertEqual(result.status, ActionStatus.FAILED)
        self.assertEqual(result.failure_code, "ALT_MISSED")
        self.assertFalse(keys_down)
        self.assertEqual(result.cleanup_evidence[0].kind, "all_keys_up")

    def test_adjacent_contact_is_rejected_by_independent_verifier(self) -> None:
        result, keys_down = self._run_fault(
            "down_002_t.contact", "ADJACENT_KEY_CONTACT"
        )
        self.assertEqual(result.failure_code, "ADJACENT_KEY_CONTACT")
        self.assertFalse(keys_down)

    def test_stuck_modifier_triggers_unified_release(self) -> None:
        result, keys_down = self._run_fault(
            "up_modifiers.release", "STUCK_MODIFIER"
        )
        self.assertEqual(result.failure_code, "STUCK_MODIFIER")
        self.assertFalse(keys_down)

    def test_full_p2_smoke_closes_contact_event_screen_and_repair_loops(self) -> None:
        result = asyncio.run(run_p2_smoke(root=project_root()))
        self.assertTrue(result.succeeded)
        self.assertTrue(result.chord_event_timeline_verified)
        self.assertEqual(result.chord_trials, 200)
        self.assertGreaterEqual(result.chord_success_rate, 0.98)
        self.assertTrue(result.repaired_text_observed)
        self.assertTrue(result.output_observed)
        self.assertEqual(result.keys_down_after_stop, ())


if __name__ == "__main__":
    unittest.main()
