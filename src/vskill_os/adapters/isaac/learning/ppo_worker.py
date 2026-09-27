"""Bounded actual-PhysX single-seed PPO diagnostic, not a proxy simulator."""

import argparse
from dataclasses import replace
import json
from pathlib import Path
import resource
import time
import traceback

from ....learning.batch import BatchConfig
from ....learning.policy_manifest import ActionScale, ActionSchema
from ....learning.ppo_config import PPOConfig, PressRewardSpec
from .batch_recording import sha
from .config import SingleHandConfig
from .observations import observation_manifest
from .provenance import COLLECTOR_FILES
from .single_hand_env import ROOT, write_json


TRAIN_SOURCES = tuple(dict.fromkeys((*COLLECTOR_FILES,
    'src/vskill_os/learning/batch.py',
    'src/vskill_os/learning/policy_manifest.py',
    'src/vskill_os/learning/ppo_config.py',
    'src/vskill_os/adapters/isaac/learning/batch_scene.py',
    'src/vskill_os/adapters/isaac/learning/batch_recording.py',
    'src/vskill_os/adapters/isaac/learning/observations.py',
    'src/vskill_os/adapters/isaac/learning/rewards.py',
    'src/vskill_os/adapters/isaac/learning/training_safety.py',
    'src/vskill_os/adapters/isaac/learning/ppo_env.py',
    'src/vskill_os/adapters/isaac/learning/ppo_worker.py',
    'src/vskill_os/adapters/learning_torch/ppo.py',
)))


