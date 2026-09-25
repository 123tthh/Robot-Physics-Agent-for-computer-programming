"""Read-only transport inspection and an importable single-world integration example.

The CLI never creates SimulationApp, World, cameras or GPU work. ``attach`` and
``central_monitor_cycle`` are helpers for an already-running, admitted scene.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from vskill_os.adapters.isaac.monitor_screen import (
    FrameReceiver,
    IsaacMonitorScreen,
    ScreenFrameError,
    _read_regular_file,
    find_robot_camera_mount,
)
from vskill_os.simulation.screen_feedback import publish_frame


def attach(stage, *, transport_dir: Path, output_dir: Path) -> IsaacMonitorScreen:
    """Call once after the existing world's reset; no new World/Kit instance."""
    monitor = IsaacMonitorScreen(stage, transport_dir=transport_dir, output_dir=output_dir)
    mount = find_robot_camera_mount(stage)
    monitor.add_camera('robot', eye_world=(0.42, 0.0, 1.22), target_world=(1.08, 0, 1.03), mount_prim_path=mount)
    monitor.add_camera('overview', eye_world=(-0.7, -1.15, 1.5), target_world=(0.7, 0, 0.9))
    return monitor


def central_monitor_cycle(world, monitor: IsaacMonitorScreen, desktop, *, maximum_steps: int = 4) -> dict:
    """Example central loop, not a second thread or timer.

    Fold this sequence into the main motion driver's existing central step when
    also moving the robot. Never run both loops against the same world at once.
    """
    if type(maximum_steps) is not int or not 1 <= maximum_steps <= 30:
        raise ValueError('maximum_steps must be in [1, 30]')
    pixels = desktop.capture()
    publish_frame(pixels, monitor.receiver.directory)
    submitted = monitor.update_from_transport(simulation_time_s=world.current_time,
                                              minimum_event_count=pixels.consumed_event_count)
    captures, failures = {}, {}
    for _ in range(maximum_steps):
        # The only simulation-clock advance in this entire example.
        world.step(render=True)
        for name in monitor.cameras:
            if name in captures:
                continue
            try:
                captures[name] = monitor.capture(name, physics_tick=world.current_time_step_index,
                                                 simulation_time_s=world.current_time)
                failures.pop(name, None)
            except ScreenFrameError as exc:
                failures[name] = str(exc)
        if len(captures) == len(monitor.cameras):
            break
    return {'submitted_frame': submitted, 'camera_captures': captures, 'capture_failures': failures,
            'robot_visual_verified': False,
            'next_gate': 'verify_robot_camera_text on the robot camera PNG with calibrated OCR regions'}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--transport', type=Path, required=True)
    parser.add_argument('--offline', action='store_true', help='Check archived integrity only; never claim live freshness')
    parser.add_argument('--robot-usd', type=Path, help='Optional CPU OpenUSD sensor-mount inspection; requires pxr')
    args = parser.parse_args()
    receiver = FrameReceiver(args.transport)
    now = None
    if args.offline:
        # Intentionally inspect archived bytes using their historical timestamp;
        # report this distinct from a live freshness test.
        manifest = json.loads(_read_regular_file(receiver.directory, 'frame.json', 16_384))
        now = manifest['captured_at_s']
    frame = receiver.receive(now_s=now)
    report = {'schema_version': 1, 'frame': frame.metadata(), 'transport_integrity_verified': True,
              'live_freshness_checked': not args.offline, 'simulation_started': False,
              'gpu_work_started': False, 'robot_visual_verified': False}
    if args.robot_usd:
        from pxr import Usd, UsdGeom

        stage = Usd.Stage.CreateInMemory()
        UsdGeom.Xform.Define(stage, '/World')
        robot = UsdGeom.Xform.Define(stage, '/World/G1')
        robot.GetPrim().GetReferences().AddReference(str(args.robot_usd.resolve()))
        report['camera_mount'] = find_robot_camera_mount(stage)
        report['usd_source_modified'] = False
    print(json.dumps(report, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
