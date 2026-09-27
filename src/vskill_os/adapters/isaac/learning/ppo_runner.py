"""Train a diagnostic candidate, capture frozen checkpoints, independently verify."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

from ....learning.policy_manifest import ActionScale
from ....learning.ppo_config import PPOConfig
from .action_runner import run_worker
from .batch_recording import sha
from .config import SingleHandConfig
from .ppo_verify import verify_evaluation, verify_training
from .ppo_worker import TRAIN_SOURCES
from .single_hand_env import ROOT, write_json


def write_launch(directory, *, config_path, physics_path, scale_path, recorded_stage='before_worker'):
    """Preserve the exact launcher inputs and source bytes outside the new run.

    The worker refuses an existing output directory. A sibling manifest also
    survives a startup failure before the worker can create that directory.
    """
    sources = {name: sha(ROOT/name) for name in TRAIN_SOURCES}
    snapshot = directory.with_suffix('.sources')
    for name in TRAIN_SOURCES:
        path = snapshot/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((ROOT/name).read_bytes())
    launch = dict(version='vf-ppo-launch/1', recorded_stage=recorded_stage,
        recorded_at_utc=datetime.now(timezone.utc).isoformat(),
        ppo_config=PPOConfig.read(config_path).to_dict(), physical_config=SingleHandConfig.read(physics_path).to_dict(),
        action_scale=ActionScale.read(scale_path).to_dict(), source_hashes=sources,
        configuration_hashes={str(p.resolve()): sha(p) for p in (config_path, physics_path, scale_path)},
        source_snapshot=str(snapshot.resolve()))
    write_json(directory.with_suffix('.launch.json'), launch)


def capture_available(directory, *, physics_path, scale_path, timeout_s=600):
    """Capture all currently sealed checkpoints; may run alongside training."""
    checkpoints = json.loads((directory/'checkpoints.json').read_text())['checkpoints']
    physical, scale = SingleHandConfig.read(physics_path), ActionScale.read(scale_path)
    results = []
    for item in checkpoints:
        if Path(item['path']).name != item['path'] or sha(directory/item['path']) != item['sha256']:
            raise ValueError('unsealed or changed checkpoint')
        checkpoint = directory/item['path']
        destination = directory/f"evaluation-{item['training_transitions']:09d}"
        if not destination.exists():
            command = [sys.executable, '-m', 'vskill_os.adapters.isaac.learning.ppo_evaluate',
                '--checkpoint', str(checkpoint), '--output', str(destination),
                '--physics-config', str(physics_path.resolve()), '--action-scale', str(scale_path.resolve())]
            run_worker(command, destination, timeout_s)
        result = verify_evaluation(destination, checkpoint, physical=physical, scale=scale)
        write_json(destination/'evaluation.json', result)
        results.append(result)
        print('VF02_POLICY_RESULT '+json.dumps(dict(transitions=item['training_transitions'],
            evidence_valid=result['evidence_valid'], success_count=result['success_count'],
            episode_count=result['episode_count'], findings=result['findings'])), flush=True)
        if not result['evidence_valid']:
            raise RuntimeError('invalid checkpoint evidence: '+str(result['findings'] or
                               [c['findings'] for c in result['cases']]))
    return results


def verify_run(directory, *, config, physical, scale):
    training = verify_training(directory, config, physical=physical, scale=scale)
    report = dict(version='vf-single-key-learnability/1', task='VF-02-T02', accepted=False,
        diagnostic_complete=False, learning_progress_observed=False, findings=[], training=training,
        recording=str(directory.resolve()), evaluations=[], trained_seeds=[config.seed],
        scope='fixed-pelvis single left-index a; privileged state PPO, no IK or demonstrations',
        visual_policy=False, camera_verified=False, real_robot_verified=False, multi_seed_gate_passed=False,
        evaluation_scope='eight frozen development seeds per checkpoint; repeated fixed scene, not held-out geometry')
    if not training['training_valid']:
        report['findings'].extend(training['findings'])
        return report
    for item in training['summary']['checkpoints']:
        path = directory/f"evaluation-{item['training_transitions']:09d}"
        result = verify_evaluation(path, directory/item['path'], physical=physical, scale=scale)
        write_json(path/'evaluation.json', result)
        report['evaluations'].append(result)
    report['diagnostic_complete'] = all(e['evidence_valid'] for e in report['evaluations'])
    if report['diagnostic_complete']:
        first, last = report['evaluations'][0], report['evaluations'][-1]
        report['learning_progress_observed'] = last['success_count'] > first['success_count']
        if not report['learning_progress_observed']:
            report['findings'].append('Budget completed without an independently verified improvement over initialization.')
    else:
        report['findings'].append('One or more checkpoint evaluations have invalid or incomplete evidence.')
    report['accepted'] = report['diagnostic_complete'] and report['learning_progress_observed']
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=ROOT/'artifacts/verification_feedback/vf02/ppo')
    parser.add_argument('--config', type=Path, default=ROOT/'configs/learning/verification_feedback/single_key_ppo_v1.json')
    parser.add_argument('--physics-config', type=Path, default=ROOT/'configs/learning/verification_feedback/single_hand_physics_v1.json')
    parser.add_argument('--action-scale', type=Path, default=ROOT/'configs/learning/verification_feedback/action_scale_v1.json')
    parser.add_argument('--evaluate-run', type=Path, help='capture missing sealed checkpoints of a local training run')
    parser.add_argument('--verify-run', type=Path, help='read-only independent replay; does not start Isaac')
    parser.add_argument('--capture-available', type=Path, help='capture only currently sealed checkpoints during training')
    parser.add_argument('--report', type=Path)
    args = parser.parse_args(argv)
    if sum(p is not None for p in (args.evaluate_run, args.verify_run, args.capture_available)) > 1:
        parser.error('choose one reuse mode')
    config = PPOConfig.read(args.config)
    physical, scale = SingleHandConfig.read(args.physics_config), ActionScale.read(args.action_scale)
    if args.capture_available:
        capture_available(args.capture_available.resolve(), physics_path=args.physics_config, scale_path=args.action_scale)
        return 0
    directory = args.verify_run or args.evaluate_run
    if directory is None:
        directory = args.output_dir/datetime.now(timezone.utc).strftime('run-%Y%m%dT%H%M%S-%f')
        directory.parent.mkdir(parents=True, exist_ok=True)
        directory = directory.resolve()
        print('VF02_TRAIN_RUN '+str(directory), flush=True)
        write_launch(directory, config_path=args.config, physics_path=args.physics_config, scale_path=args.action_scale)
        command = [sys.executable, '-m', 'vskill_os.adapters.isaac.learning.ppo_worker', '--output', str(directory),
            '--config', str(args.config.resolve()), '--physics-config', str(args.physics_config.resolve()),
            '--action-scale', str(args.action_scale.resolve())]
        run_worker(command, directory, 5400)
    directory = directory.resolve()
    if not args.verify_run and (directory/'training.json').exists() and not (directory/'failure.json').exists():
        capture_available(directory, physics_path=args.physics_config, scale_path=args.action_scale)
    report = verify_run(directory, config=config, physical=physical, scale=scale)
    write_json(directory/'learnability.json', report)
    if args.report:
        write_json(args.report, report)
    print(json.dumps(dict(accepted=report['accepted'], diagnostic_complete=report['diagnostic_complete'],
        learning_progress_observed=report['learning_progress_observed'], findings=report['findings'])), flush=True)
    return 0 if report['accepted'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
