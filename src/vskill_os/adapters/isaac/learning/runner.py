"""Bounded physics worker and independent acceptance, outside Isaac's lifecycle.

Kit's shutdown can return zero after a Python failure. This parent checks the
exact run directory, all expected cases, raw bundles and reset tolerances before
returning success. Neither worker status nor collection.json is task evidence.
"""

from __future__ import annotations

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

from ....learning.contracts import EpisodeRecord
from ....learning.physical import PhysicsTrace
from .config import SingleHandConfig
from .single_hand_env import ROOT, write_json
from .verify import verify_bundle

CASE_EXPECTATIONS = {
    "press_1": (True, ()),
    "press_2": (True, ()),
    "miss": (False, ("PRIMITIVE_MISMATCH",)),
    "stuck": (False, ("STUCK_KEY",)),
    "timeout_release": (False, ("ADMINISTRATIVE_RELEASE", "STUCK_KEY")),
    "press_after_reset": (True, ()),
    "wrong_mapping": (False, ("JOINT_MAPPING",)),
    "stale_owner": (False, ("CONTROL_OWNERSHIP",)),
    "expired_action": (False, ("ACTION_EXPIRED",)),
    "time_limit": (False, ("INCOMPLETE_EPISODE",)),
}


def verify_run(directory: Path, *, config: SingleHandConfig, process: dict) -> dict:
    report = {
        "schema_version": "vf-single-hand-suite/1", "accepted": False,
        "capability": "isaac_physx_single_hand_v1", "num_envs": 1,
        "learned_policy": False, "camera_verified": False, "desktop_verified": False,
        "recording": str(directory.resolve()), "process": process, "cases": [], "errors": [],
    }
    try:
        if process.get("returncode") != 0 or process.get("timed_out") is not False:
            raise ValueError("worker did not exit normally within its deadline")
        if (directory / "failure.json").exists():
            raise ValueError("worker preserved failure.json; exit status alone cannot prove success")
        collection = json.loads((directory / "collection.json").read_text())
        if [case["case"] for case in collection["cases"]] != list(CASE_EXPECTATIONS):
            raise ValueError("collection is incomplete or is not the frozen diagnostic suite")
        traces, episodes = {}, {}
        for name, (expected, required_codes) in CASE_EXPECTATIONS.items():
            result = verify_bundle(directory / name, expected_config=config)
            codes = {finding["code"] for finding in result["findings"]}
            matched = ("consistent" in result and "INVALID_BUNDLE" not in codes
                       and result["physical_primitive_passed"] == expected and set(required_codes) <= codes)
            result.update({"case": name, "expected_pass": expected, "expectation_matched": matched})
            write_json(directory / name / "evaluation.json", result)
            report["cases"].append(result)
            if not matched:
                report["errors"].append(f"unexpected evaluation: {name}")
            traces[name] = PhysicsTrace.read(directory / name / "trace.json")
            episodes[name] = EpisodeRecord.read(directory / name / "episode.json")
            cleanup = json.loads((directory / name / "cleanup.json").read_text())["cleanup"]
            if cleanup["physical_release_claimed"] is not False or cleanup["episode_id"] != name:
                raise ValueError(f"invalid administrative cleanup: {name}")
        tokens = [trace.owner_token for trace in traces.values()]
        if len(set(tokens)) != len(tokens):
            raise ValueError("reset reused control ownership")
        reference = traces["press_1"].samples[0]
        reset_comparison = []
        # Fixed tolerances, defined before acceptance; compare physical values,
        # not per-run UUIDs or wall-clock filenames.
        for name in ("press_2", "press_after_reset"):
            sample = traces[name].samples[0]
            position_error = max(abs(a-b) for a, b in zip(reference.joint_positions, sample.joint_positions, strict=True))
            velocity_error = max(abs(a-b) for a, b in zip(reference.joint_velocities, sample.joint_velocities, strict=True))
            tip_error = math.dist(reference.fingertip_xyz_m, sample.fingertip_xyz_m)
            event_error = max(abs(a.stamp.capture_time_s-b.stamp.capture_time_s)
                              for a, b in zip(episodes["press_1"].events, episodes[name].events, strict=True))
            reset_comparison.append({"case": name, "maximum_joint_position_error_rad": position_error,
                "maximum_joint_velocity_error_rad_s": velocity_error, "tip_error_m": tip_error,
                "maximum_event_time_error_s": event_error,
                "passed": position_error <= 1e-4 and velocity_error <= 1e-3 and tip_error <= 1e-4
                          and event_error <= config.physics_dt_s + 1e-9})
        report["reset_comparison"] = reset_comparison
        report["reset_tolerances"] = {"joint_position_rad": 1e-4, "joint_velocity_rad_s": 1e-3,
                                       "tip_position_m": 1e-4, "event_time_s": config.physics_dt_s}
        if not all(item["passed"] for item in reset_comparison):
            report["errors"].append("reset or event repeatability exceeded the frozen tolerance")
        for name in ("wrong_mapping", "stale_owner", "expired_action"):
            if len(traces[name].samples) != 1 or episodes[name].end_time_s != 0.0 or episodes[name].termination != "safety_stop":
                report["errors"].append(f"invalid action advanced physics: {name}")
        if episodes["time_limit"].termination != "time_limit" or abs(episodes["time_limit"].end_time_s-.05) > 1e-9:
            report["errors"].append("horizon truncation lost the last physical state")
        report["accepted"] = not report["errors"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        report["errors"].append(str(exc))
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts/verification_feedback/vf01/physics")
    parser.add_argument("--config", type=Path, default=ROOT / "configs/learning/verification_feedback/single_hand_physics_v1.json")
    parser.add_argument("--timeout-s", type=float, default=600.)
    parser.add_argument("--verify-run", type=Path, help="recheck an existing run without starting Isaac")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    if not math.isfinite(args.timeout_s) or args.timeout_s <= 0:
        parser.error("timeout must be positive and finite")
    config = SingleHandConfig.read(args.config)
    if args.verify_run:
        directory = args.verify_run.resolve()
        process = json.loads((directory / "process.json").read_text())
    else:
        run_id = datetime.now(timezone.utc).strftime("run-%Y%m%dT%H%M%S-%f")
        args.output_dir.mkdir(parents=True, exist_ok=True)
        directory = (args.output_dir / run_id).resolve()
        log_path = args.output_dir / f"{run_id}.log"
        command = [sys.executable, "-m", "vskill_os.adapters.isaac.learning.smoke", "--case", "suite",
                   "--output-dir", str(args.output_dir.resolve()), "--run-id", run_id, "--config", str(args.config.resolve())]
        print(f"Collecting single-hand physics: {directory}\nWorker log: {log_path}", flush=True)
        start = time.perf_counter()
        process = {"timed_out": False, "returncode": None, "log": str(log_path.resolve())}
        with log_path.open("w") as log:
            child = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            try:
                process["returncode"] = child.wait(timeout=args.timeout_s)
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
        process["wall_time_s"] = time.perf_counter()-start
        write_json(directory / "process.json", process)
    report = verify_run(directory, config=config, process=process)
    write_json(directory / "suite.json", report)
    if args.report:
        write_json(args.report, report)
    for case in report["cases"]:
        print(f"{case['case']}: primitive={case['physical_primitive_passed']}, expected={case['expectation_matched']}")
    print(json.dumps({"accepted": report["accepted"], "errors": report["errors"], "report": str(directory / "suite.json")}))
    return 0 if report["accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
