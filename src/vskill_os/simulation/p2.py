"""P2 modular ActionGraph simulation over key physics, events, and a real PTY."""

from __future__ import annotations

import asyncio
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
import tempfile
import time
from typing import Mapping

from ..action_graph import (
    ActionExecution,
    ActionGraphRunner,
    ActionKind,
    ActionModuleCatalog,
    ActionNode,
    ActionStatus,
    IndependentActionVerifier,
    KeyboardActionCompiler,
)
from ..domain import Evidence, EvidenceDomain
from ..typing import KeyboardCalibration, TypingPlanner
from .desktop import ScreenFrame, render_terminal_text
from .hybrid import HybridSimulationHarness
from .keyboard_bridge import KeyEvent, KeyEventType


@dataclass(frozen=True, slots=True)
class P2SmokeResult:
    succeeded: bool
    action_graph_contract: str
    module_versions: Mapping[str, str]
    single_key_succeeded: bool
    chord_succeeded: bool
    chord_event_timeline_verified: bool
    chord_trials: int
    chord_successes: int
    chord_success_rate: float
    text_succeeded: bool
    repair_succeeded: bool
    repaired_text_observed: bool
    output_observed: bool
    terminal_visible: bool
    terminal_focused: bool
    keys_down_after_stop: tuple[str, ...]
    event_counts: Mapping[str, int]
    action_counts: Mapping[str, int]
    graph_summaries: tuple[Mapping[str, object], ...]
    screen_text: str

    def to_dict(self) -> dict[str, object]:
        return {
            "succeeded": self.succeeded,
            "action_graph_contract": self.action_graph_contract,
            "module_versions": dict(self.module_versions),
            "single_key_succeeded": self.single_key_succeeded,
            "chord_succeeded": self.chord_succeeded,
            "chord_event_timeline_verified": self.chord_event_timeline_verified,
            "chord_trials": self.chord_trials,
            "chord_successes": self.chord_successes,
            "chord_success_rate": self.chord_success_rate,
            "text_succeeded": self.text_succeeded,
            "repair_succeeded": self.repair_succeeded,
            "repaired_text_observed": self.repaired_text_observed,
            "output_observed": self.output_observed,
            "terminal_visible": self.terminal_visible,
            "terminal_focused": self.terminal_focused,
            "keys_down_after_stop": list(self.keys_down_after_stop),
            "event_counts": dict(self.event_counts),
            "action_counts": dict(self.action_counts),
            "graph_summaries": [dict(item) for item in self.graph_summaries],
            "screen_text": self.screen_text,
        }


