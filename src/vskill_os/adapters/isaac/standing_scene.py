"""Isolated Isaac 6.0.1 standing, two-index demo entry point.

No simulator is imported until version, source assets and exclusive occupancy
have passed. Source-checked only until an idle-machine physical run records its
own report; inherited P1/P2 reports are never reused as success evidence.
"""
from __future__ import annotations

import argparse
import faulthandler
from dataclasses import asdict
import hashlib
import json
import math
import os
import signal
from pathlib import Path

from vskill_os.standing_typing import StandingTypingPlanner
from .runtime_guard import RuntimeLease

ROOT = Path(__file__).resolve().parents[4]


def sha256(path: Path) -> str:
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def load_configuration(path: Path) -> dict:
    config = json.loads(path.read_text())
    if config['runtime']['isaac_sim_version'] != '6.0.1':
        raise ValueError('This entry point supports Isaac Sim 6.0.1 only')
    if config['scene']['chair'] or config['robot']['base_mode'] != 'fixed_standing':
        raise ValueError('Standing demo must use fixed standing base and no chair')
    if config['robot']['active_typing_fingers'] != ['left.index', 'right.index']:
        raise ValueError('Only two index fingers may type')
    if not config['runtime']['refuse_existing_simulator'] or not config['runtime']['refuse_busy_gpu']:
        raise ValueError('Resource isolation guards cannot be disabled')
    dt = config['scene']['physics_dt_s']
    if not math.isfinite(dt) or not 0 < dt <= 1/60:
        raise ValueError('Physics timestep must be finite and in (0, 1/60]')
    return config


