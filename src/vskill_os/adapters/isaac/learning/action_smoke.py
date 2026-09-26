"""VF-02-T01 bounded normalized-action diagnostic; no PPO, IK or camera."""

import argparse
from dataclasses import replace
import json
from pathlib import Path
import traceback

from ....learning.contracts import EpisodeRecord
from ....learning.environment import ActionRejected, ResetSpec
from ....learning.policy_manifest import ActionScale, ActionSchema, NormalizedJointAction
from .config import SingleHandConfig
from .joint_action import JointActionSession
from .provenance import COLLECTOR_FILES
from .single_hand_env import ROOT, file_sha256, write_json


ACTION_SOURCES = (*COLLECTOR_FILES,
    'src/vskill_os/learning/policy_manifest.py',
    'src/vskill_os/adapters/isaac/learning/joint_action.py',
    'src/vskill_os/adapters/isaac/learning/action_smoke.py',
)
CASES_BY_DECIMATION = {2: ('normalized_press',), 4: ('normalized_press', 'paused_press', 'press_after_reset'),
                       8: ('normalized_press',)}


def collect_case(session, name, seed, old_port=None):
    backend = session.backend
    schema = session.schema
    port, observation, lease = session.reset(ResetSpec(name, seed=seed), schema.claims())
    dt = schema.control.control_dt_s
    index = schema.control.joint_names.index('left_hand_index_1_joint')
    sequence = 0

    def request(desired=0.):
        # Nonzero masked columns deliberately exercise all 22 held controls.
        # Only the active joint drives the finger. This is a scripted setpoint
        # diagnostic; it is not a demonstration dataset or a trained policy.
        values = [1. if i % 2 == 0 else -1. for i in range(23)]
        values[index] = max(-1., min(1., (desired-observation.joints[index].commanded_target_rad)
                                   / schema.step_scale_rad[index]))
        return NormalizedJointAction(schema.control.joint_names, tuple(values), dt,
                                      observation.time_s+dt, lease.owner_token, sequence)

    def reject(source, action, code):
        before = (backend.tick, len(backend.actions), len(backend.raw_samples), session.sequence)
        try:
            source.step(action)
        except ActionRejected as exc:
            if exc.code != code:
                raise RuntimeError(f'expected {code}, got {exc.code}') from exc
        else:
            raise RuntimeError(f'invalid input accepted: {code}')
        if before != (backend.tick, len(backend.actions), len(backend.raw_samples), session.sequence):
            raise RuntimeError('rejected action advanced physics or consumed sequence')

    if old_port is not None:
        reject(old_port, request(), 'CONTROL_OWNERSHIP')
    if name == 'paused_press':
        valid = request()
        for changes, code in (
            ({'owner_token': 'stale-lease'}, 'CONTROL_OWNERSHIP'),
            ({'joint_names': tuple(reversed(valid.joint_names))}, 'JOINT_MAPPING'),
            ({'sequence': 9}, 'ACTION_SEQUENCE'),
            ({'values': (1.01,)*23}, 'ACTION_RANGE'),
            ({'duration_s': dt*2}, 'ACTION_EXPIRED'),
            ({'valid_until_s': 0.}, 'ACTION_EXPIRED'),
        ):
            reject(port, replace(valid, **changes), code)

    def advance(desired, duration):
        nonlocal observation, sequence
        for _ in range(round(duration/dt)):
            result = port.step(request(desired))
            if result.terminated or result.truncated or result.failure_codes:
                raise RuntimeError(f'physical diagnostic stopped early: {result.failure_codes}')
            observation = result.observation
            sequence += 1

    advance(.26, .9)
    if name == 'paused_press':
        old = port
        session.pause()
        reject(old, request(.26), 'CONTROL_STOPPED')
        for _ in range(round(.1/dt)):
            result = session.hold_step()
            if result.terminated or result.truncated or result.failure_codes:
                raise RuntimeError(f'hold stopped: {result.failure_codes}')
        port, observation, lease = session.resume(schema.claims())
        sequence = 0
        reject(old, request(.26), 'CONTROL_OWNERSHIP')
    advance(.26, .2)
    advance(0., .9)
    advance(0., .2)
    session.finish()
    episode = EpisodeRecord.read(backend.episode_dir/'episode.json')
    mapping = session.trace(episode.sha256())
    mapping.write(backend.episode_dir/'action_mapping.json')
    return port, dict(case=name, seed=seed, steps=backend.action_sequence,
                      sim_time_s=observation.time_s, mapping_sha256=mapping.sha256())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--physics-config', type=Path, required=True)
    parser.add_argument('--action-scale', type=Path, required=True)
    parser.add_argument('--decimation', type=int, choices=tuple(CASES_BY_DECIMATION), required=True)
    args = parser.parse_args(argv)
    if args.output.exists():
        raise ValueError('never overwrite a recording')
    config = replace(SingleHandConfig.read(args.physics_config), decimation=args.decimation)
    scale = ActionScale.read(args.action_scale)
    sources = {name: file_sha256(ROOT/name) for name in ACTION_SOURCES}
    from isaaclab.app import AppLauncher
    app = AppLauncher(headless=True, enable_cameras=False, device='cuda:0').app
    from .single_hand_env import SingleHandKeyboardEnv
    backend = None
    try:
        backend = SingleHandKeyboardEnv(output_dir=args.output, config=config)
        session = JointActionSession(backend, ActionSchema.from_control(backend.control_spec, scale))
        old_port, cases = None, []
        for i, name in enumerate(CASES_BY_DECIMATION[args.decimation]):
            old_port, summary = collect_case(session, name, 7+i, old_port)
            cases.append(summary)
            if sources != {name: file_sha256(ROOT/name) for name in ACTION_SOURCES}:
                raise RuntimeError('collector changed while capturing action evidence')
            files = ('action_mapping.json', 'manifest.json', 'episode.json', 'trace.json')
            write_json(backend.episode_dir/'action_manifest.json', dict(schema_version='vf-action-bundle/1',
                sources=sources, files={name: file_sha256(backend.episode_dir/name) for name in files}))
            print('VF02_COLLECTED '+json.dumps(summary), flush=True)
        backend.close()
        write_json(args.output/'action_collection.json', dict(cases=cases, decimation=args.decimation,
            learned_policy=False, camera_verified=False, real_robot_verified=False))
    except Exception as exc:
        write_json(args.output/'failure.json', dict(error=str(exc), traceback=traceback.format_exc()))
        traceback.print_exc()
        return 1
    finally:
        try:
            if backend is not None and not backend.closed:
                backend.close()
        finally:
            app.close(wait_for_replicator=False, skip_cleanup=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