class P2SimulationActionAdapter:
    """Deterministic adapter preserving the same contract as an Isaac/IK backend."""

    def __init__(
        self,
        harness: HybridSimulationHarness,
        *,
        faults: Mapping[str, tuple[str, ...]] | None = None,
    ) -> None:
        self.harness = harness
        self.calibration = KeyboardCalibration((0.72, 0.145, 0.76))
        self._faults: dict[str, deque[str]] = defaultdict(deque)
        for selector, codes in (faults or {}).items():
            self._faults[selector].extend(codes)

    async def execute(self, node: ActionNode) -> ActionExecution:
        fault = self._consume_fault(node)
        target = str(node.parameters.get("target_key", ""))
        controller_claim = Evidence(
            domain=EvidenceDomain.SYSTEM,
            kind="controller_claim",
            value={"node": node.node_id, "completed": True},
            source="action_controller",
        )
        if fault == "HAND_COLLISION_RISK":
            return ActionExecution(False, (controller_claim,), fault, "collision guard tripped")
        if node.kind is ActionKind.CARTESIAN_MOVE:
            pose = self.calibration.key_pose(target)
            return ActionExecution(
                True,
                (
                    Evidence(
                        domain=EvidenceDomain.PHYSICAL,
                        kind="fingertip_pose",
                        value={
                            "target_key": target,
                            "phase": node.parameters.get("phase"),
                            "xyz_m": [pose.x_m, pose.y_m, pose.z_m],
                            "within_tolerance": fault != "UNREACHABLE_KEY",
                            "executor": node.executor,
                        },
                        source="sim_cartesian_oracle",
                    ),
                    controller_claim,
                ),
                failure_code=("UNREACHABLE_KEY" if fault == "UNREACHABLE_KEY" else None),
            )
        if node.kind is ActionKind.CONTACT_GATE:
            adjacent = ("adjacent_key",) if fault == "ADJACENT_KEY_CONTACT" else ()
            self.harness.observe_contact(target, adjacent_contacts=adjacent)
            force_n = 12.0 if fault == "FORCE_LIMIT" else 1.8
            in_contact = fault != "MISSED_CONTACT"
            return ActionExecution(
                True,
                (
                    Evidence(
                        domain=EvidenceDomain.PHYSICAL,
                        kind="fingertip_key_contact",
                        value={
                            "target_key": target,
                            "in_contact": in_contact,
                            "adjacent_contacts": list(adjacent),
                            "force_n": force_n,
                            "executor": node.executor,
                        },
                        source="sim_contact_sensor",
                    ),
                    controller_claim,
                ),
            )
        if node.kind is ActionKind.KEY_ACTUATE:
            travel = Evidence(
                domain=EvidenceDomain.PHYSICAL,
                kind="keycap_travel",
                value={"target_key": target, "travel_m": 0.0022, "force_n": 1.8},
                source="sim_keycap_oracle",
            )
            if fault == "ALT_MISSED":
                return ActionExecution(
                    False,
                    (travel, controller_claim),
                    "ALT_MISSED",
                    "Alt did not cross the actuation threshold",
                )
            if fault == "MISSED_EVENT":
                return ActionExecution(True, (travel, controller_claim))
            event_key = _adjacent_key(target) if fault == "WRONG_KEY" else target
            events = self.harness.key_down(event_key)
            event_evidence = tuple(_event_evidence(event) for event in events)
            if fault == "DUPLICATE_EVENT" and event_evidence:
                event_evidence = (*event_evidence, event_evidence[0])
            return ActionExecution(
                True,
                (travel, *event_evidence, controller_claim),
            )
        if node.kind is ActionKind.KEY_HOLD:
            return ActionExecution(
                True,
                (
                    Evidence(
                        domain=EvidenceDomain.PHYSICAL,
                        kind="key_hold_state",
                        value={
                            "target_key": target,
                            "keys_down": list(self.harness.bridge.keys_down),
                            "stable": fault != "HOLD_LOST",
                            "force_n": 1.8,
                        },
                        source="sim_hold_sensor",
                    ),
                    controller_claim,
                ),
            )
        if node.kind is ActionKind.KEY_RELEASE:
            if fault in {"STUCK_MODIFIER", "STUCK_KEY"}:
                return ActionExecution(
                    False,
                    (controller_claim,),
                    fault,
                    "key remained below its release threshold",
                )
            if fault == "MISSING_KEYUP":
                return ActionExecution(True, (controller_claim,))
            events = self.harness.key_up(target)
            return ActionExecution(
                True,
                (*(_event_evidence(event) for event in events), controller_claim),
            )
        if node.kind is ActionKind.KEY_RELEASE_GROUP:
            target_keys = tuple(
                str(key) for key in node.parameters.get("target_keys", ())
            )
            if fault == "STUCK_MODIFIER":
                return ActionExecution(False, (controller_claim,), fault)
            events = []
            states = []
            for key in target_keys:
                events.extend(self.harness.key_up(key))
                released = key not in self.harness.bridge.keys_down
                states.append(
                    Evidence(
                        domain=EvidenceDomain.PHYSICAL,
                        kind="key_release_state",
                        value={
                            "target_key": key,
                            "released": released,
                            "clear": released,
                            "clearance_m": 0.010 if released else 0.0,
                        },
                        source="sim_clearance_sensor",
                    )
                )
            return ActionExecution(
                True,
                (
                    *(_event_evidence(event) for event in events),
                    *states,
                    controller_claim,
                ),
            )
        if node.kind is ActionKind.CLEARANCE_GATE:
            released = target not in self.harness.bridge.keys_down
            if fault in {"STUCK_MODIFIER", "STUCK_KEY"}:
                released = False
            return ActionExecution(
                True,
                (
                    Evidence(
                        domain=EvidenceDomain.PHYSICAL,
                        kind="key_release_state",
                        value={
                            "target_key": target,
                            "released": released,
                            "clear": released,
                            "clearance_m": 0.010 if released else 0.0,
                        },
                        source="sim_clearance_sensor",
                    ),
                    controller_claim,
                ),
            )
        if node.kind is ActionKind.BARRIER:
            return ActionExecution(
                True,
                (
                    Evidence(
                        domain=EvidenceDomain.PHYSICAL,
                        kind="barrier_state",
                        value={"barrier": node.parameters.get("barrier"), "ready": True},
                        source="sim_barrier_monitor",
                    ),
                    controller_claim,
                ),
            )
        if node.kind is ActionKind.SCREEN_GATE:
            frame = await self._screen_frame(node)
            return ActionExecution(
                True,
                (
                    Evidence(
                        domain=EvidenceDomain.GUI,
                        kind="screen_state",
                        value={
                            "frame_id": frame.frame_id,
                            "visible": frame.visible,
                            "focused": frame.focused,
                            "text": render_terminal_text(frame.text),
                            "keys_down": list(self.harness.bridge.keys_down),
                        },
                        source=frame.source,
                    ),
                    controller_claim,
                ),
            )
        return ActionExecution(False, failure_code="UNSUPPORTED_ACTION")

    async def emergency_release(self, reason: str) -> tuple[Evidence, ...]:
        releases = self.harness.release_all(f"action_graph:{reason}")
        return (
            Evidence(
                domain=EvidenceDomain.PHYSICAL,
                kind="all_keys_up",
                value={
                    "released": [event.key for event in releases],
                    "keys_down": list(self.harness.bridge.keys_down),
                    "reason": reason,
                },
                source="keyboard_event_bridge",
            ),
        )

    def _consume_fault(self, node: ActionNode) -> str | None:
        target = str(node.parameters.get("target_key", ""))
        for selector in (node.node_id, target, node.kind.value):
            queue = self._faults.get(selector)
            if queue:
                return queue.popleft()
        return None

    async def _screen_frame(self, node: ActionNode) -> ScreenFrame:
        expected = str(node.parameters.get("expected_text", ""))
        mode = str(node.parameters.get("match_mode", "contains"))
        deadline = time.monotonic() + min(node.timeout_seconds * 0.8, 0.75)
        frame = self.harness.desktop.snapshot()
        while time.monotonic() < deadline:
            text = render_terminal_text(frame.text)
            matched = not expected or (
                (mode == "contains" and expected in text)
                or (mode == "suffix" and text.endswith(expected))
                or (mode == "exact" and text == expected)
            )
            if matched:
                break
            await asyncio.sleep(0.005)
            frame = self.harness.desktop.snapshot()
        return frame


