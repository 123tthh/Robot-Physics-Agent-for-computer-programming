"""Command-line interface for simulation, inspection, and benchmarking."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .benchmark import BenchmarkConfig, run_benchmark
from .ledger import EventLedger
from .simulation.app import build_simulation_app, project_root


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
    raise RuntimeError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
