"""Untrained contract demonstration, deliberately opt-in and separately labeled."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
import json
from pathlib import Path

from ..domain import SkillRequest, StateSnapshot
from ..ledger import EventLedger
from ..recovery import build_default_recovery_manager
from ..registry import SkillRegistry
from ..runtime import SkillRuntime
from .model import Trace
from .proxy import ProxyExecutor, ProxyObserver, ProxyWorld
from .skill import ChairRepositionSkill, build_chair_verifier
from .verification import ChairVerifier


async def run_demo(output: Path, fault: str = "none") -> dict[str, object]:
    output.mkdir(parents=True, exist_ok=False)
    world = ProxyWorld(fault)
    observer = ProxyObserver(world)
    registry = SkillRegistry()
    registry.register(ChairRepositionSkill(ProxyExecutor(world), observer))
    ledger = EventLedger(output / "ledger.sqlite")
    runtime = SkillRuntime(registry=registry,
                           verifier=build_chair_verifier(observer.source, "contract_proxy"),
                           ledger=ledger, recovery=build_default_recovery_manager())
    # Explicit synthetic initial conditions, never live/hardware evidence.
    state = StateSnapshot(facts={"robot_safe", "seated_stable", "work_pose_ready", "keyboard_calibrated"})
    try:
        result = await runtime.execute(SkillRequest(
            task_id="chair-contract-demo", skill="reposition_while_seated",
            arguments={"vx_mps": 0.12, "vy_mps": 0.04, "yaw_rate_rps": 0.10, "duration_s": 2.0},
        ), state)
        trace = Trace.from_dict(result.evidence[0].value)
        verdict = ChairVerifier(observer_source=observer.source).verify(trace)
        report = {
            "schema": "chair-demo-v1", "evidence_level": "contract_proxy",
            "description": "Command-driven kinematic proxy; no robot dynamics or learned policy.",
            "physical_success_verified": False, "trained_policy": False,
            "fault": fault, "safe_hold_requested": world.stopped,
            "result": result.to_dict(), "verdict": asdict(verdict),
            "final_facts": sorted(state.facts),
        }
        (output / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
        with (output / "trace.jsonl").open("w") as stream:
            for sample in trace.samples:
                stream.write(json.dumps(asdict(sample), allow_nan=False) + "\n")
        return report
    finally:
        ledger.close()


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("artifacts/chair/demo"))
    parser.add_argument("--fault", default="none", choices=("none", "stationary", "seat_loss", "wrong_way", "chair_stuck", "stale", "work_pose"))
    args = parser.parse_args()
    report = asyncio.run(run_demo(args.output, args.fault))
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if report["result"]["failure_code"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