async def run_p2_smoke(
    *,
    root: str | Path,
    workspace: str | Path | None = None,
    manifest_path: str | Path | None = None,
) -> P2SmokeResult:
    project = Path(root).resolve()
    manifest = manifest_path or project / "configs/simulation/g1_29dof_5010_p1.json"
    temporary: tempfile.TemporaryDirectory[str] | None = None
    if workspace is None:
        temporary = tempfile.TemporaryDirectory(prefix="vskill-p2-")
        workspace = temporary.name
    catalog = ActionModuleCatalog.from_directory(
        project / "configs/action_graphs/modules"
    )
    compiler = KeyboardActionCompiler(catalog)
    planner = TypingPlanner()
    harness = HybridSimulationHarness(
        project_root=project,
        workspace=workspace,
        manifest_path=manifest,
    )
    adapter = P2SimulationActionAdapter(harness)
    runner = ActionGraphRunner(adapter=adapter, verifier=IndependentActionVerifier())
    graph_results = []
    final_frame = ScreenFrame(0, time.monotonic(), False, False, "")
    repaired_text_observed = False
    chord_timeline_verified = False
    chord_trials = 200
    chord_successes = 0
    try:
        harness.start()
        single = compiler.compile_single_key(planner.plan_text("a").strokes[0])
        single_result = await runner.run(single)
        graph_results.append(single_result)

        chord_plan = planner.plan_chord_detailed(["ctrl", "alt", "t"])
        chord_event_start = len(harness.events)
        chord = compiler.compile_chord(chord_plan)
        chord_result = await runner.run(chord)
        graph_results.append(chord_result)
        chord_events = harness.events[chord_event_start:]
        chord_timeline_verified = _events_match_plan(chord_events, chord_plan.expected_events)
        chord_successes += int(
            chord_result.status is ActionStatus.SUCCEEDED
            and chord_timeline_verified
            and not harness.bridge.keys_down
        )
        for _ in range(chord_trials - 1):
            event_start = len(harness.events)
            stress_result = await runner.run(chord)
            stress_events = harness.events[event_start:]
            if (
                stress_result.status is ActionStatus.SUCCEEDED
                and _events_match_plan(stress_events, chord_plan.expected_events)
                and not harness.bridge.keys_down
            ):
                chord_successes += 1

        typo = planner.plan_text("codwx")
        typo_result = await runner.run(compiler.compile_text(typo))
        graph_results.append(typo_result)

        repair = planner.repair(expected="codex", observed="codwx")
        repair_result = await runner.run(compiler.compile_repair(repair))
        graph_results.append(repair_result)
        repaired_text_observed = render_terminal_text(
            harness.desktop.snapshot().text
        ).endswith("codex")

        clear = planner.repair(expected="", observed="codex")
        clear_result = await runner.run(compiler.compile_repair(clear))
        graph_results.append(clear_result)

        command = planner.plan_text("printf p2-ready\n")
        text_result = await runner.run(
            compiler.compile_text(
                command,
                expected_screen_text="p2-ready",
                match_mode="contains",
            )
        )
        graph_results.append(text_result)
        final_frame = harness.desktop.snapshot()
        screen_text = render_terminal_text(final_frame.text)
        output_observed = screen_text.count("p2-ready") >= 2
        terminal_visible = final_frame.visible
        terminal_focused = final_frame.focused
    finally:
        harness.stop()
        keys_down = harness.bridge.keys_down
        if temporary is not None:
            temporary.cleanup()

    event_counts = Counter(event.event_type.value for event in harness.events)
    balanced = event_counts[KeyEventType.KEYDOWN.value] == event_counts[KeyEventType.KEYUP.value]
    action_counts = Counter(
        node.kind.value for result in graph_results for node in result.node_results
    )
    statuses = [result.status for result in graph_results]
    single_ok = statuses[0] is ActionStatus.SUCCEEDED
    chord_ok = statuses[1] is ActionStatus.SUCCEEDED
    typo_ok = statuses[2] is ActionStatus.SUCCEEDED
    repair_ok = statuses[3] is ActionStatus.SUCCEEDED
    text_ok = statuses[-1] is ActionStatus.SUCCEEDED
    return P2SmokeResult(
        succeeded=(
            all(status is ActionStatus.SUCCEEDED for status in statuses)
            and chord_timeline_verified
            and chord_successes / chord_trials >= 0.98
            and repaired_text_observed
            and output_observed
            and terminal_visible
            and terminal_focused
            and balanced
            and not keys_down
        ),
        action_graph_contract="1.0.0",
        module_versions=catalog.versions,
        single_key_succeeded=single_ok,
        chord_succeeded=chord_ok,
        chord_event_timeline_verified=chord_timeline_verified,
        chord_trials=chord_trials,
        chord_successes=chord_successes,
        chord_success_rate=chord_successes / chord_trials,
        text_succeeded=typo_ok and text_ok,
        repair_succeeded=repair_ok,
        repaired_text_observed=repaired_text_observed,
        output_observed=output_observed,
        terminal_visible=terminal_visible,
        terminal_focused=terminal_focused,
        keys_down_after_stop=keys_down,
        event_counts=dict(event_counts),
        action_counts=dict(action_counts),
        graph_summaries=tuple(
            {
                "name": result.graph_name,
                "version": result.graph_version,
                "status": result.status.value,
                "completed_actions": len(result.completed_nodes),
                "failure_node": result.failure_node,
                "failure_code": result.failure_code,
            }
            for result in graph_results
        ),
        screen_text=render_terminal_text(final_frame.text),
    )


def _event_evidence(event: KeyEvent) -> Evidence:
    return Evidence(
        domain=EvidenceDomain.PHYSICAL,
        kind="key_event",
        value={
            "event_type": event.event_type.value,
            "key": event.key,
            "modifiers": list(event.modifiers),
            "reason": event.reason,
            "event_id": event.event_id,
        },
        source=event.source,
    )


def _adjacent_key(key: str) -> str:
    return {"t": "y", "y": "t", "left_alt": "space", "right_alt": "space"}.get(
        key, "x"
    )


def _events_match_plan(events, expected) -> bool:
    return [
        (event.event_type.value, event.key, tuple(event.modifiers)) for event in events
    ] == [
        (item.event_type.value, item.key, tuple(item.modifiers)) for item in expected
    ]
