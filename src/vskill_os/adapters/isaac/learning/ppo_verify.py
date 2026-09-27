"""Independent policy inference binding plus the existing raw physical verifier."""

import hashlib
import json
import math
from pathlib import Path

from ....learning.contracts import EpisodeRecord
from ....learning.environment import ResetSpec
from ....learning.physical import PhysicsTrace
from ....learning.policy_manifest import ActionScale, ActionSchema, MappingTrace
from ....learning.ppo_config import PPOConfig
from ....learning.serialization import _unique_object
from .batch_recording import sha
from .config import SingleHandConfig, control_spec
from .ppo_evaluate import EVALUATION_SOURCES, features_from_sample, advance_progress
from .ppo_worker import TRAIN_SOURCES
from .single_hand_env import ROOT, DEFAULT_URDF, write_json
from .verify import verify_bundle


# These are actual task failures, not permission to ignore corrupted identity,
# missing raw samples, unlogged targets, modified assets or incompatible code.
TASK_FAILURES = {'PRIMITIVE_MISMATCH', 'TEXT_ORACLE_MISMATCH', 'INCOMPLETE_EPISODE', 'STUCK_KEY',
    'SAFETY_STOP', 'JOINT_LIMIT', 'JOINT_SPEED', 'EXCESS_FORCE', 'UNSAFE_SUPPORT', 'UNSAFE_POSTURE',
    'KEY_TRAVEL_LIMIT', 'UNEXPECTED_KEY', 'INCOMPLETE_RELEASE', 'NO_CONTACT'}


def _require(ok, message):
    if not ok:
        raise ValueError(message)


def _check_launch_binding(launch, config, physical, scale, source_hashes):
    # JSON arrays become Python lists. Reconstruct versioned contracts rather
    # than compare raw JSON to dataclass dictionaries containing tuples.
    _require(launch['version'] == 'vf-ppo-launch/1'
        and PPOConfig.from_dict(launch['ppo_config']) == config
        and SingleHandConfig.from_dict(launch['physical_config']) == physical
        and ActionScale.from_dict(launch['action_scale']) == scale
        and launch['source_hashes'] == source_hashes, 'LAUNCH_CONFIG_BINDING')


