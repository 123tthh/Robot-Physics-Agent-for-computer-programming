from __future__ import annotations

import json
import tempfile
import time
import os
import signal
import unittest
from pathlib import Path

from vskill_os.simulation.app import project_root
from vskill_os.simulation.assets import G1SimulationManifest
from vskill_os.simulation.desktop import DesktopStatus, PtyDesktopWorker
from vskill_os.simulation.hybrid import run_hybrid_smoke
from vskill_os.simulation.keyboard_bridge import (
    KeyEvent,
    KeyEventType,
    KeycapPhase,
    KeycapSample,
    KeyboardEventBridge,
)
from vskill_os.simulation.navigation_assets import NavigationRoom
from vskill_os.simulation.workstation_assets import WorkstationAssetChain
from vskill_os.adapters.isaac.p1_scene import (
    key_legend,
    keyboard_key_center,
    standard_keyboard_layout,
)


class KeyboardBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bridge = KeyboardEventBridge()
        self.now = time.monotonic()

    def sample(self, displacement: float, contact: bool, offset: float, **kwargs):
        return KeycapSample(
            key="t",
            displacement_m=displacement,
            in_contact=contact,
            monotonic_time=self.now + offset,
            sample_id=f"sample-{offset}",
            **kwargs,
        )

    def test_contact_without_actuation_emits_no_event(self) -> None:
        events = self.bridge.process(
            self.sample(0.0008, True, 0.01), now=self.now + 0.01
        )
        self.assertFalse(events)
        self.assertEqual(self.bridge.phase("t"), KeycapPhase.CONTACT)

    def test_press_hold_release_emits_exactly_one_pair_despite_bounce(self) -> None:
        samples = (
            self.sample(0.0005, True, 0.01),
            self.sample(0.0022, True, 0.02),
            self.sample(0.0018, True, 0.03),
            self.sample(0.0021, True, 0.04),
            self.sample(0.0010, True, 0.05),
            self.sample(0.0013, True, 0.06),
            self.sample(0.0, False, 0.07),
        )
        events = self.bridge.replay(samples)
        self.assertEqual(
            [event.event_type for event in events],
            [KeyEventType.KEYDOWN, KeyEventType.KEYUP],
        )
        self.assertEqual(self.bridge.keys_down, ())

    def test_release_bounce_cannot_rearm_until_contact_clears(self) -> None:
        samples = (
            self.sample(0.0022, True, 0.01),
            self.sample(0.0010, True, 0.02),
            self.sample(0.0022, True, 0.04),
            self.sample(0.0, False, 0.05),
            self.sample(0.0022, True, 0.06),
        )
        events = self.bridge.replay(samples)

        self.assertEqual(
            [event.event_type for event in events],
            [KeyEventType.KEYDOWN, KeyEventType.KEYUP, KeyEventType.KEYDOWN],
        )

    def test_duplicate_stale_and_adjacent_contacts_are_recorded(self) -> None:
        sample = self.sample(
            0.0005,
            True,
            0.01,
            adjacent_contacts=("r",),
        )
        self.bridge.process(sample, now=self.now + 0.01)
        self.bridge.process(sample, now=self.now + 0.01)
        stale = self.sample(0.0022, True, 0.02)
        self.bridge.process(stale, now=self.now + 1.0)

        self.assertEqual(self.bridge.adjacent_contact_log[-1][2], ("r",))
        self.assertEqual(
            [reason for _, reason in self.bridge.rejected_samples],
            ["DUPLICATE_SAMPLE", "STALE_SAMPLE"],
        )

    def test_all_keys_up_releases_a_stuck_key(self) -> None:
        self.bridge.process(self.sample(0.0022, True, 0.01), now=self.now + 0.01)
        released = self.bridge.all_keys_up(monotonic_time=self.now + 0.02)
        self.assertEqual(released[0].event_type, KeyEventType.KEYUP)
        self.assertEqual(released[0].reason, "worker_shutdown")
        self.assertFalse(self.bridge.keys_down)

    def test_maximum_hold_forces_key_up(self) -> None:
        self.bridge.process(self.sample(0.0022, True, 0.01), now=self.now + 0.01)
        held = self.sample(
            0.0022,
            True,
            self.bridge.config.maximum_hold_seconds + 0.02,
        )
        events = self.bridge.process(held, now=held.monotonic_time)

        self.assertEqual([event.event_type for event in events], [KeyEventType.KEYUP])
        self.assertEqual(events[0].reason, "maximum_hold")
        self.assertFalse(self.bridge.keys_down)

    def test_bridge_event_source_is_distinct_from_physics_oracle(self) -> None:
        sample = self.sample(0.0022, True, 0.01)
        event = self.bridge.process(sample, now=self.now + 0.01)[0]

        self.assertEqual(sample.source, "isaac_keycap_oracle")
        self.assertEqual(event.source, "keyboard_event_bridge")