def asset_evidence(usd: Path, manifest_path: Path, dependency_lock: Path | None = None) -> dict:
    manifest = json.loads(manifest_path.read_text())
    if 'usd_target' in manifest:
        expected = manifest['usd_target']['sha256']
    elif 'output_usd' in manifest:
        expected = next(item['sha256'] for item in manifest['layers']
                        if Path(item['path']).name == usd.name)
    else:
        raise ValueError('Expected pinned Dex3 asset manifest or a build report')
    actual = sha256(usd)
    if actual != expected:
        raise ValueError('Robot USD differs from the supplied asset manifest')
    if 'output_usd' in manifest:
        original_root = Path(manifest['output_usd']).parent
        entries = [{'path':str(Path(item['path']).relative_to(original_root)), 'sha256':item['sha256']}
                   for item in manifest['layers']]
    elif dependency_lock is not None:
        entries = json.loads(dependency_lock.read_text())['layers']
    else:
        raise ValueError('A trusted complete USD dependency lock is required')
    expected_layers = {item['path']:item['sha256'] for item in entries}
    actual_layers = {str(p.relative_to(usd.parent)):sha256(p)
                     for p in sorted(usd.parent.rglob('*')) if p.suffix in {'.usd','.usda','.usdc'}}
    if actual_layers != expected_layers:
        raise ValueError('USD dependency files are missing, added or differ from pinned hashes')
    # All sibling USD layers are hashed too, since hashing only the small root
    # layer cannot identify a change in robot physics or mesh dependencies.
    return {'usd': str(usd), 'sha256': actual, 'manifest': str(manifest_path),
            'dependencies_verified':True,
            'layers': [{'path': p, 'sha256': digest} for p,digest in actual_layers.items()]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=ROOT/'configs/simulation/dex3_standing_demo.json')
    parser.add_argument('--text')
    parser.add_argument('--seed', type=int)
    parser.add_argument('--headless', action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument('--device', choices=['cpu'], default='cpu',
                        help='Compatibility core adapter uses CPU PhysX; RTX images still require idle GPU')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--record-dir', type=Path)
    parser.add_argument('--no-cameras', action='store_true')
    parser.add_argument('--shared-cpu-physics', action='store_true', help='Explicit bounded no-render CPU check alongside identified resident PIDs')
    parser.add_argument('--resident-pid', type=int, action='append', default=[])
    parser.add_argument('--maximum-steps', type=int, default=30000)
    parser.add_argument('--maximum-phase-steps', type=int, default=1200)
    parser.add_argument('--robot-usd', type=Path)
    parser.add_argument('--asset-manifest', type=Path)
    parser.add_argument('--inspect-only', action='store_true', help='Starts Kit after guard; loads/samples scene without typing')
    args = parser.parse_args(argv)
    if args.shared_cpu_physics and (not args.headless or not args.no_cameras or not args.resident_pid or not 1 <= args.maximum_steps <= 3000):
        parser.error('shared CPU profile requires --headless --no-cameras --resident-pid PID and --maximum-steps 1..3000')
    if args.resident_pid and not args.shared_cpu_physics:
        parser.error('--resident-pid requires the explicit shared CPU profile')
    if args.maximum_steps <= 0:
        parser.error('--maximum-steps must be positive')
    if not 1 <= args.maximum_phase_steps <= 3000:
        parser.error('--maximum-phase-steps must be in 1..3000')
    config = load_configuration(args.config)
    output = args.output or ROOT/config['evidence']['output']
    record_dir = None if args.no_cameras else (args.record_dir or ROOT/config['evidence']['record_dir'])
    seed = config['task']['seed'] if args.seed is None else args.seed
    if seed < 0:
        parser.error('seed must be non-negative')
    plan = StandingTypingPlanner().plan_text(args.text if args.text is not None else config['task']['text'])
    usd = (args.robot_usd or ROOT/config['robot']['usd_path']).resolve()
    manifest = args.asset_manifest or ROOT/config['robot']['asset_manifest']
    report = {'schema':'standing_isaac_acceptance','version':'1.0.0',
              'status':'not_started','simulation_started':False,'physical_acceptance':False,
              'seed':seed,'plan':plan.to_dict(),'plan_id':plan.plan_id,
              'config':config,'config_sha256':sha256(args.config),
              'posture':'fixed_standing','chair_present':False,
              'free_standing_balance_verified':False,'camera_only_verification':False,
              'implementation_status':'source_checked_pending_physics_validation'}
    app = None
    driver = None
    code = 2
    def checkpoint(phase):
        report['last_phase'] = phase
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n')
        print(f'[standing] {phase}', flush=True)
    try:
        dependency_lock = config['robot'].get('asset_dependency_lock')
        report['assets'] = asset_evidence(usd, manifest, ROOT/dependency_lock if dependency_lock else None)
        runtime_root = Path(os.environ.get('ISAAC_PATH', config['runtime']['isaac_sim_root']))
        with RuntimeLease(runtime_root, resident_pids=tuple(args.resident_pid)) as check:
            report['runtime'] = check.to_dict()
            report['runtime']['explicit_shared_cpu_profile'] = args.shared_cpu_physics
            report['runtime']['permitted_resident_pids'] = args.resident_pid
            # Standalone python.sh configures extension search paths. Importing
            # regular package modules above never launches Kit or accesses CUDA.
            from isaacsim import SimulationApp
            checkpoint('starting_application')
            faulthandler.dump_traceback_later(60, repeat=True)
            app = SimulationApp({'headless':args.headless, 'width':640,'height':480,
                'renderer':'MinimalRendering' if args.no_cameras else 'RaytracedLighting',
                'minimal_shading_mode':4 if args.no_cameras else 0,
                # No-render startup cannot produce a viewport frame. Defer the
                # stage until after Kit's viewport-ready wait has been skipped.
                'create_new_stage':not args.no_cameras,
                'disable_viewport_updates':args.headless,'multi_gpu':False,'max_gpu_count':1,
                'anti_aliasing':0,'limit_cpu_threads':4,'fast_shutdown':True,
                'shutdown_watchdog_timeout':15.0})
            # Kit installs an immediate-exit SIGINT handler. Restore Python's
            # exception path so the controller can request physical release.
            signal.signal(signal.SIGINT, signal.default_int_handler)
            if args.no_cameras:
                from isaacsim.core.utils.stage import create_new_stage
                create_new_stage()
            report['simulation_started'] = True
            checkpoint('building_scene')
            try:
                from .standing_driver import Isaac6StandingDriver
                driver = Isaac6StandingDriver(usd,dt=config['scene']['physics_dt_s'],
                    render=not args.headless,record_dir=record_dir,seed=seed)
                report['initial_frame'] = asdict(driver.observe())
                checkpoint('scene_initialized')
                if args.inspect_only:
                    report['status'] = 'scene_inspected_only'
                    report['initial_frame'] = asdict(driver.observe())
                    code = 0
                else:
                    from vskill_os.standing_control import StandingTypingController
                    result = StandingTypingController(driver, maximum_total_steps=args.maximum_steps,
                        maximum_phase_steps=args.maximum_phase_steps,
                        tip_contact_offset_m=driver.tip_contact_offset_m,
                        maximum_press_depth_m=driver.maximum_press_depth_m).run(plan)
                    report['execution'] = result.to_dict()
                    report['final_frame'] = asdict(driver.observe())
                    report['physical_acceptance'] = result.verification.physical_acceptance and result.succeeded
                    report['implementation_status'] = 'physical_acceptance_for_reported_text_only' if report['physical_acceptance'] else 'physical_execution_failed'
                    report['status'] = 'passed' if report['physical_acceptance'] else 'failed'
                    code = 0 if report['physical_acceptance'] else 1
                report['physics_dt_s'] = driver.dt
                report['contact_geometry'] = {'tip_contact_offset_m':getattr(driver,'tip_contact_offset_m',None),
                    'maximum_press_depth_m':getattr(driver,'maximum_press_depth_m',None)}
                report['physics_steps'] = driver.tick
                report['frames_directory'] = str(record_dir) if record_dir else None
                report['sensor_basis'] = 'PhysX key travel and index/key contact pairs + event buffer; not OCR'
                checkpoint('execution_finished')
            except BaseException as exc:
                code = 130 if isinstance(exc, KeyboardInterrupt) else 2
                report['status'] = 'failed'
                report['physical_acceptance'] = False
                report['error'] = f'{type(exc).__name__}: {exc}'
            finally:
                cleanup_errors = []
                try:
                    if driver:
                        driver.close()
                except BaseException as exc:
                    cleanup_errors.append(f'driver: {type(exc).__name__}: {exc}')
                if cleanup_errors:
                    code = 2
                    report['status'] = 'failed'
                    report['physical_acceptance'] = False
                    report['error'] = 'Shutdown failed: ' + '; '.join(cleanup_errors)
                # Isaac 6 fast shutdown can call os._exit() and never execute
                # Python finally blocks. Persist evidence and exit status first.
                report['driver_close_completed'] = not cleanup_errors
                report['kit_shutdown'] = 'requested_process_exit_not_yet_observed'
                checkpoint('kit_shutdown_requested')
                try:
                    app.close(exit_code=code)
                    report['kit_shutdown'] = 'close_returned'
                except BaseException as exc:
                    cleanup_errors.append(f'Kit: {type(exc).__name__}: {exc}')
                app = None
                if cleanup_errors:
                    raise RuntimeError('Shutdown failed: ' + '; '.join(cleanup_errors))
    except BaseException as exc:
        code = 130 if isinstance(exc, KeyboardInterrupt) else 2
        report['status'] = 'failed' if report['simulation_started'] else 'blocked_before_launch'
        report['error'] = f'{type(exc).__name__}: {exc}'
        report['physical_acceptance'] = False
    finally:
        faulthandler.cancel_dump_traceback_later()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n')
    print(json.dumps({'status':report['status'],'report':str(output),
                      'simulation_started':report['simulation_started'],
                      'physical_acceptance':report['physical_acceptance']},ensure_ascii=False))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
