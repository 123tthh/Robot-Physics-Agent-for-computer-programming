"""Frozen small-sample desktop/camera revalidation; no IK or learned policy."""
import argparse
import json
from pathlib import Path
import traceback

from ....learning.camera_feedback import DesktopCameraSpec
from ....learning.environment import JointTargetAction, ResetSpec
from .config import SingleHandConfig
from .desktop_env import CASES


def collect_case(supervisor, name, seed):
    port, observation, metadata = supervisor.reset(ResetSpec(name, seed=seed, maximum_time_s=supervisor.spec.feedback.horizon_s), name)
    dt, names = metadata.control_spec.control_dt_s, metadata.control_spec.joint_names
    finger, sequence = names.index('left_hand_index_1_joint'), 0
    port.request_verification('intermediate')
    if port.observe().feedback:
        raise RuntimeError('reset leaked previous camera feedback')

    def hold(target, duration):
        nonlocal sequence, observation
        for _ in range(round(duration/dt)):
            base = observation.physical
            delta = [0.]*len(names)
            delta[finger] = target-base.joints[finger].commanded_target_rad
            result = port.step(JointTargetAction(names, tuple(delta), dt, base.time_s+dt, metadata.owner_token, sequence))
            observation, sequence = result.observation, sequence+1
            if result.terminated or result.truncated:
                raise RuntimeError(f'physical diagnostic aborted: {name}: {result.failure_codes}')
    for target, duration in ((.26, .9), (.26, .15), (0., .9), (0., .15)):
        hold(target, duration)
    receipt = port.request_verification('final')
    if not receipt.accepted:
        raise RuntimeError('final camera request rejected: '+str(receipt.rejection))
    if name != 'finish_before_camera':
        hold(0., .15)
    observation = port.observe()
    port.finish()
    try:
        port.observe()
    except RuntimeError:
        pass
    else:
        raise RuntimeError('finished camera port remained usable')
    return dict(case=name, seed=seed, end_time_s=observation.physical.time_s,
                statuses=[v.status for v in observation.feedback], collected=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--physics-config', type=Path, required=True)
    parser.add_argument('--desktop-config', type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists():
        raise ValueError('never overwrite a desktop/camera recording')
    from isaaclab.app import AppLauncher
    app = AppLauncher(headless=True, enable_cameras=True, device='cuda:0').app
    from .single_hand_env import SingleHandKeyboardEnv, write_json
    from .desktop_env import DesktopCameraEnvironment
    backend = supervisor = None
    try:
        backend = SingleHandKeyboardEnv(output_dir=args.output, config=SingleHandConfig.read(args.physics_config))
        supervisor = DesktopCameraEnvironment(backend, DesktopCameraSpec.read(args.desktop_config))
        cases = []
        for index, name in enumerate(CASES):
            result = collect_case(supervisor, name, 7+index)
            cases.append(result)
            print('VF05_COLLECTED '+json.dumps(result), flush=True)
        supervisor.close()
        write_json(args.output/'desktop_collection.json', dict(cases=cases, learned_policy=False, real_robot_verified=False))
    except Exception as exc:
        write_json(args.output/'failure.json', dict(error=str(exc), traceback=traceback.format_exc()))
        traceback.print_exc()
    finally:
        try:
            if supervisor is not None: supervisor.close()
            elif backend is not None: backend.close()
        finally:
            app.close(wait_for_replicator=False, skip_cleanup=True)


if __name__ == '__main__':
    main()
