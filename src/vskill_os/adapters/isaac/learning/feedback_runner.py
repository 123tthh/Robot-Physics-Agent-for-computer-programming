"""Bounded Isaac worker; independent text, visibility and physical acceptance."""

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from ....learning.feedback_demo import case_spec
from ....learning.feedback_session import FeedbackSpec
from .config import SingleHandConfig
from .feedback_smoke import CASES
from .feedback_verify import verify_feedback_bundle
from .single_hand_env import ROOT, write_json


def verify_feedback_run(directory: Path, *, config: SingleHandConfig, feedback_spec: FeedbackSpec, process: dict) -> dict:
    report = dict(schema_version="vf-feedback-suite/1", accepted=False, recording=str(directory.resolve()),
                  process=process, num_envs=1, camera_verified=False, desktop_verified=False, learned_policy=False,
                  readout_source="text_state_readout_v1", cases=[], errors=[])
    try:
        if process.get("returncode") != 0 or process.get("timed_out") is not False or (directory / "failure.json").exists():
            raise ValueError("worker failed, timed out or preserved failure.json")
        collection = json.loads((directory / "feedback_collection.json").read_text())
        if [item["case"] for item in collection["cases"]] != list(CASES):
            raise ValueError("physical feedback collection does not contain the frozen suite")
        for name in CASES:
            evaluation = verify_feedback_bundle(directory / name, expected=case_spec(name, feedback_spec), physics_config=config)
            expected_pass = name == "verified_after_wait"
            expected_failure = "TEXT_INCORRECT" if name in {"software_drop", "focus_lost", "false_positive_readout"} else "FINAL_FEEDBACK_UNCONFIRMED"
            matched = (evaluation["binding_passed"] and evaluation["isolation_passed"] and evaluation["physical_primitive_passed"]
                       and evaluation["end_to_end_passed"] == expected_pass
                       and (expected_pass or expected_failure in evaluation["feedback"]["task_failures"]))
            evaluation.update(case=name, expected_end_to_end_passed=expected_pass, acceptance_passed=matched)
            write_json(directory / name / "feedback_evaluation.json", evaluation)
            report["cases"].append(evaluation)
            if not matched:
                report["errors"].append(f"unexpected evaluation: {name}")
            cleanup = json.loads((directory / name / "cleanup.json").read_text())["cleanup"]
            if cleanup["physical_release_claimed"] is not False or cleanup["episode_id"] != name:
                raise ValueError(f"invalid administrative cleanup: {name}")
        report["accepted"] = not report["errors"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        report["errors"].append(str(exc))
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts/verification_feedback/vf01/feedback_physics")
    parser.add_argument("--config", type=Path, default=ROOT / "configs/learning/verification_feedback/single_hand_physics_v1.json")
    parser.add_argument("--feedback-config", type=Path, default=ROOT / "configs/learning/verification_feedback/text_feedback_v1.json")
    parser.add_argument("--timeout-s", type=float, default=600.)
    parser.add_argument("--verify-run", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    if not math.isfinite(args.timeout_s) or args.timeout_s <= 0:
        parser.error("timeout must be finite and positive")
    config, spec = SingleHandConfig.read(args.config), FeedbackSpec.read(args.feedback_config)
    if args.verify_run:
        directory = args.verify_run.resolve()
        process = json.loads((directory / "process.json").read_text())
    else:
        run_id = datetime.now(timezone.utc).strftime("run-%Y%m%dT%H%M%S-%f")
        directory = (args.output_dir / run_id).resolve()
        args.output_dir.mkdir(parents=True, exist_ok=True)
        log_path = args.output_dir / f"{run_id}.log"
        command = [sys.executable, "-m", "vskill_os.adapters.isaac.learning.feedback_smoke",
                   "--output-dir", str(args.output_dir.resolve()), "--run-id", run_id,
                   "--config", str(args.config.resolve()), "--feedback-config", str(args.feedback_config.resolve())]
        print(f"Collecting physical text/feedback cases: {directory}\nWorker log: {log_path}", flush=True)
        started = time.perf_counter()
        process = dict(timed_out=False, returncode=None, log=str(log_path.resolve()))
        with log_path.open("w") as log:
            child = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            try:
                child.wait(timeout=args.timeout_s)
            except subprocess.TimeoutExpired:
                process["timed_out"] = True
            finally:
                if child.poll() is None:
                    os.killpg(child.pid, signal.SIGTERM)
                    try:
                        child.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(child.pid, signal.SIGKILL)
                        child.wait(timeout=5)
                process["returncode"] = child.returncode
        process["wall_time_s"] = time.perf_counter()-started
        write_json(directory / "process.json", process)
    report = verify_feedback_run(directory, config=config, feedback_spec=spec, process=process)
    write_json(directory / "feedback_suite.json", report)
    if args.report:
        write_json(args.report, report)
    for case in report["cases"]:
        print(f"{case['case']}: isolation={case['isolation_passed']}, physics={case['physical_primitive_passed']}, accepted={case['acceptance_passed']}")
    print(json.dumps(dict(accepted=report["accepted"], errors=report["errors"], report=str(directory / "feedback_suite.json"))))
    return 0 if report["accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
