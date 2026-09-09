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
from ..robot.g1 import G1DeploymentConfig
from ..runtime import SkillRuntime, StateValidityPolicy
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
    robot_config_path: str | Path | None = None,
) -> SimulationApp:
    root = project_root()
    scenario_file = Path(scenario_path) if scenario_path else root / "configs/scenarios/software_recovery.json"
    workflow_file = Path(workflow_path) if workflow_path else root / "configs/workflows/create_hello.json"
    robot_config_file = (
        Path(robot_config_path)
        if robot_config_path
        else root / "configs/robots/g1_29dof_5010.json"
    )
    scenario = SimulationScenario.from_json(scenario_file)
    robot_config = G1DeploymentConfig.from_json(robot_config_file)
    world = SimulatedWorld(
        scenario,
        workspace=workspace,
        robot_profile=robot_config.simulation_profile(),
    )
    graph = load_skill_graph(workflow_file)
    ledger = EventLedger(ledger_path)
    registry = build_simulation_registry(world)
    runtime = SkillRuntime(
        registry=registry,
        verifier=build_simulation_verifier(),
        ledger=ledger,
        recovery=build_default_recovery_manager(),
        state_validity={
            "keyboard_calibrated": StateValidityPolicy(
                ttl_seconds=30.0,
                dependencies=("work_pose_ready",),
            ),
            "work_pose_ready": StateValidityPolicy(
                ttl_seconds=2.0,
                dependencies=("seated_stable", "screen_visible", "hands_reachable"),
            ),
            "terminal_focused": StateValidityPolicy(ttl_seconds=5.0),
            "terminal_ready": StateValidityPolicy(
                ttl_seconds=2.0,
                dependencies=(
                    "terminal_visible",
                    "terminal_focused",
                    "fingers_released",
                    "work_pose_ready",
                ),
            ),
        },
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
            "g1_profile": world.control.profile.compatibility_key,
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
