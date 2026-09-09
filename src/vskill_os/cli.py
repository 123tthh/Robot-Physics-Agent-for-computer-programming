"""Command-line interface for simulation, inspection, and benchmarking."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .action_graph import ActionModuleCatalog, KeyboardActionCompiler
from .benchmark import BenchmarkConfig, run_benchmark
from .config import load_json
from .ledger import EventLedger
from .research import ReferenceCatalog
from .robot.g1 import (
    G1DeploymentConfig,
    G1Joint,
    G1PolicyManifest,
    G1_5010_PROFILES,
)
from .robot.seated_policy import SeatedLocomotionPolicyManifest
from .simulation.app import build_simulation_app, project_root
from .simulation.assets import G1SimulationManifest
from .simulation.dex3_assets import (
    compose_mode15_g1_with_dex3,
    validate_g1_dex3_urdf,
    validate_g1_dex3_usd,
)
from .simulation.hybrid import run_hybrid_smoke
from .simulation.p2 import run_p2_smoke
from .typing import TypingPlanner
from .voice import AgentToolPolicy, RuleBasedToolPlanner, VoiceCommandPipeline


def build_parser() -> argparse.ArgumentParser:
    root = project_root()
    parser = argparse.ArgumentParser(prog="vskill", description="V-Skill OS simulator")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="run one configured simulation task")
    run.add_argument(
        "--scenario",
        type=Path,
        default=root / "configs/scenarios/software_recovery.json",
    )
    run.add_argument(
        "--workflow",
        type=Path,
        default=root / "configs/workflows/create_hello.json",
    )
    run.add_argument("--ledger", type=Path, default=None)
    run.add_argument("--workspace", type=Path, default=None)
    run.add_argument("--task-id", default=None)
    run.add_argument("--resume", action="store_true")
    run.add_argument("--stop-after-nodes", type=int, default=None)
    run.add_argument("--output", type=Path, default=None)

    benchmark = subparsers.add_parser("benchmark", help="run fault-injection episodes")
    benchmark.add_argument("--episodes", type=int, default=20)
    benchmark.add_argument("--seed", type=int, default=7)
    benchmark.add_argument(
        "--profile",
        choices=("nominal", "recovery", "stress"),
        default="recovery",
    )
    benchmark.add_argument("--output", type=Path, default=None)

    skills = subparsers.add_parser("list-skills", help="list registered simulation skills")
    skills.add_argument(
        "--scenario",
        type=Path,
        default=root / "configs/scenarios/nominal.json",
    )

    events = subparsers.add_parser("events", help="inspect a persisted task ledger")
    events.add_argument("--ledger", type=Path, required=True)
    events.add_argument("--task-id", required=True)

    robot = subparsers.add_parser(
        "inspect-robot", help="inspect G1 29-DoF/5010 compatibility profiles"
    )
    robot.add_argument(
        "--config",
        type=Path,
        default=root / "configs/robots/g1_29dof_5010.json",
    )
    robot.add_argument("--policy-manifest", type=Path, default=None)

    projects = subparsers.add_parser(
        "list-projects", help="list reviewed upstream integration candidates"
    )
    projects.add_argument(
        "--catalog",
        type=Path,
        default=root / "configs/research/projects.json",
    )
    projects.add_argument("--subsystem", default=None)

    voice = subparsers.add_parser(
        "voice-plan", help="run the deterministic local voice planning baseline"
    )
    voice.add_argument("transcript")

    hybrid = subparsers.add_parser(
        "hybrid-smoke", help="run the P1 keycap-to-PTY simulation smoke test"
    )
    hybrid.add_argument(
        "--manifest",
        type=Path,
        default=root / "configs/simulation/g1_29dof_5010_p1.json",
    )
    hybrid.add_argument("--workspace", type=Path, default=None)

    p2 = subparsers.add_parser(
        "p2-smoke",
        help="run the modular P2 single-key, chord, text, and repair acceptance",
    )
    p2.add_argument(
        "--manifest",
        type=Path,
        default=root / "configs/simulation/g1_29dof_5010_p1.json",
    )
    p2.add_argument("--workspace", type=Path, default=None)
    p2.add_argument("--output", type=Path, default=None)

    action_graph = subparsers.add_parser(
        "inspect-action-graph",
        help="compile and inspect the versioned Ctrl+Alt+T ActionGraph",
    )
    action_graph.add_argument(
        "--modules",
        type=Path,
        default=root / "configs/action_graphs/modules",
    )
    action_graph.add_argument("--output", type=Path, default=None)

    assets = subparsers.add_parser(
        "inspect-sim-assets", help="validate the pinned P1 simulation manifest"
    )
    assets.add_argument(
        "--manifest",
        type=Path,
        default=root / "configs/simulation/g1_29dof_5010_p1.json",
    )

    dex3_assets = subparsers.add_parser(
        "inspect-dex3-assets",
        help="compose and validate collected G1 mode-15 + Dex3 assets",
    )
    dex3_assets.add_argument(
        "--unitree-ros-root",
        type=Path,
        default=root / "artifacts/simulation-assets/unitree_ros_7d6075f7/robots",
    )
    dex3_assets.add_argument(
        "--usd",
        type=Path,
        default=(
            root
            / "artifacts/simulation-assets/unitree_sim_isaaclab_usds_394cf244"
            / "robots/g1-29dof-dex3-base-fix-usd/g1_29dof_with_dex3_base_fix.usd"
        ),
    )
    dex3_assets.add_argument(
        "--target-usd",
        type=Path,
        default=(
            root
            / "artifacts/isaac-p1/assets/g1_mode15_dex3_p1/g1_mode15_dex3_p1.usd"
        ),
    )
    dex3_assets.add_argument("--require-pxr", action="store_true")
    return parser


async def run_command(args: argparse.Namespace) -> int:
    task_id = args.task_id or f"run-{uuid4().hex[:12]}"
    artifacts = project_root() / "artifacts"
    ledger_path = args.ledger or artifacts / f"{task_id}.db"
    workspace = args.workspace or artifacts / "workspaces" / task_id
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    workspace.mkdir(parents=True, exist_ok=True)

    app = build_simulation_app(
        scenario_path=args.scenario,
        workflow_path=args.workflow,
        ledger_path=ledger_path,
        workspace=workspace,
    )
    try:
        result = await app.run(
            task_id=task_id,
            resume=args.resume,
            stop_after_nodes=args.stop_after_nodes,
        )
        payload = result.to_dict()
        payload["scenario"] = app.scenario.name
        payload["ledger"] = str(ledger_path)
        payload["workspace"] = str(workspace)
        payload["generated_at"] = datetime.now(timezone.utc).isoformat()
        payload["control_stack"] = app.world.control.snapshot()
    finally:
        app.close()

    _emit_json(payload, args.output)
    return 0 if payload["status"] == "succeeded" else 2


async def benchmark_command(args: argparse.Namespace) -> int:
    report = await run_benchmark(
        BenchmarkConfig(episodes=args.episodes, seed=args.seed, profile=args.profile)
    )
    _emit_json(report, args.output)
    return 0


def list_skills_command(args: argparse.Namespace) -> int:
    app = build_simulation_app(scenario_path=args.scenario)
    try:
        print(json.dumps(app.runtime.registry.contracts(), ensure_ascii=False, indent=2))
    finally:
        app.close()
    return 0


def events_command(args: argparse.Namespace) -> int:
    with EventLedger(args.ledger) as ledger:
        events = [
            {
                "event_id": event.event_id,
                "task_id": event.task_id,
                "call_id": event.call_id,
                "kind": event.kind,
                "payload": event.payload,
                "created_at": event.created_at,
            }
            for event in ledger.list_events(args.task_id)
        ]
    print(json.dumps(events, ensure_ascii=False, indent=2))
    return 0


def inspect_robot_command(args: argparse.Namespace) -> int:
    config = G1DeploymentConfig.from_json(args.config)
    payload = {
        "configured_hardware_mode": config.selected_mode_machine,
        "simulation_mode": config.simulation_mode_machine,
        "hardware_confirmation_required": config.require_hardware_confirmation,
        "joint_order": [joint.name.lower() for joint in G1Joint],
        "supported_5010_profiles": [
            {
                "mode_machine": profile.mode_machine,
                "asset": profile.description_asset,
                "hip_gear_ratio": [
                    profile.hip_pitch_gear_ratio,
                    profile.hip_roll_gear_ratio,
                ],
                "waist_locked": profile.waist_locked,
                "compatibility_key": profile.compatibility_key,
            }
            for profile in G1_5010_PROFILES.values()
        ],
    }
    if args.policy_manifest is not None:
        raw_manifest = load_json(args.policy_manifest)
        manifest = G1PolicyManifest.from_dict(raw_manifest)
        manifest.validate_for(config.simulation_profile())
        payload["policy_manifest"] = str(args.policy_manifest)
        payload["policy_compatible_with_simulation"] = True
        if "seated_locomotion" in raw_manifest:
            seated = SeatedLocomotionPolicyManifest.from_dict(raw_manifest)
            seated.validate_for(config.simulation_profile())
            payload["seated_locomotion"] = {
                "actor_dimension": seated.actor_dimension,
                "critic_dimension": seated.critic_dimension,
                "training_risk_flags": list(seated.training_risk_flags),
            }
    _emit_json(payload, None)
    return 0


def list_projects_command(args: argparse.Namespace) -> int:
    catalog = ReferenceCatalog.from_json(args.catalog)
    projects = (
        catalog.projects
        if args.subsystem is None
        else catalog.for_subsystem(args.subsystem)
    )
    _emit_json(
        [
            {
                "name": project.name,
                "subsystem": project.subsystem,
                "maturity": project.maturity,
                "integration": project.integration,
                "url": project.url,
                "use_for": list(project.use_for),
                "constraints": list(project.constraints),
            }
            for project in projects
        ],
        None,
    )
    return 0


async def voice_plan_command(args: argparse.Namespace) -> int:
    policy = AgentToolPolicy(
        frozenset(
            {
                "prepare_computer",
                "ask_code_agent",
                "verify_software_task",
                "resume_interrupted_task",
                "execute_verified_goal",
            }
        )
    )
    pipeline = VoiceCommandPipeline(
        asr=_UnusedASR(), planner=RuleBasedToolPlanner(), policy=policy
    )
    plan = await pipeline.handle_transcript(args.transcript)
    _emit_json(
        {
            "transcript": plan.transcript,
            "accepted": plan.accepted,
            "calls": [
                {"skill": call.skill, "arguments": dict(call.arguments)}
                for call in plan.calls
            ],
            "rejected_calls": [call.skill for call in plan.rejected_calls],
        },
        None,
    )
    return 0


def hybrid_smoke_command(args: argparse.Namespace) -> int:
    result = run_hybrid_smoke(
        root=project_root(),
        workspace=args.workspace,
        manifest_path=args.manifest,
    )
    _emit_json(result.to_dict(), None)
    return 0 if result.succeeded else 2


async def p2_smoke_command(args: argparse.Namespace) -> int:
    result = await run_p2_smoke(
        root=project_root(),
        workspace=args.workspace,
        manifest_path=args.manifest,
    )
    _emit_json(result.to_dict(), args.output)
    return 0 if result.succeeded else 2


def inspect_action_graph_command(args: argparse.Namespace) -> int:
    catalog = ActionModuleCatalog.from_directory(args.modules)
    plan = TypingPlanner().plan_chord_detailed(["ctrl", "alt", "t"])
    graph = KeyboardActionCompiler(catalog).compile_chord(plan)
    _emit_json(graph.to_dict(), args.output)
    return 0


def inspect_sim_assets_command(args: argparse.Namespace) -> int:
    manifest = G1SimulationManifest.from_json(args.manifest)
    root = project_root()
    manifest.validate(root=root)
    capability = manifest.capability()
    report_path = capability.get("acceptance_report")
    if report_path:
        manifest.verify_acceptance_report(root / str(report_path))
        capability["acceptance_evidence"] = "verified"
    _emit_json(capability, None)
    return 0


def inspect_dex3_assets_command(args: argparse.Namespace) -> int:
    description = args.unitree_ros_root / "g1_description"
    hand = args.unitree_ros_root / "dex3_1"
    target = description / "g1_29dof_mode_15_with_dex3_1.urdf"
    composite = compose_mode15_g1_with_dex3(
        g1_urdf=description / "g1_29dof_mode_15.urdf",
        left_hand_urdf=hand / "dex3_1_l.urdf",
        right_hand_urdf=hand / "dex3_1_r.urdf",
        output=target,
    )
    legacy = validate_g1_dex3_urdf(
        description / "g1_29dof_with_hand_rev_1_0.urdf"
    )
    usd = validate_g1_dex3_usd(
        args.usd,
        expected_sha256="d23b9bbc8b59cf20fa1983f55874f855a76e038b9e22eae39542e62be5b4ba3d",
        require_pxr=args.require_pxr,
        require_positive_masses=False,
    )
    target_config = load_json(
        project_root() / "configs/simulation/g1_29dof_5010_dex3_assets.json"
    )["usd_target"]
    target_usd = validate_g1_dex3_usd(
        args.target_usd,
        expected_sha256=str(target_config["sha256"]),
        require_pxr=args.require_pxr,
    )
    _emit_json(
        {
            "target_composite_urdf": composite.to_dict(),
            "target_composite_usd": {
                **target_usd.to_dict(),
                "compatible_with_mode15_5010": True,
            },
            "official_legacy_composite_urdf": {
                **legacy.to_dict(),
                "compatible_with_mode15_5010": False,
                "reason": "official README identifies it as mode 5 with 4010 wrists",
            },
            "official_fixed_base_usd_reference": {
                **usd.to_dict(),
                "file_integrity_validated": True,
                "joint_schema_validated": usd.pxr_parsed,
                "dynamics_compatible_with_mode15_5010": False,
                "reason": "default prim derives from the legacy mode-5 composite",
            },
        },
        None,
    )
    return 0


class _UnusedASR:
    async def transcribe(self, audio: bytes) -> str:
        del audio
        raise RuntimeError("voice-plan accepts an existing transcript")


def _emit_json(payload: object, output: Path | None) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if output is None:
        print(text)
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text + "\n", encoding="utf-8")
    print(output)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "run":
        return asyncio.run(run_command(args))
    if args.command == "benchmark":
        return asyncio.run(benchmark_command(args))
    if args.command == "list-skills":
        return list_skills_command(args)
    if args.command == "events":
        return events_command(args)
    if args.command == "inspect-robot":
        return inspect_robot_command(args)
    if args.command == "list-projects":
        return list_projects_command(args)
    if args.command == "voice-plan":
        return asyncio.run(voice_plan_command(args))
    if args.command == "hybrid-smoke":
        return hybrid_smoke_command(args)
    if args.command == "p2-smoke":
        return asyncio.run(p2_smoke_command(args))
    if args.command == "inspect-action-graph":
        return inspect_action_graph_command(args)
    if args.command == "inspect-sim-assets":
        return inspect_sim_assets_command(args)
    if args.command == "inspect-dex3-assets":
        return inspect_dex3_assets_command(args)
    raise RuntimeError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