def verify_episode(directory, checkpoint, *, physical, scale, seed):
    import torch
    from ...learning_torch.checkpoint import load_candidate
    from .rewards import PressProgress
    torch.set_num_threads(1)
    report = dict(case=directory.name, seed=seed, evidence_valid=False, physical_primitive_passed=False,
                  findings=[], task_failures=[], recording=str(directory.resolve()))
    try:
        manifest = json.loads((directory/'policy_manifest.json').read_text(), object_pairs_hook=_unique_object)
        files = ('policy_mapping.json', 'inference.json', 'manifest.json', 'trace.json', 'episode.json')
        _require(manifest['version'] == 'vf-ppo-evidence/1' and set(manifest['files']) == set(files)
            and all(manifest['files'][n] == sha(directory/n) for n in files), 'POLICY_ARTIFACT_HASH')
        sources = {n: sha(ROOT/n) for n in EVALUATION_SOURCES}
        _require(manifest['sources'] == sources, 'POLICY_SOURCE_HASH')
        _require(manifest['checkpoint']['sha256'] == sha(checkpoint), 'CHECKPOINT_HASH')
        expected_schema = ActionSchema.from_control(control_spec(DEFAULT_URDF, physical), scale)
        model, payload, schema, config = load_candidate(checkpoint, expected_schema=expected_schema)
        _require(payload['source_hashes'] == {n: sha(ROOT/n) for n in TRAIN_SOURCES}, 'TRAINING_SOURCE_HASH')
        reset = ResetSpec.read(directory/'reset.json')
        _require(reset == ResetSpec(f'seed-{seed}', seed=seed, maximum_time_s=config.horizon_s), 'FROZEN_EVALUATION_SEED_OR_HORIZON')
        raw = PhysicsTrace.read(directory/'trace.json')
        episode = EpisodeRecord.read(directory/'episode.json')
        mapping = MappingTrace.read(directory/'policy_mapping.json')
        inference = json.loads((directory/'inference.json').read_text(), object_pairs_hook=_unique_object)
        _require(inference['version'] == 'vf-ppo-inference/1' and inference['deterministic'] is True
            and inference['checkpoint_sha256'] == sha(checkpoint)
            and inference['training_transitions'] == payload['training_transitions']
            and inference['observation_manifest'] == payload['observations'], 'INFERENCE_IDENTITY')
        _require(mapping.action_schema == schema and mapping.control_claims == schema.claims()
            and mapping.physical_episode_sha256 == episode.sha256()
            and (mapping.env_id, mapping.episode_id) == (episode.env_id, episode.episode_id), 'MAPPING_IDENTITY')
        events = mapping.events
        _require(events and events[0].kind == 'reset' and events[0].after_time_s == 0
            and events[0].policy_sequence == 0 and events[0].state == 'active', 'POLICY_RESET')
        lease = events[0].lease_token
        _require(bool(lease), 'MISSING_POLICY_LEASE')
        steps = events[1:-1] if events[-1].kind == 'finish' else events[1:]
        _require(len(steps) == len(raw.actions) == len(inference['actions']) and len(steps) > 0, 'INFERENCE_COVERAGE')
        names = tuple(l.name for l in raw.coordinate_limits)
        ids = [names.index(n) for n in schema.control.joint_names]
        key = raw.key_names.index('a')
        progress = PressProgress(1, len(raw.key_names), key, 'cpu')
        previous = torch.zeros((1, 23))
        consumed = 0
        max_observation_error, max_action_error = 0., 0.
        for i, (mapped, applied, prediction) in enumerate(zip(steps, raw.actions, inference['actions'], strict=True)):
            tick = round(applied.time_s/physical.physics_dt_s)
            _require(0 <= tick < len(raw.samples), 'ACTION_TIME')
            for sample in raw.samples[consumed+1:tick+1]:
                advance_progress(progress, sample, physical.physics_dt_s)
            consumed = tick
            sample = raw.samples[tick]
            feature = features_from_sample(sample, coordinate_names=names, control=schema.control, key_names=raw.key_names,
                progress=progress, previous_action=previous, horizon_s=config.horizon_s)
            stored = torch.tensor(prediction['observation'], dtype=torch.float32).reshape(1, -1)
            _require(stored.shape == feature.shape and torch.isfinite(stored).all(), 'ACTOR_OBSERVATION_SHAPE')
            error = (feature-stored).abs().max().item()
            max_observation_error = max(max_observation_error, error)
            _require(error <= 2e-6, 'ACTOR_INPUT_NOT_DERIVED_FROM_RAW_PHYSICS')
            active, _, _, _ = model.act(feature, deterministic=True)
            full = torch.zeros_like(previous); full[:, payload['active_indices']] = active
            wanted = tuple(full[0].tolist())
            _require(mapped.kind == 'policy' and mapped.action is not None and mapped.port_token == lease
                and mapped.action.owner_token == lease and mapped.action.joint_names == schema.control.joint_names
                and mapped.action.sequence == i and mapped.backend_action_index == i
                and mapped.policy_sequence == i+1 and prediction['sequence'] == i
                and abs(prediction['time_s']-applied.time_s) < 1e-9
                and abs(mapped.before_time_s-applied.time_s) < 1e-9, 'POLICY_ACTION_IDENTITY')
            _require(len(prediction['normalized_action']) == 23 and len(mapped.action.values) == 23
                and all(math.isfinite(v) for v in prediction['normalized_action']), 'ACTION_SHAPE')
            action_error = max(abs(a-b) for a, b in zip(wanted, mapped.action.values, strict=True))
            max_action_error = max(max_action_error, action_error)
            _require(action_error <= 2e-6 and max(abs(a-b) for a, b in zip(wanted, prediction['normalized_action'], strict=True)) <= 2e-6,
                     'EXECUTED_ACTION_NOT_FROM_CHECKPOINT')
            # Recompute normalized deltas before the independent physical guard.
            delta = tuple(a*s for a, s in zip(mapped.action.values, schema.step_scale_rad, strict=True))
            _require(all(abs(a-b) < 1e-12 for a, b in zip(delta, applied.action.delta_rad, strict=True))
                and mapped.action.duration_s == applied.action.duration_s
                and mapped.action.valid_until_s == applied.action.valid_until_s, 'NORMALIZED_TO_JOINT_BINDING')
            q = tuple(sample.joint_targets[j] for j in ids)
            proposed = tuple(a+b for a, b in zip(q, delta, strict=True))
            _require(len(mapped.proposed_targets_rad) == 23 and len(mapped.filtered_targets_rad) == 23
                and all(abs(a-b) < 1e-12 for a, b in zip(proposed, mapped.proposed_targets_rad, strict=True))
                and all(abs(a-b) < 1e-12 for a, b in zip(applied.applied_targets_rad, mapped.filtered_targets_rad, strict=True)),
                'MAPPER_RECEIPT_BINDING')
            end_tick = round(mapped.after_time_s/physical.physics_dt_s)
            _require(tick < end_tick <= min(tick+physical.decimation, len(raw.samples)-1), 'MAPPING_ENDPOINT')
            end = raw.samples[end_tick]
            _require(tuple(j.commanded_target_rad for j in mapped.joints) == tuple(end.joint_targets[j] for j in ids),
                     'EXECUTED_TARGET_FEEDBACK')
            previous = full
        _require(abs(steps[-1].after_time_s-episode.end_time_s) < 1e-9, 'FINAL_POLICY_SAMPLE')
        physical_result = verify_bundle(directory, expected_config=physical)
        _require('consistent' in physical_result, 'INVALID_RAW_PHYSICS_BUNDLE: '+str(physical_result['findings']))
        structural = [f for f in physical_result['findings'] if f['code'] not in TASK_FAILURES]
        _require(not structural, 'RAW_PHYSICS_BINDING: '+str(structural))
        report.update(evidence_valid=True, physical_primitive_passed=physical_result['physical_primitive_passed'],
            task_failures=sorted({f['code'] for f in physical_result['findings']}), physical=physical_result,
            checkpoint_sha256=sha(checkpoint), training_transitions=payload['training_transitions'],
            action_count=len(steps), maximum_observation_error=max_observation_error, maximum_action_error=max_action_error,
            elapsed_sim_s=episode.end_time_s, source_hashes=sources,
            evaluator_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    except (OSError, ValueError, KeyError, TypeError, IndexError, RuntimeError) as exc:
        report['findings'].append(str(exc))
    return report


def verify_evaluation(directory, checkpoint, *, physical, scale):
    from ...learning_torch.checkpoint import load_candidate
    report = dict(version='vf-ppo-evaluation/1', evidence_valid=False, success_count=0, episode_count=0,
                  findings=[], cases=[], visual_policy=False, real_robot_verified=False)
    try:
        _, payload, _, config = load_candidate(checkpoint)
        process = json.loads((directory/'process.json').read_text())
        _require(process.get('returncode') == 0 and process.get('timed_out') is False
            and not process.get('interrupted') and not (directory/'failure.json').exists(), 'EVALUATION_WORKER_FAILED')
        collection = json.loads((directory/'policy_collection.json').read_text())
        _require(collection['checkpoint_sha256'] == sha(checkpoint)
            and [c['seed'] for c in collection['cases']] == list(config.evaluation_seeds)
            and all(c['training_transitions'] == payload['training_transitions'] for c in collection['cases']),
            'INCOMPLETE_FROZEN_EVALUATION')
        for seed in config.evaluation_seeds:
            case = verify_episode(directory/f'seed-{seed}', checkpoint, physical=physical, scale=scale, seed=seed)
            write_json(directory/f'seed-{seed}'/'policy_evaluation.json', case)
            report['cases'].append(case)
        report['episode_count'] = len(report['cases'])
        report['success_count'] = sum(c['physical_primitive_passed'] for c in report['cases'])
        report['evidence_valid'] = all(c['evidence_valid'] for c in report['cases'])
        report['training_transitions'] = payload['training_transitions']
        report['checkpoint_sha256'] = sha(checkpoint)
        report['success_rate'] = report['success_count']/report['episode_count']
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        report['findings'].append(str(exc))
    return report


def verify_training(directory, config: PPOConfig, *, physical, scale):
    from ...learning_torch.checkpoint import load_candidate
    report = dict(training_valid=False, findings=[])
    try:
        process = json.loads((directory/'process.json').read_text())
        _require(process.get('returncode') == 0 and process.get('timed_out') is False
            and not process.get('interrupted') and not (directory/'failure.json').exists(), 'TRAINING_WORKER_FAILED')
        training = json.loads((directory/'training.json').read_text(), object_pairs_hook=_unique_object)
        launch = json.loads(directory.with_suffix('.launch.json').read_text(), object_pairs_hook=_unique_object)
        _check_launch_binding(launch, config, physical, scale, training['source_hashes'])
        snapshot = Path(launch['source_snapshot'])
        _require(all(sha(snapshot/name) == value for name, value in launch['source_hashes'].items()),
                 'TRAINING_SOURCE_SNAPSHOT')
        schema = ActionSchema.from_dict(training['action_schema'])
        _require(schema == ActionSchema.from_control(control_spec(DEFAULT_URDF, physical), scale), 'TRAINING_ACTION_SCHEMA')
        _require(training['assets']['urdf_sha256'] == sha(DEFAULT_URDF)
            and training['assets']['scene_sha256'] == sha(directory/'scene.usda'), 'TRAINING_ASSET_BINDING')
        _require(training['completed'] is True and training['transitions'] == config.transitions
            and PPOConfig.from_dict(training['config']) == config, 'TRAINING_BUDGET_OR_CONFIG')
        _require(training['source_hashes'] == {n: sha(ROOT/n) for n in TRAIN_SOURCES}, 'TRAINING_SOURCE_HASH')
        _require(training['metrics_sha256'] == sha(directory/'training_metrics.jsonl'), 'TRAINING_METRICS_HASH')
        rows = [json.loads(line) for line in (directory/'training_metrics.jsonl').read_text().splitlines()]
        transitions = 0
        expected_checkpoints, next_checkpoint = [0], config.checkpoint_interval
        totals = dict(episodes=0, completed_cycles=0, mistakes=0, safety_stops=0, timeouts=0)
        for i, row in enumerate(rows):
            expected_steps = min(config.rollout_steps, (config.transitions-transitions)//config.num_envs)
            transitions += expected_steps*config.num_envs
            _require(row['update'] == i+1 and row['rollout_steps'] == expected_steps and row['transitions'] == transitions
                and row['independent_verification'] is False, 'TRAINING_COUNTER_GAP')
            for name, field in (('episodes', 'episode_count'), ('completed_cycles', 'training_completed_cycles'),
                                ('mistakes', 'mistakes'), ('safety_stops', 'safety_stops'), ('timeouts', 'timeouts')):
                totals[name] += row[field]
            _require(totals == row['totals'], 'EPISODE_COUNTER_GAP')
            if transitions >= next_checkpoint or transitions == config.transitions:
                expected_checkpoints.append(transitions)
                next_checkpoint += config.checkpoint_interval
        _require(transitions == config.transitions and len(rows) == training['updates'] and totals == training['totals'], 'INCOMPLETE_TRAINING')
        _require(training['physics_steps'] == (config.transitions//config.num_envs)*physical.decimation, 'PHYSICS_TRANSITION_ACCOUNTING')
        _require([c['training_transitions'] for c in training['checkpoints']] == expected_checkpoints
            and json.loads((directory/'checkpoints.json').read_text())['checkpoints'] == training['checkpoints'],
            'CHECKPOINT_COVERAGE')
        for checkpoint in training['checkpoints']:
            _require(Path(checkpoint['path']).name == checkpoint['path'] and sha(directory/checkpoint['path']) == checkpoint['sha256'],
                     'CHECKPOINT_HASH')
            _, payload, _, saved_config = load_candidate(directory/checkpoint['path'], expected_schema=schema)
            _require(saved_config == config and payload['training_transitions'] == checkpoint['training_transitions']
                and payload['source_hashes'] == training['source_hashes'], 'CHECKPOINT_TRAINING_BINDING')
        report.update(training_valid=True, summary=training, launch=launch)
    except (OSError, ValueError, KeyError, TypeError, IndexError, RuntimeError) as exc:
        report['findings'].append(str(exc))
    return report
