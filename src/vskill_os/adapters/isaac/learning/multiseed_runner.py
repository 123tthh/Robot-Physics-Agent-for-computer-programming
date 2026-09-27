"""One isolated T03 seed per process/workspace; reuse the frozen T02 collectors.

The external launcher owns GPU assignment and separate conversion directories.
This runner never selects a better seed, changes the reward, or promotes a
candidate into the online skill runtime.
"""

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import sys

from ....learning.multiseed import MultiseedProtocol, summarize_seed
from ....learning.policy_manifest import ActionScale
from ....learning.ppo_config import PPOConfig
from .action_runner import run_worker
from .batch_recording import sha
from .config import SingleHandConfig
from .isolated_worker import asset_mirror, healthy_gpu, kit_arguments, same_cuda_uuid
from .ppo_runner import write_launch
from .ppo_verify import verify_evaluation, verify_training
from .single_hand_env import ROOT, write_json


def worker_command(kind, directory, forwarded):
    return [sys.executable, '-m', 'vskill_os.adapters.isaac.learning.isolated_worker',
        '--kind', kind, '--cache', str(directory.with_suffix('.kit')),
        '--receipt', str(directory.with_suffix('.worker.json')), '--', *forwarded]


def verify_seed(directory, *, protocol, seed, config, physical, scale):
    training = verify_training(directory, config, physical=physical, scale=scale)
    report = dict(version='vf-single-key-multiseed-seed/1', task='VF-02-T03', training_seed=seed,
        accepted=False, findings=list(training['findings']), training=training,
        recording=str(directory.resolve()), protocol=protocol.to_dict(),
        evaluator_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        privileged_state=True, camera_verified=False, real_robot_verified=False)
    if not training['training_valid']:
        return report
    last = training['summary']['checkpoints'][-1]
    evaluation = verify_evaluation(directory/'evaluation-final', directory/last['path'], physical=physical, scale=scale)
    statistics = summarize_seed(protocol, seed, evaluation)
    report.update(evaluation=evaluation, statistics=statistics, accepted=statistics['passed'])
    try:
        receipts = []
        launcher = ROOT/'src/vskill_os/adapters/isaac/learning/isolated_worker.py'
        for kind, path in (('train', directory), ('evaluate', directory/'evaluation-final')):
            receipt = json.loads(path.with_suffix('.worker.json').read_text())
            gpu = receipt['gpu']
            ecc = gpu['volatile_uncorrectable_ecc']
            healthy_gpu(receipt['cuda_visible_devices'],
                f"{gpu['uuid']}, {gpu['physical_index']}, {gpu['name']}, {'N/A' if ecc is None else ecc}")
            mirrored_assets = receipt.get('asset_mirror')
            if mirrored_assets is not None and asset_mirror(mirrored_assets['root']) != mirrored_assets:
                raise ValueError('asset mirror changed after collection')
            if (receipt['version'] != 'vf-isolated-worker/1' or receipt['kind'] != kind
                    or receipt['cuda_probe_passed'] is not True or receipt['launcher_sha256'] != sha(launcher)
                    or not same_cuda_uuid(receipt['cuda_visible_devices'], receipt['cuda_device_uuid'])
                    or receipt['kit_args'] != kit_arguments(path.with_suffix('.kit'), mirrored_assets)):
                raise ValueError('isolated worker launch binding differs')
            receipts.append(receipt)
        if receipts[0]['gpu'] != receipts[1]['gpu']:
            raise ValueError('training/evaluation GPU placement differs')
        if receipts[0].get('asset_mirror') != receipts[1].get('asset_mirror'):
            raise ValueError('training/evaluation asset mirror differs')
        report['worker_launches'] = receipts
    except (OSError, ValueError, KeyError, TypeError) as exc:
        report['accepted'] = False
        report['findings'].append('Worker launch evidence: '+str(exc))
    if not evaluation['evidence_valid']:
        report['findings'].append('Incomplete or invalid final physical evaluation')
    elif not statistics['passed']:
        report['findings'].append('Per-training-seed complete-cycle success rate is below 95%')
    return report


