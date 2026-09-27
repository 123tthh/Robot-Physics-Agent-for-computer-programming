"""Replay each T03 run in its recorded workspace without relaxing evidence checks."""

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from ....learning.serialization import _unique_object
from .batch_recording import sha
from .single_hand_env import ROOT, write_json


# The collector's own source checks still run in the child. Also require the
# independently maintained verifiers and orchestration to match the coordinator.
REPLAY_SOURCES = (
    'src/vskill_os/adapters/isaac/learning/multiseed_runner.py',
    'src/vskill_os/adapters/isaac/learning/multiseed_replay.py',
    'src/vskill_os/adapters/isaac/learning/ppo_verify.py',
    'src/vskill_os/adapters/isaac/learning/verify.py',
    'src/vskill_os/learning/physical_evaluation.py',
    'src/vskill_os/learning/evaluation.py',
    'src/vskill_os/learning/action_evaluation.py',
    'src/vskill_os/learning/multiseed.py',
)


def read_json(path):
    return json.loads(path.read_text(), object_pairs_hook=_unique_object)


def replay_seed(directory, seed, *, inputs, destination, timeout_s):
    """A fresh process must import the run's sources, not the parent's ROOT."""
    directory = directory.resolve()
    receipt = dict(training_seed=seed, recording=str(directory), completed=False)
    started = time.monotonic()
    try:
        placement_path = directory.with_suffix('.placement.json')
        placement = read_json(placement_path)
        root = Path(placement['code_root']).resolve(strict=True)
        if (placement['seed'] != seed or not directory.is_relative_to(root)
                or placement['protocol_sha256'] != sha(inputs['protocol'])
                or placement['baseline_sha256'] != sha(inputs['baseline'])):
            raise ValueError('WORKSPACE_BINDING: run, seed or frozen configuration differs')
        verifier_sources = {name: sha(ROOT/name) for name in REPLAY_SOURCES}
        if {name: sha(root/name) for name in REPLAY_SOURCES} != verifier_sources:
            raise ValueError('VERIFIER_SOURCE_HASH: workspace verifier differs from coordinator')
        report_path = destination/f'seed-{seed}.json'
        log_path = destination/f'seed-{seed}.log'
        command = [sys.executable, '-m', 'vskill_os.adapters.isaac.learning.multiseed_runner',
            '--seed', str(seed), '--run', str(directory), '--verify-only', '--report', str(report_path)]
        for name, path in inputs.items():
            command.extend(['--'+name, str(path)])
        env = dict(os.environ)
        env['PYTHONPATH'] = os.pathsep.join(filter(None, (str(root/'src'), env.get('PYTHONPATH'))))
        receipt.update(workspace=str(root), placement_sha256=sha(placement_path),
            verifier_sources=verifier_sources, command=command, report=str(report_path), log=str(log_path))
        with log_path.open('x') as log:
            process = subprocess.run(command, cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT,
                                     timeout=timeout_s, check=False)
        receipt['returncode'] = process.returncode
        report = read_json(report_path)
        if (process.returncode not in (0, 1) or type(report.get('accepted')) is not bool
                or (process.returncode == 0) != report['accepted']
                or report.get('version') != 'vf-single-key-multiseed-seed/1'
                or report.get('training_seed') != seed or report.get('recording') != str(directory)
                or report.get('evaluator_sha256') != verifier_sources[REPLAY_SOURCES[0]]):
            raise ValueError('REPLAY_REPORT_BINDING: fresh report identity or process status differs')
        receipt.update(completed=True, report_sha256=sha(report_path))
        return report, receipt
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as exc:
        receipt['error'] = str(exc)
        return None, receipt
    finally:
        receipt['wall_s'] = time.monotonic()-started
        write_json(destination/f'seed-{seed}.replay.json', receipt)


def replay_runs(directories, *, protocol, inputs, report_path, timeout_s, workers):
    """Keep fresh replay reports/logs beside the suite; never reuse old success flags."""
    destination = report_path.parent/(report_path.stem+'.replays')
    if report_path.exists():
        raise ValueError('choose a new suite report path; preserve previous verification results')
    destination.mkdir(parents=True, exist_ok=False)
    inputs = {name: path.resolve(strict=True) for name, path in inputs.items()}
    directories = [path.resolve(strict=True) for path in directories]
    if len(set(directories)) != len(protocol.training_seeds):
        raise ValueError('duplicate run directories')

    def replay(item):
        directory, seed = item
        return replay_seed(directory, seed, inputs=inputs, destination=destination, timeout_s=timeout_s)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        results = list(executor.map(replay, zip(directories, protocol.training_seeds, strict=True)))
    return [report for report, _ in results], [receipt for _, receipt in results]