class HybridSimulationTests(unittest.TestCase):
    def test_p1_procedural_keyboard_has_unique_full_layout(self) -> None:
        layout = standard_keyboard_layout()
        names = {key.name for key in layout}

        self.assertEqual(len(layout), 74)
        self.assertEqual(len(names), len(layout))
        self.assertTrue({"left_ctrl", "left_alt", "t", "space", "enter"} <= names)

    def test_keyboard_legends_and_operator_side_are_explicit(self) -> None:
        layout = standard_keyboard_layout()
        by_name = {key.name: key for key in layout}
        space = keyboard_key_center(
            by_name["space"], operator_row_x=0.72, left_origin_y=0.145, top_z=0.76
        )
        escape = keyboard_key_center(
            by_name["escape"], operator_row_x=0.72, left_origin_y=0.145, top_z=0.76
        )
        f12 = keyboard_key_center(
            by_name["f12"], operator_row_x=0.72, left_origin_y=0.145, top_z=0.76
        )

        self.assertLess(space[0], escape[0])
        self.assertGreater(escape[1], f12[1])
        self.assertEqual(key_legend("left_ctrl"), "CTRL")
        self.assertEqual(key_legend("a"), "A")
        self.assertTrue(all(key_legend(spec.name) for spec in layout))

    def test_manifest_pins_g1_mode15_and_enables_dual_dex3(self) -> None:
        root = project_root()
        manifest = G1SimulationManifest.from_json(
            root / "configs/simulation/g1_29dof_5010_p1.json"
        )
        manifest.validate(root=root)
        capability = manifest.capability()

        self.assertEqual(capability["body_dof"], 29)
        self.assertEqual(capability["mode_machine"], 15)
        self.assertEqual(capability["wrist_motor"], "5010")
        self.assertEqual(capability["hand_assets"], "dual_dex3_1")
        self.assertEqual(capability["hand_dof"], 14)
        self.assertEqual(capability["chair_asset_type"], "passive_mobile_office_chair_5caster")
        self.assertEqual(capability["chair_passive_dof"], 10)
        self.assertEqual(capability["physics_validation"], "verified_local_isaac_runtime")
        self.assertEqual(manifest.isaac_sim_version, "5.1.0")

    def test_p0_p1_workstation_chain_has_mobile_chair_and_ordered_gates(self) -> None:
        chain = WorkstationAssetChain.from_json(
            project_root() / "configs/simulation/p0_p1_workstation_asset_chain.json"
        )

        self.assertEqual(chain.stage_order[0], "room_entry")
        self.assertEqual(chain.stage_order[-1], "work_pose")
        self.assertEqual(len(chain.stages), 6)
        self.assertEqual(chain.chair["caster_count"], 5)
        self.assertEqual(chain.summary()["passive_dof_count"], 10)
        self.assertAlmostEqual(chain.summary()["seat_top_height_m"], 0.4775)
        self.assertFalse(chain.summary()["world_fixed"])
        self.assertGreater(chain.chair["mobility_probe"]["force_n"], 0.0)
        self.assertAlmostEqual(chain.chair["rolling_joint_max_velocity_deg_s"], 20.0)
        self.assertIn("pelvis_seat_contact", chain.stage("seated_stabilization").required_evidence)
        self.assertEqual(
            chain.stage("work_pose").required_evidence,
            ("seated_stable", "screen_visible", "hands_reachable"),
        )

    def test_p1_navigation_room_has_four_walls_and_multiple_workstations(self) -> None:
        room = NavigationRoom.from_json(
            project_root() / "configs/simulation/p1_navigation_room.json"
        )

        self.assertEqual(room.summary()["logical_wall_count"], 4)
        self.assertEqual(room.summary()["wall_collider_count"], 6)
        self.assertEqual(room.summary()["workstation_ids"], ["A", "B", "C"])
        self.assertEqual(room.target_workstation.id, "A")
        self.assertEqual(room.summary()["spawn_count"], 3)
        self.assertEqual(room.summary()["anchor_count"], 12)
        self.assertEqual(room.summary()["obstacle_count"], 1)

    def test_durable_isaac_acceptance_report_matches_manifest(self) -> None:
        root = project_root()
        manifest = G1SimulationManifest.from_json(
            root / "configs/simulation/g1_29dof_5010_p1.json"
        )
        report = manifest.verify_acceptance_report(
            root / "reports/isaac_p1_acceptance.json"
        )

        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["robot"]["joint_count"], 43)
        self.assertEqual(report["scene"]["keyboard_key_count"], 74)
        self.assertEqual(report["scene"]["keyboard_legend_count"], 74)
        self.assertTrue(report["robot"]["faces_keyboard"])
        workstation = report["workstation_asset_chain"]
        self.assertEqual(workstation["stage_count"], 6)
        self.assertEqual(workstation["caster_count"], 5)
        self.assertEqual(workstation["passive_dof_count"], 10)
        self.assertEqual(workstation["chair_rigid_body_count"], 11)
        self.assertTrue(workstation["required_prims_present"])
        self.assertAlmostEqual(workstation["chair_seat_top_height_m"], 0.4775)
        self.assertFalse(workstation["chair_world_fixed"])
        mobility = workstation["chair_mobility_probe"]
        self.assertTrue(mobility["passed"])
        self.assertGreaterEqual(
            mobility["displacement_m"], mobility["minimum_displacement_m"]
        )
        self.assertTrue(mobility["sliding_dominant"])
        self.assertLessEqual(
            mobility["rolling_contribution_ratio"],
            mobility["maximum_rolling_contribution_ratio"],
        )
        self.assertEqual(
            workstation["motion_policy_status"],
            "not_claimed_asset_readiness_only",
        )
        navigation = report["navigation_environment"]
        self.assertEqual(navigation["logical_wall_count"], 4)
        self.assertEqual(navigation["wall_collider_count"], 6)
        self.assertEqual(navigation["workstation_ids"], ["A", "B", "C"])
        self.assertEqual(navigation["target_workstation_id"], "A")
        self.assertEqual(navigation["spawn_count"], 3)
        self.assertEqual(navigation["anchor_count"], 12)
        self.assertTrue(navigation["required_prims_present"])
        self.assertEqual(
            navigation["policy_status"], "not_claimed_asset_readiness_only"
        )

    def test_durable_workstation_usd_topology_report_passes_all_gates(self) -> None:
        report_path = project_root() / "reports/isaac_p1_workstation_validation.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))

        self.assertEqual(report["phase"], "P1")
        self.assertEqual(report["status"], "passed")
        self.assertTrue(all(report["checks"].values()))
        self.assertEqual(report["details"]["default_prim"], "/World")
        self.assertEqual(len(report["details"]["chair_rigid_body_paths"]), 11)
        self.assertEqual(len(report["details"]["chair_joint_paths"]), 10)
        self.assertEqual(len(report["details"]["chair_contact_paths"]), 7)
        self.assertTrue(report["checks"]["chair_root_free"])
        self.assertTrue(report["checks"]["chair_seat_height_matches_config"])
        self.assertFalse(report["details"]["chair_fixed_joint_paths"])
        self.assertAlmostEqual(report["details"]["chair_seat_top_height_m"], 0.4775)
        self.assertEqual(
            report["details"]["chair_rolling_joint_max_velocity_deg_s"],
            [20.0] * 5,
        )
        self.assertEqual(
            report["details"]["navigation_workstation_ids"], ["A", "B", "C"]
        )
        self.assertEqual(len(report["details"]["navigation_wall_paths"]), 6)
        self.assertEqual(len(report["details"]["navigation_anchor_paths"]), 12)
        self.assertTrue(report["checks"]["navigation_four_wall_shell"])
        self.assertTrue(report["checks"]["navigation_anchors_complete"])
        self.assertEqual(report["formal_simready_conformance"], "not_claimed")

    def test_durable_p2_acceptance_report_preserves_physical_gates(self) -> None:
        report_path = project_root() / "reports/isaac_p2_acceptance.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))

        self.assertEqual(report["phase"], "P2")
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["robot"]["joint_count"], 43)
        self.assertEqual(report["keyboard"]["joint_count"], 74)

        graph = report["action_graph"]
        self.assertFalse(graph["joint_commands_in_graph"])
        self.assertEqual(graph["run"]["status"], "succeeded")
        self.assertEqual(len(graph["run"]["completed_nodes"]), graph["node_count"])

        acceptance = report["fingertip_acceptance"]
        self.assertEqual(acceptance["event_counts"], {"keydown": 3, "keyup": 3})
        self.assertTrue(acceptance["event_timeline_verified"])
        self.assertTrue(acceptance["event_duration_verified"])
        self.assertLessEqual(
            acceptance["event_duration_seconds"],
            acceptance["duration_limit_seconds"],
        )
        self.assertFalse(acceptance["keys_down_after_shutdown"])
        for metrics in acceptance["key_metrics"].values():
            self.assertGreaterEqual(metrics["maximum_key_displacement_m"], 0.002)
            self.assertLessEqual(metrics["maximum_contact_force_n"], 8.0)

        screen_node = next(
            result
            for result in graph["run"]["node_results"]
            if result["node_id"] == "screen.verify"
        )
        screen = screen_node["evidence"][0]["value"]
        self.assertTrue(screen["visible"])
        self.assertTrue(screen["focused"])
        self.assertFalse(screen["keys_down"])

    def test_keycap_chord_opens_terminal_and_pty_executes_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = run_hybrid_smoke(root=project_root(), workspace=Path(directory))

        self.assertTrue(result.succeeded, result.screen_text)
        self.assertEqual(result.event_counts["keydown"], result.event_counts["keyup"])
        self.assertFalse(result.keys_down_after_stop)

    def test_desktop_worker_crash_clears_modifiers_and_can_recover(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            worker = PtyDesktopWorker(directory)
            try:
                worker.start()
                worker.open_terminal()
                worker.handle_event(
                    KeyEvent(
                        KeyEventType.KEYDOWN,
                        "left_ctrl",
                        time.monotonic(),
                        "keyboard_event_bridge",
                        ("left_ctrl",),
                    )
                )
                assert worker.process_id is not None
                os.kill(worker.process_id, signal.SIGKILL)
                deadline = time.monotonic() + 1.0
                while worker.status is not DesktopStatus.FAILED and time.monotonic() < deadline:
                    worker.snapshot()
                    time.sleep(0.01)
                self.assertEqual(worker.status, DesktopStatus.FAILED)
                self.assertFalse(worker.modifiers)
                self.assertFalse(worker.focused)

                worker.recover()
                self.assertEqual(worker.status, DesktopStatus.READY)
                worker.open_terminal()
                self.assertEqual(worker.status, DesktopStatus.TERMINAL_FOCUSED)
            finally:
                worker.stop()


if __name__ == "__main__":
    unittest.main()
