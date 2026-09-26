"""Bounded direct-joint diagnostic, not a learned policy or typing benchmark."""

from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import time
import traceback

from ....learning.environment import JointTargetAction, ResetSpec
from ....simulation.keyboard_bridge import KeycapConfig
from .config import SingleHandConfig


def collect_case(env, *, name: str, amplitude: float, hold_s: float, seed: int) -> dict:
    began = time.perf_counter()
    previous_owner = getattr(env, "owner_token", "uninitialized")
    observation, metadata = env.reset(ResetSpec(name, seed=seed, maximum_time_s=0.05 if name == "time_limit" else 3.0))
    initial = observation.to_dict()
    print("VF01_RESET " + json.dumps({"case": name, "tip_xyz": env.raw_samples[0].fingertip_xyz_m,
                                      "pelvis_xyz": env.raw_samples[0].pelvis_xyz_m}), flush=True)
    index = metadata.control_spec.joint_names.index("left_hand_index_1_joint")
    phases = ((amplitude, 0.9), (amplitude, hold_s))
    if "stuck" not in name and "timeout_release" not in name:
        phases += ((0.0, 0.9), (0.0, 0.15))
    sequence = 0
    if name in {"wrong_mapping", "stale_owner", "expired_action"}:
        action = JointTargetAction(metadata.control_spec.joint_names, (0.0,)*len(metadata.control_spec.joint_names),
                                   metadata.control_spec.control_dt_s, metadata.control_spec.control_dt_s, metadata.owner_token, 0)
        if name == "wrong_mapping":
            action = replace(action, joint_names=tuple(reversed(action.joint_names)))
        elif name == "stale_owner":
            action = replace(action, owner_token=previous_owner)
        else:
            action = replace(action, valid_until_s=0.0)
        step = env.step(action)
        observation = step.observation
        if not step.terminated or step.truncated or step.record_refs or observation.time_s != 0.0:
            raise RuntimeError("invalid action advanced physics or lost its terminal observation")
        phases = ()
    for desired, duration in phases:
        for _ in range(round(duration / metadata.control_spec.control_dt_s)):
            deltas = [0.0] * len(metadata.control_spec.joint_names)
            deltas[index] = desired - observation.joints[index].commanded_target_rad
            action = JointTargetAction(metadata.control_spec.joint_names, tuple(deltas), metadata.control_spec.control_dt_s,
                                       observation.time_s + metadata.control_spec.control_dt_s, metadata.owner_token, sequence)
            step = env.step(action)
            observation = step.observation
            sequence += 1
            if step.terminated or step.truncated:
                break
        if not env.running:
            break
    env.finish()
    summary = {
        "case": name, "recording": str(env.episode_dir), "initial_observation": initial,
        "termination": env.termination, "safety_failures": env.failures,
        "events": [{"key": e.key, "type": e.event_type, "reason": e.reason, "time_s": e.stamp.capture_time_s} for e in env.events],
        "text_oracle": env.text, "maximum_travel_m": max(k.travel_m for frame in env.observations for k in frame.keys),
        "maximum_force_n": max(k.normal_force_n for frame in env.observations for k in frame.keys),
        "elapsed_sim_s": observation.time_s, "collected": True,
        "collection_wall_s": time.perf_counter() - began,
    }
    print("VF01_COLLECTED " + json.dumps({k: v for k, v in summary.items() if k != "initial_observation"}), flush=True)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/verification_feedback/vf01/physics"))
    parser.add_argument("--case", choices=("press", "miss", "stuck", "timeout_release", "suite"), default="press")
    parser.add_argument("--amplitude", type=float, default=0.26)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--headless", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--key-height", type=float, default=0.791)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--run-id")
    args = parser.parse_args(argv)
    run_id = args.run_id or datetime.now(timezone.utc).strftime("run-%Y%m%dT%H%M%S-%f")
    ResetSpec(run_id)
    destination = args.output_dir / run_id
    if destination.exists():
        raise ValueError("run directory already exists; never overwrite a recording")
    from isaaclab.app import AppLauncher
    app = AppLauncher(headless=args.headless, enable_cameras=False, device=args.device).app
    from .single_hand_env import SingleHandKeyboardEnv, write_json
    env = None
    summaries = []
    try:
        config = SingleHandConfig.read(args.config) if args.config else replace(SingleHandConfig(), key_center_z_m=args.key_height)
        env = SingleHandKeyboardEnv(output_dir=destination, config=config, device=args.device)
        cases = ("press_1", "press_2", "miss", "stuck", "timeout_release", "press_after_reset",
                 "wrong_mapping", "stale_owner", "expired_action", "time_limit") if args.case == "suite" else (args.case,)
        for name in cases:
            env.bridge_config = KeycapConfig(maximum_hold_seconds=0.15 if name == "timeout_release" else 5.0)
            summary = collect_case(env, name=name, amplitude=0.04 if name == "miss" else args.amplitude,
                                   hold_s=0.3 if name == "timeout_release" else 0.15, seed=args.seed)
            summaries.append(summary)
        cleanup = env.close()
        if env.close() != cleanup:
            raise RuntimeError("close must be idempotent")
        write_json(destination / "collection.json", {"capability": "isaac_physx_single_hand_v1", "cases": summaries,
                                                     "learned_policy": False, "camera_verified": False})
        write_json(args.output_dir / "latest.json", {"run_directory": str(destination.resolve())})
        print("VF01_RUN " + str(destination.resolve()), flush=True)
        return 0
    except Exception as exc:
        destination.mkdir(parents=True, exist_ok=True)
        write_json(destination / "failure.json", {"error": str(exc), "traceback": traceback.format_exc()})
        traceback.print_exc()
        return 1
    finally:
        try:
            if env is not None and not env.closed:
                env.close()
        finally:
            # All evidence and administrative cleanup are already persisted.
            # This camera-free worker needs no replicator/extension drain.
            # Kit may exit with status 0 even after exceptions; runner.py owns
            # the authoritative command exit status and artifact checks.
            app.close(wait_for_replicator=False, skip_cleanup=True)


if __name__ == "__main__":
    raise SystemExit(main())
