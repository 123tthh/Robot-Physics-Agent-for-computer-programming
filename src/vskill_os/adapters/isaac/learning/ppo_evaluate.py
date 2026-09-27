"""Roll out frozen PPO candidates through the original raw-recording backend.

Model output alone drives every active joint delta; no press/release setpoint
script, IK trajectory, reward oracle or text buffer is available to the actor.
"""

import argparse
import json
from pathlib import Path
import traceback

from ....learning.contracts import EpisodeRecord
from ....learning.environment import ResetSpec
from ....learning.policy_manifest import ActionScale, ActionSchema, NormalizedJointAction
from .action_smoke import ACTION_SOURCES
from .batch_recording import sha
from .config import SingleHandConfig, control_spec
from .single_hand_env import ROOT, DEFAULT_URDF, write_json


EVALUATION_SOURCES = tuple(dict.fromkeys((*ACTION_SOURCES,
    'src/vskill_os/learning/ppo_config.py',
    'src/vskill_os/adapters/learning_torch/ppo.py',
    'src/vskill_os/adapters/learning_torch/checkpoint.py',
    'src/vskill_os/adapters/isaac/learning/observations.py',
    'src/vskill_os/adapters/isaac/learning/rewards.py',
    'src/vskill_os/adapters/isaac/learning/ppo_evaluate.py',
)))


def features_from_sample(sample, *, coordinate_names, control, key_names, progress, previous_action, horizon_s):
    import torch
    from .observations import teacher_observation
    indices = [coordinate_names.index(n) for n in control.joint_names]
    key = key_names.index('a')
    def tensor(values):
        return torch.tensor(values, dtype=torch.float32).reshape(1, -1)
    return teacher_observation(tensor(sample.joint_positions)[:, indices], tensor(sample.joint_velocities)[:, indices],
        tensor(sample.joint_targets)[:, indices], previous_action, tensor(sample.fingertip_xyz_m),
        tensor(sample.key_body_positions_xyz_m).reshape(1, -1, 3)[:, key],
        (-tensor(sample.key_joint_positions_m)[:, key]).clamp(min=0.),
        tensor(sample.key_force_vectors_n).reshape(1, -1, 3)[:, key], progress.pressed_seen,
        progress.clear_age[:, key], progress.elapsed, control, horizon_s)


def advance_progress(progress, sample, dt):
    import torch
    travel = (-torch.tensor(sample.key_joint_positions_m).reshape(1, -1)).clamp(min=0.)
    force = torch.tensor(sample.key_force_vectors_n).reshape(1, -1, 3)
    return progress.advance(travel, force, dt)


def collect(backend, session, model, payload, config, checkpoint, seed):
    import torch
    from .rewards import PressProgress
    name = f'seed-{seed}'
    port, observation, lease = session.reset(ResetSpec(name, seed=seed, maximum_time_s=config.horizon_s), session.schema.claims())
    progress = PressProgress(1, len(backend.key_names), backend.key_names.index('a'), 'cpu')
    previous = torch.zeros((1, 23))
    sequence = 0
    inference = []
    while session.state == 'active':
        feature = features_from_sample(backend.raw_samples[-1], coordinate_names=backend.robot.joint_names,
            control=session.schema.control, key_names=backend.key_names, progress=progress,
            previous_action=previous, horizon_s=config.horizon_s)
        active, _, _, _ = model.act(feature, deterministic=True)
        full = torch.zeros_like(previous)
        full[:, payload['active_indices']] = active
        dt = session.schema.control.control_dt_s
        action = NormalizedJointAction(session.schema.control.joint_names, tuple(full[0].tolist()), dt,
            observation.time_s+dt, lease.owner_token, sequence)
        inference.append(dict(sequence=sequence, time_s=observation.time_s, observation=feature[0].tolist(),
                              normalized_action=full[0].tolist()))
        start = len(backend.raw_samples)
        result = port.step(action)
        observation = result.observation
        for sample in backend.raw_samples[start:]:
            advance_progress(progress, sample, backend.config.physics_dt_s)
        previous = full
        sequence += 1
        if session.state == 'active' and bool(progress.completed[0]):
            session.finish()  # a request only; independent verifier decides
    episode = EpisodeRecord.read(backend.episode_dir/'episode.json')
    session.trace(episode.sha256()).write(backend.episode_dir/'policy_mapping.json')
    write_json(backend.episode_dir/'inference.json', dict(version='vf-ppo-inference/1', deterministic=True,
        checkpoint_sha256=sha(checkpoint), training_transitions=payload['training_transitions'],
        observation_manifest=payload['observations'], actions=inference))
    return dict(case=name, seed=seed, training_transitions=payload['training_transitions'],
                termination=episode.termination, actions=sequence, claimed_cycle=bool(progress.completed[0]))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkpoint', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--physics-config', type=Path, default=ROOT/'configs/learning/verification_feedback/single_hand_physics_v1.json')
    parser.add_argument('--action-scale', type=Path, default=ROOT/'configs/learning/verification_feedback/action_scale_v1.json')
    args = parser.parse_args(argv)
    if args.output.exists():
        raise ValueError('never overwrite a policy evaluation')
    from ...learning_torch.checkpoint import load_candidate
    import torch
    torch.set_num_threads(1)
    physical, scale = SingleHandConfig.read(args.physics_config), ActionScale.read(args.action_scale)
    model, payload, schema, config = load_candidate(args.checkpoint,
        expected_schema=ActionSchema.from_control(control_spec(DEFAULT_URDF, physical), scale))
    sources = {name: sha(ROOT/name) for name in EVALUATION_SOURCES}
    from isaaclab.app import AppLauncher
    app = AppLauncher(headless=True, enable_cameras=False, device='cuda:0').app
    backend = None
    try:
        from .single_hand_env import SingleHandKeyboardEnv
        from .joint_action import JointActionSession
        backend = SingleHandKeyboardEnv(output_dir=args.output, config=physical)
        session = JointActionSession(backend, schema)
        cases = []
        for seed in config.evaluation_seeds:
            result = collect(backend, session, model, payload, config, args.checkpoint, seed)
            if sources != {name: sha(ROOT/name) for name in EVALUATION_SOURCES}:
                raise RuntimeError('evaluation sources changed during capture')
            files = ('policy_mapping.json', 'inference.json', 'manifest.json', 'trace.json', 'episode.json')
            write_json(backend.episode_dir/'policy_manifest.json', dict(version='vf-ppo-evidence/1', sources=sources,
                checkpoint=dict(path=str(args.checkpoint.resolve()), sha256=sha(args.checkpoint)),
                files={name: sha(backend.episode_dir/name) for name in files}))
            cases.append(result)
            print('VF02_EVAL_COLLECTED '+json.dumps(result), flush=True)
        backend.close()
        write_json(args.output/'policy_collection.json', dict(cases=cases, checkpoint_sha256=sha(args.checkpoint),
            visual_policy=False, camera_verified=False, real_robot_verified=False, independent_verification=False))
        return 0
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


if __name__ == '__main__':
    raise SystemExit(main())
