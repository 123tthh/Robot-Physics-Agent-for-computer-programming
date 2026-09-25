"""CPU-only export and independent re-evaluation of VF-01 evidence records."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .contracts import EpisodeRecord, TaskSpec
from .evaluation import EvaluationPolicy, IndependentTypingEvaluator
from .replay import CASE_EXPECTATIONS, build_replay_case


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


def run_demo(output: Path, policy: EvaluationPolicy) -> dict:
    evaluator = IndependentTypingEvaluator(policy)
    summaries = []
    policy.write(output / "policy.json")
    for name in CASE_EXPECTATIONS:
        case = build_replay_case(name)
        destination = output / name
        case.task.write(destination / "task.json")
        case.episode.write(destination / "episode.json")
        # Evaluate the exported data, exercising the same import path as users.
        result = evaluator.evaluate(TaskSpec.read(destination / "task.json"),
                                    EpisodeRecord.read(destination / "episode.json"))
        result.write(destination / "evaluation.json")
        codes = sorted({finding.code for finding in result.findings})
        summaries.append({
            "case": name, "expected_contract_pass": case.expected_pass,
            "contract_passed": result.contract_passed, "failures": codes,
            "expectation_met": (result.contract_passed == case.expected_pass
                                and set(case.required_failures) <= set(codes)),
            "episode_sha256": result.episode_sha256,
        })
    report = {
        "task": "VF-01-T01", "capability": "contract_replay",
        "physical_execution_verified": False,
        "evaluator_sha256": evaluator.code_sha256,
        "policy_sha256": policy.sha256(),
        "all_expectations_met": all(row["expectation_met"] for row in summaries),
        "cases": summaries,
    }
    _write_json(output / "report.json", report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="VF-01 offline contract evidence, not a physics/training environment")
    commands = parser.add_subparsers(dest="command", required=True)
    demo = commands.add_parser("demo", help="export and check positive and negative synthetic records")
    demo.add_argument("--output", type=Path, default=Path("artifacts/verification_feedback/vf01/contracts"))
    demo.add_argument("--policy", type=Path)
    evaluate = commands.add_parser("evaluate", help="independently check a saved episode against a separate task")
    evaluate.add_argument("--task", type=Path, required=True)
    evaluate.add_argument("--episode", type=Path, required=True)
    evaluate.add_argument("--policy", type=Path)
    evaluate.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        policy = EvaluationPolicy.read(args.policy) if args.policy else EvaluationPolicy()
        if args.command == "demo":
            report = run_demo(args.output, policy)
            print(json.dumps(report, indent=2, ensure_ascii=False))
            return 0 if report["all_expectations_met"] else 1
        result = IndependentTypingEvaluator(policy).evaluate(TaskSpec.read(args.task), EpisodeRecord.read(args.episode))
        if args.output:
            result.write(args.output)
        print(result.to_json(), end="")
        return 0 if result.contract_passed else 1
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc), "contract_passed": False}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
