"""Bounded T05 collection and CPU-only independent desktop/camera replay."""
import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from ....learning.camera_feedback import DesktopCameraSpec
from ....learning.environment import ResetSpec
from .config import SingleHandConfig
from .desktop_env import CASES
from .desktop_verify import verify_desktop_bundle
from .single_hand_env import ROOT, write_json


def verify_run(directory, spec, physical, process):
    report = dict(schema_version='vf-desktop-camera-suite/1', task='VF-01-T05', accepted=False,
        recording=str(directory.resolve()), process=process, cases=[], findings=[], learned_policy=False,
        real_robot_verified=False, code_agent_executed=False,
        scope='fixed-pelvis direct-joint single a; real PTY terminal; Isaac robot RGB-D camera')
    try:
        if (process.get('returncode') != 0 or process.get('timed_out') is not False
                or process.get('interrupted') or (directory/'failure.json').exists()):
            raise ValueError('desktop worker failed, interrupted or timed out')
        collection = json.loads((directory/'desktop_collection.json').read_text())
        if [c['case'] for c in collection['cases']] != list(CASES):
            raise ValueError('incomplete frozen desktop suite')
        process_ids = set()
        for index, name in enumerate(CASES):
            result = verify_desktop_bundle(directory/name, spec=spec, physical_config=physical)
            expected_pass = name in ('visible_input', 'visible_after_reset')
            required_failure = {'focus_lost': 'DESKTOP_TEXT_INCORRECT', 'frozen_texture': 'DISPLAY_TEXTURE_STALE',
                'missing_camera': 'CAMERA_FINAL_UNCONFIRMED', 'finish_before_camera': 'CAMERA_FINAL_UNCONFIRMED'}.get(name)
            matched = (result['binding_passed'] and result['physical_primitive_passed']
                       and result['end_to_end_passed'] == expected_pass
                       and (required_failure is None or required_failure in result['task_failures']))
            if (ResetSpec.read(directory/name/'reset.json').seed != 7+index
                    or collection['cases'][index]['seed'] != 7+index):
                matched = False
                result['findings'].append('frozen diagnostic seed changed')
            if result.get('terminal_pid') in process_ids:
                matched = False
                result['findings'].append('terminal process reused across episodes')
            process_ids.add(result.get('terminal_pid'))
            result.update(case=name, expected_end_to_end_passed=expected_pass, acceptance_passed=matched)
            write_json(directory/name/'desktop_evaluation.json', result)
            report['cases'].append(result)
            if not matched: report['findings'].append(name+': '+str(result['findings'] or result['task_failures']))
            print('VF05_RESULT '+json.dumps(dict(case=name, accepted=matched, end_to_end=result['end_to_end_passed'],
                findings=result['findings'], task_failures=result['task_failures'])), flush=True)
        report['accepted'] = not report['findings']
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.TimeoutExpired) as exc:
        report['findings'].append(str(exc))
    return report


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
                try: child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait(timeout=5)
            process['returncode'] = child.returncode
    process['wall_s'] = time.monotonic()-started
    write_json(directory/'process.json', process)
    return process


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=ROOT/'artifacts/verification_feedback/vf01/desktop_camera')
    parser.add_argument('--physics-config', type=Path, default=ROOT/'configs/learning/verification_feedback/single_hand_physics_v1.json')
    parser.add_argument('--desktop-config', type=Path, default=ROOT/'configs/learning/verification_feedback/desktop_camera_v1.json')
    parser.add_argument('--timeout-s', type=float, default=600.)
    parser.add_argument('--verify-run', type=Path)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args(argv)
    if not math.isfinite(args.timeout_s) or args.timeout_s <= 0:
        parser.error('timeout must be finite and positive')
    spec, physical = DesktopCameraSpec.read(args.desktop_config), SingleHandConfig.read(args.physics_config)
    if args.verify_run:
        directory = args.verify_run.resolve()
        process = json.loads((directory/'process.json').read_text())
    else:
        run_id = datetime.now(timezone.utc).strftime('run-%Y%m%dT%H%M%S-%f')
        directory = (args.output_dir/run_id).resolve()
        args.output_dir.mkdir(parents=True, exist_ok=True)
        command = [sys.executable, '-m', 'vskill_os.adapters.isaac.learning.desktop_smoke', '--output', str(directory),
                   '--physics-config', str(args.physics_config.resolve()), '--desktop-config', str(args.desktop_config.resolve())]
        print('VF05_RUN '+str(directory), flush=True)
        process = run_worker(command, directory, args.timeout_s)
    report = verify_run(directory, spec, physical, process)
    write_json(directory/'desktop_suite.json', report)
    if args.report: write_json(args.report, report)
    print(json.dumps(dict(accepted=report['accepted'], findings=report['findings'], report=str(directory/'desktop_suite.json'))), flush=True)
    return 0 if report['accepted'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