def aggregate(protocol, reports):
    """Require each frozen training seed separately, never a pooled threshold."""
    seeds = [r.get('training_seed') for r in reports]
    if seeds != list(protocol.training_seeds):
        raise ValueError('missing, duplicated, reordered or undeclared training seed')
    summaries = []
    for seed, report in zip(protocol.training_seeds, reports, strict=True):
        if MultiseedProtocol.from_dict(report['protocol']) != protocol:
            raise ValueError('different frozen protocol')
        if report.get('training', {}).get('training_valid') is not True:
            raise ValueError('missing independently checked training budget')
        config = PPOConfig.from_dict(report['training']['summary']['config'])
        if config != protocol.training_config(PPOConfig(), seed):
            raise ValueError('different training hyperparameters or initialization seed')
        summary = summarize_seed(protocol, seed, report['evaluation'])
        summary['passed'] = summary['passed'] and report.get('accepted') is True
        summaries.append(summary)
    return dict(version='vf-single-key-multiseed-suite/1', task='VF-02-T03',
        accepted=all(s['passed'] for s in summaries), protocol=protocol.to_dict(),
        seeds=summaries, training_transitions=sum(r['training']['summary']['transitions'] for r in reports),
        evaluation_episodes=sum(s['episode_count'] for s in summaries),
        minimum_seed_success_rate=min((s['success_rate'] for s in summaries if s['success_rate'] is not None), default=None),
        fixed_scene=True, privileged_state=True, held_out_geometry=False,
        camera_verified=False, real_robot_verified=False, published_runtime_skill=False,
        reports=[dict(training_seed=r['training_seed'], recording=r['recording']) for r in reports])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--protocol', type=Path, default=ROOT/'configs/learning/verification_feedback/multiseed_v1.json')
    parser.add_argument('--baseline', type=Path, default=ROOT/'configs/learning/verification_feedback/single_key_ppo_v1.json')
    parser.add_argument('--physics-config', type=Path, default=ROOT/'configs/learning/verification_feedback/single_hand_physics_v1.json')
    parser.add_argument('--action-scale', type=Path, default=ROOT/'configs/learning/verification_feedback/action_scale_v1.json')
    parser.add_argument('--seed', type=int)
    parser.add_argument('--run', type=Path)
    parser.add_argument('--aggregate-runs', type=Path, nargs=3, help='independently replay all three runs in frozen seed order')
    parser.add_argument('--report', type=Path)
    parser.add_argument('--verify-only', action='store_true')
    parser.add_argument('--training-timeout', type=float, default=10800)
    parser.add_argument('--evaluation-timeout', type=float, default=7200)
    args = parser.parse_args(argv)
    if any(not math.isfinite(t) or t <= 0 for t in (args.training_timeout, args.evaluation_timeout)):
        parser.error('timeouts must be finite and positive')
    protocol = MultiseedProtocol.read(args.protocol)
    if args.aggregate_runs:
        if args.run or args.seed is not None or not args.report:
            parser.error('--aggregate-runs requires --report and no --run/--seed')
        physical, scale = SingleHandConfig.read(args.physics_config), ActionScale.read(args.action_scale)
        reports = [verify_seed(path.resolve(), protocol=protocol, seed=seed,
            config=protocol.training_config(PPOConfig.read(args.baseline), seed), physical=physical, scale=scale)
            for path, seed in zip(args.aggregate_runs, protocol.training_seeds, strict=True)]
        result = aggregate(protocol, reports)
        write_json(args.report, result)
        print(json.dumps(result), flush=True)
        return 0 if result['accepted'] else 1
    if args.run is None or args.seed is None:
        parser.error('one seed requires --run and --seed')
    config = protocol.training_config(PPOConfig.read(args.baseline), args.seed)
    physical, scale = SingleHandConfig.read(args.physics_config), ActionScale.read(args.action_scale)
    directory = args.run.resolve()
    if directory.suffix:
        parser.error('run directory name must not contain a suffix; sibling evidence files share its stem')
    directory.parent.mkdir(parents=True, exist_ok=True)
    config_path = directory.with_suffix('.ppo.json')
    if not args.verify_only:
        if not directory.exists():
            config.write(config_path)
            write_launch(directory, config_path=config_path, physics_path=args.physics_config, scale_path=args.action_scale)
            write_json(directory.with_suffix('.placement.json'), dict(seed=args.seed,
                cuda_visible_devices=os.environ.get('CUDA_VISIBLE_DEVICES'), code_root=str(ROOT),
                protocol_sha256=sha(args.protocol), baseline_sha256=sha(args.baseline)))
            run_worker(worker_command('train', directory, [
                '--output', str(directory), '--config', str(config_path),
                '--physics-config', str(args.physics_config.resolve()), '--action-scale', str(args.action_scale.resolve())]),
                directory, args.training_timeout)
        training = verify_training(directory, config, physical=physical, scale=scale)
        if training['training_valid'] and not (directory/'evaluation-final').exists():
            last = training['summary']['checkpoints'][-1]
            run_worker(worker_command('evaluate', directory/'evaluation-final', [
                '--checkpoint', str(directory/last['path']), '--output', str(directory/'evaluation-final'),
                '--physics-config', str(args.physics_config.resolve()), '--action-scale', str(args.action_scale.resolve())]),
                directory/'evaluation-final', args.evaluation_timeout)
    report = verify_seed(directory, protocol=protocol, seed=args.seed, config=config, physical=physical, scale=scale)
    write_json(directory/'multiseed.json', report)
    if args.report:
        write_json(args.report, report)
    print(json.dumps(dict(seed=args.seed, accepted=report['accepted'], findings=report['findings'],
        statistics=report.get('statistics'))), flush=True)
    return 0 if report['accepted'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
