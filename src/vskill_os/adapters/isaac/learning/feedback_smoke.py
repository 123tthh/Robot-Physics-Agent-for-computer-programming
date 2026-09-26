"""Bounded direct-joint integration diagnostic, with simulated text readouts."""

import argparse
import json
from pathlib import Path
import traceback

from ....learning.environment import JointTargetAction, ResetSpec
from ....learning.feedback_demo import case_spec
from ....learning.feedback_session import FeedbackSpec
from .config import SingleHandConfig

CASES = ("verified_after_wait", "software_drop", "focus_lost", "missing_readout", "finish_before_feedback",
         "stale_readout", "timeout", "false_positive_readout")


def collect_case(supervisor, *, name: str, spec: FeedbackSpec):
    port, observation, metadata = supervisor.reset(ResetSpec(name, maximum_time_s=spec.config.horizon_s), spec)
    dt, names = metadata.control_spec.control_dt_s, metadata.control_spec.joint_names
    index, sequence = names.index("left_hand_index_1_joint"), 0
    port.request_verification("intermediate")
    if port.observe().feedback:
        raise RuntimeError("a new episode exposed feedback before delivery")

    def hold_at(desired, duration):
        nonlocal sequence, observation
        for _ in range(round(duration/dt)):
            delta = [0.]*len(names)
            delta[index] = desired-observation.joints[index].commanded_target_rad
            result = port.step(JointTargetAction(names, tuple(delta), dt, observation.time_s+dt, metadata.owner_token, sequence))
            observation = result.observation
            sequence += 1
            if result.terminated or result.truncated:
                raise RuntimeError(f"physical diagnostic aborted: {name}: {result.failure_codes}")

    # Same measured 2.1 s pulse as T02. The diagnostic does not synthesize keys,
    # consult oracle text, retry on missing feedback, or bypass the safety guard.
    for target, duration in ((.26, .9), (.26, .15), (0., .9), (0., .15)):
        hold_at(target, duration)
    receipt = port.request_verification("final")
    if not receipt.accepted:
        raise RuntimeError(f"diagnostic final request rejected: {receipt.rejection}")
    if name != "finish_before_feedback":
        hold_at(0., .35 if name == "timeout" else .15)
    observation = port.observe()
    port.finish()
    try:
        port.observe()
    except RuntimeError:
        pass
    else:
        raise RuntimeError("finished port remained usable")
    return dict(case=name, elapsed_sim_s=observation.time_s, request_count=observation.budget.accepted_requests,
                visible_statuses=[item.status for item in observation.feedback], collected=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--feedback-config", type=Path, required=True)
    args = parser.parse_args(argv)
    ResetSpec(args.run_id)
    directory = args.output_dir / args.run_id
    if directory.exists():
        raise ValueError("never overwrite a physical recording")
    from isaaclab.app import AppLauncher
    app = AppLauncher(headless=True, enable_cameras=False, device="cuda:0").app
    from .single_hand_env import SingleHandKeyboardEnv, write_json
    from .feedback_env import isaac_feedback_environment
    backend = supervisor = None
    try:
        base = FeedbackSpec.read(args.feedback_config)
        backend = SingleHandKeyboardEnv(output_dir=directory, config=SingleHandConfig.read(args.config))
        supervisor = isaac_feedback_environment(backend)
        cases = []
        for name in CASES:
            summary = collect_case(supervisor, name=name, spec=case_spec(name, base))
            cases.append(summary)
            print("VF03_COLLECTED " + json.dumps(summary), flush=True)
        supervisor.close()
        write_json(directory / "feedback_collection.json", dict(cases=cases, camera_verified=False, learned_policy=False,
                                                                 readout_source="text_state_readout_v1"))
        return 0
    except Exception as exc:
        write_json(directory / "failure.json", {"error": str(exc), "traceback": traceback.format_exc()})
        traceback.print_exc()
        return 1
    finally:
        try:
            if supervisor is not None:
                supervisor.close()
            elif backend is not None:
                backend.close()
        finally:
            app.close(wait_for_replicator=False, skip_cleanup=True)


if __name__ == "__main__":
    raise SystemExit(main())
