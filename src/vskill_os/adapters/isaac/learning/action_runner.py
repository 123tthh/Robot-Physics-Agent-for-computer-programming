"""Bounded action health workers at 30/60/120 Hz; independent offline replay."""

import argparse
from dataclasses import replace
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from ....learning.policy_manifest import ActionScale
from .action_smoke import CASES_BY_DECIMATION
from .action_verify import verify_action_bundle
from .config import SingleHandConfig
from .single_hand_env import ROOT, write_json


def run_worker(command, directory, timeout):
    log_path = directory.with_suffix('.log')
    started = time.monotonic()
    process = dict(returncode=None, timed_out=False, interrupted=False, log=str(log_path))
    with log_path.open('w') as log:
        child = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            child.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process['timed_out'] = True
        except KeyboardInterrupt:
            process['interrupted'] = True
        finally:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGTERM)
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait(timeout=5)
            process['returncode'] = child.returncode
    process['wall_s'] = time.monotonic()-started
    write_json(directory/'process.json', process)
    return process


def verify_run(directory, physical, scale):
    report = dict(schema_version='vf-action-health-suite/1', task='VF-02-T01', accepted=False,
        recording=str(directory.resolve()), findings=[], cases=[], learned_policy=False, real_robot_verified=False,
        scope='fixed pelvis, single left index, normalized 23-D schema; Isaac PhysX at 30/60/120 Hz',
        limit_endpoint_evidence='synthetic CPU arithmetic only; no physical hard-stop collision test')
    try:
        leases, backend_tokens = [], []
        for decimation, cases in CASES_BY_DECIMATION.items():
            path = directory/f'd{decimation}'
            process = json.loads((path/'process.json').read_text())
            if (process.get('returncode') != 0 or process.get('timed_out') is not False
                    or process.get('interrupted') or (path/'failure.json').exists()):
                raise ValueError(f'worker failed, timed out or interrupted: d{decimation}')
            collection = json.loads((path/'action_collection.json').read_text())
            if collection['decimation'] != decimation or [c['case'] for c in collection['cases']] != list(cases):
                raise ValueError('incomplete frozen action collection')
            for i, name in enumerate(cases):
                result = verify_action_bundle(path/name, physical_config=replace(physical, decimation=decimation),
                                              scale=scale, case=name, seed=7+i)
                result['decimation'] = decimation
                write_json(path/name/'action_evaluation.json', result)
                report['cases'].append(result)
                leases.extend(result.get('lease_tokens', []))
                backend_tokens.append(result.get('backend_token'))
                if not result['accepted']:
                    report['findings'].append(f'd{decimation}/{name}: '+str(result['findings'] or result.get('physical', {}).get('findings')))
                print('VF02_RESULT '+json.dumps(dict(decimation=decimation, case=name, accepted=result['accepted'],
                    mapping=result['mapping_passed'], physical=result['physical_primitive_passed'], findings=result['findings'])), flush=True)
        if len(leases) != len(set(leases)) or len(backend_tokens) != len(set(backend_tokens)):
            report['findings'].append('control ownership reused across resets/workers')
        report['accepted'] = not report['findings']
    except (OSError, ValueError, KeyError, TypeError) as exc:
        report['findings'].append(str(exc))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=ROOT/'artifacts/verification_feedback/vf02/action_health')
    parser.add_argument('--physics-config', type=Path, default=ROOT/'configs/learning/verification_feedback/single_hand_physics_v1.json')
    parser.add_argument('--action-scale', type=Path, default=ROOT/'configs/learning/verification_feedback/action_scale_v1.json')
    parser.add_argument('--timeout-s', type=float, default=600.)
    parser.add_argument('--verify-run', type=Path)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args(argv)
    if not math.isfinite(args.timeout_s) or args.timeout_s <= 0:
        parser.error('timeout must be finite and positive')
    physical, scale = SingleHandConfig.read(args.physics_config), ActionScale.read(args.action_scale)
    if args.verify_run:
        directory = args.verify_run.resolve()
    else:
        run_id = datetime.now(timezone.utc).strftime('run-%Y%m%dT%H%M%S-%f')
        directory = (args.output_dir/run_id).resolve()
        directory.mkdir(parents=True, exist_ok=False)
        print('VF02_RUN '+str(directory), flush=True)
        for decimation in CASES_BY_DECIMATION:
            path = directory/f'd{decimation}'
            command = [sys.executable, '-m', 'vskill_os.adapters.isaac.learning.action_smoke',
                '--output', str(path), '--decimation', str(decimation),
                '--physics-config', str(args.physics_config.resolve()), '--action-scale', str(args.action_scale.resolve())]
            process = run_worker(command, path, args.timeout_s)
            if process['returncode'] or process['timed_out'] or process['interrupted'] or (path/'failure.json').exists():
                break
    report = verify_run(directory, physical, scale)
    write_json(directory/'action_suite.json', report)
    if args.report:
        write_json(args.report, report)
    print(json.dumps(dict(accepted=report['accepted'], findings=report['findings'], report=str(directory/'action_suite.json'))), flush=True)
    return 0 if report['accepted'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