def train(scene, env, config, schema, directory, sources):
    import torch
    from ...learning_torch.ppo import ActorCritic, PPOTrainer
    torch.set_num_threads(1)
    torch.manual_seed(config.seed)
    torch.cuda.manual_seed_all(config.seed)
    observation = env.observation()
    model = ActorCritic(observation.shape[-1], len(env.active_indices), config.hidden_size, config.initial_log_std).to(env.device)
    trainer = PPOTrainer(model, config)
    began = time.monotonic()
    checkpoints = []
    transitions, update, next_checkpoint = 0, 0, config.checkpoint_interval
    totals = dict(episodes=0, completed_cycles=0, mistakes=0, safety_stops=0, timeouts=0)

    def checkpoint():
        if sources != {name: sha(ROOT/name) for name in TRAIN_SOURCES}:
            raise RuntimeError('training source changed during collection')
        path = directory/f'policy-{transitions:09d}.pt'
        payload = dict(version='vf-ppo-checkpoint/1', training_transitions=transitions, seed=config.seed,
            config=config.to_dict(), action_schema=schema.to_dict(), observations=observation_manifest(schema.control),
            active_indices=env.active_indices, model=model.state_dict(), optimizer=trainer.optimizer.state_dict(),
            source_hashes=sources, torch_rng=torch.get_rng_state(), cuda_rng=torch.cuda.get_rng_state_all(),
            torch_version=str(torch.__version__), policy_kind='diagnostic_candidate',
            environment_state_saved=False, training_demonstrations=False, ik_initialization=False)
        torch.save(payload, path)
        item = dict(path=path.name, sha256=sha(path), training_transitions=transitions)
        checkpoints.append(item)
        write_json(directory/'checkpoints.json', dict(checkpoints=checkpoints))
        return item

    checkpoint()
    with (directory/'training_metrics.jsonl').open('w') as metrics:
        while transitions < config.transitions:
            steps = min(config.rollout_steps, (config.transitions-transitions)//config.num_envs)
            buffer = {k: [] for k in ('observations', 'latent', 'log_prob', 'values', 'rewards', 'next_values', 'terminated', 'truncated')}
            reward_sum = observation.new_zeros(7)
            episode_rows = []
            rollout_start = time.monotonic()
            for _ in range(steps):
                action, latent, logp, value = model.act(observation)
                result = env.step(action)
                with torch.no_grad():
                    next_value = model.value(result['terminal_observation'])
                values = dict(observations=observation, latent=latent, log_prob=logp, values=value,
                    rewards=result['rewards'], next_values=next_value,
                    terminated=result['terminated'], truncated=result['truncated'])
                for key, tensor in values.items():
                    buffer[key].append(tensor.clone())
                observation = result['observation']
                reward_sum += result['terms'].sum(0)
                episode_rows.append(result['episodes'])
                transitions += config.num_envs
            rollout_s = time.monotonic()-rollout_start
            optimization = trainer.update({key: torch.stack(values) for key, values in buffer.items()})
            episodes = torch.cat(episode_rows)
            counts = episodes[:, 2:].sum(0).long().tolist()
            totals['episodes'] += len(episodes)
            for name, value in zip(('completed_cycles', 'mistakes', 'safety_stops', 'timeouts'), counts, strict=True):
                totals[name] += value
            update += 1
            from .rewards import REWARD_NAMES
            row = dict(update=update, transitions=transitions, rollout_steps=steps, rollout_s=rollout_s,
                transitions_per_s=steps*config.num_envs/rollout_s, elapsed_wall_s=time.monotonic()-began,
                episode_count=len(episodes), training_completed_cycles=counts[0], mistakes=counts[1],
                safety_stops=counts[2], timeouts=counts[3],
                mean_episode_return=episodes[:, 0].mean().item() if len(episodes) else None,
                mean_episode_time_s=episodes[:, 1].mean().item() if len(episodes) else None,
                reward_terms=dict(zip(REWARD_NAMES, (reward_sum/(steps*config.num_envs)).tolist(), strict=True)),
                optimizer=optimization, totals=totals.copy(), independent_verification=False)
            metrics.write(json.dumps(row, allow_nan=False)+'\n'); metrics.flush()
            print('VF02_PPO '+json.dumps({k: row[k] for k in ('update', 'transitions', 'transitions_per_s',
                'episode_count', 'training_completed_cycles', 'safety_stops', 'elapsed_wall_s')}), flush=True)
            if transitions >= next_checkpoint or transitions == config.transitions:
                checkpoint()
                next_checkpoint += config.checkpoint_interval
    summary = dict(version='vf-ppo-training/1', completed=True, transitions=transitions, config=config.to_dict(),
        reward_spec=env.reward_spec.to_dict(), action_schema=schema.to_dict(), observations=observation_manifest(schema.control),
        runtime=scene.runtime, source_hashes=sources, checkpoints=checkpoints, updates=update, totals=totals,
        wall_s=time.monotonic()-began, physics_steps=env.physics_steps, initialization_physics_steps=scene.config.settling_steps,
        reset_count=env.reset_count, torch_peak_allocated_bytes=torch.cuda.max_memory_allocated(),
        torch_peak_reserved_bytes=torch.cuda.max_memory_reserved(), process_peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        raw_training_recording='aggregate metrics only; full raw success/failure rollouts captured in separate evaluation worker',
        independent_verification=False, visual_policy=False, ik_or_demonstrations_used=False,
        assets=dict(urdf_sha256=sha(scene.urdf), scene_sha256=sha(directory/'scene.usda')),
        metrics_sha256=sha(directory/'training_metrics.jsonl'))
    write_json(directory/'training.json', summary)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--config', type=Path, default=ROOT/'configs/learning/verification_feedback/single_key_ppo_v1.json')
    parser.add_argument('--physics-config', type=Path, default=ROOT/'configs/learning/verification_feedback/single_hand_physics_v1.json')
    parser.add_argument('--action-scale', type=Path, default=ROOT/'configs/learning/verification_feedback/action_scale_v1.json')
    parser.add_argument('--transitions', type=int, help='explicit preflight budget override, recorded in the run')
    args = parser.parse_args(argv)
    if args.output.exists():
        raise ValueError('never overwrite a training run')
    config = PPOConfig.read(args.config)
    if args.transitions is not None:
        config = replace(config, transitions=args.transitions)
    physical, scale = SingleHandConfig.read(args.physics_config), ActionScale.read(args.action_scale)
    sources = {name: sha(ROOT/name) for name in TRAIN_SOURCES}
    from isaaclab.app import AppLauncher
    app = AppLauncher(headless=True, enable_cameras=False, device='cuda:0').app
    try:
        from .batch_scene import BatchPhysicsScene
        from .ppo_env import SingleKeyTrainingEnv
        scene = BatchPhysicsScene(args.output, replace(BatchConfig(), num_envs=config.num_envs, seed=config.seed), physical)
        schema = ActionSchema.from_control(scene.control, scale)
        env = SingleKeyTrainingEnv(scene, schema, config, PressRewardSpec())
        train(scene, env, config, schema, args.output, sources)
        return 0
    except Exception as exc:
        write_json(args.output/'failure.json', dict(error=str(exc), traceback=traceback.format_exc()))
        traceback.print_exc()
        return 1
    finally:
        app.close(wait_for_replicator=False, skip_cleanup=True)


if __name__ == '__main__':
    raise SystemExit(main())
