"""Composition root for a complete simulation run."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..config import load_skill_graph
from ..domain import StateSnapshot
from ..graph import SkillGraph
from ..graph_executor import GraphRunResult, GraphSupervisor
from ..ledger import EventLedger
from ..recovery import build_default_recovery_manager
from ..runtime import SkillRuntime
from ..skills.simulation import build_simulation_registry
from ..verifier import RuleBasedVerifier, build_default_verifier
from .environment import SimulationScenario, SimulatedWorld


def project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def build_simulation_verifier() -> RuleBasedVerifier:
    verifier = build_default_verifier()
    verifier.register(
        "work_pose_ready",
        lambda state, _: state.has_all(
            {"seated_stable", "screen_visible", "hands_reachable"}
        ),
    )
    verifier.register(
        "task_reported",
        lambda state, _: "test_passed" in state.facts,
    )
    return verifier


@dataclass(slots=True)
class SimulationApp:
    scenario: SimulationScenario
    world: SimulatedWorld
    graph: SkillGraph
    ledger: EventLedger
    runtime: SkillRuntime
    supervisor: GraphSupervisor
    initial_state: StateSnapshot

    async def run(
        self,
        *,
        task_id: str,
        resume: bool = False,
        stop_after_nodes: int | None = None,
    ) -> GraphRunResult:
        return await self.supervisor.run(
            task_id=task_id,
            graph=self.graph,
            initial_state=self.initial_state.copy(),
            resume=resume,
            stop_after_nodes=stop_after_nodes,
        )

    def close(self) -> None:
        self.ledger.close()
        self.world.close()

    def __enter__(self) -> "SimulationApp":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def build_simulation_app(
    *,
    scenario_path: str | Path | None = None,
    workflow_path: str | Path | None = None,
    ledger_path: str | Path = ":memory:",
    workspace: str | Path | None = None,
) -> SimulationApp:
    root = project_root()
    scenario_file = Path(scenario_path) if scenario_path else root / "configs/scenarios/software_recovery.json"
    workflow_file = Path(workflow_path) if workflow_path else root / "configs/workflows/create_hello.json"
    scenario = SimulationScenario.from_json(scenario_file)
    world = SimulatedWorld(scenario, workspace=workspace)
    graph = load_skill_graph(workflow_file)
    ledger = EventLedger(ledger_path)
    registry = build_simulation_registry(world)
    runtime = SkillRuntime(
        registry=registry,
        verifier=build_simulation_verifier(),
        ledger=ledger,
        recovery=build_default_recovery_manager(),
    )
    supervisor = GraphSupervisor(
        runtime=runtime,
        ledger=ledger,
        max_recovery_attempts=scenario.max_recovery_attempts,
        resume_hook=world.synchronize_from_verified_state,
    )
    state = StateSnapshot(
        facts={"task_received", "robot_safe"},
        values={
            "scenario": scenario.name,
            "expected_output": scenario.expected_output,
        },
    )
    return SimulationApp(
        scenario=scenario,
        world=world,
        graph=graph,
        ledger=ledger,
        runtime=runtime,
        supervisor=supervisor,
        initial_state=state,
    )
