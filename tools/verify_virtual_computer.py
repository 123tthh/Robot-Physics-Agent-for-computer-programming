"""Independent physical-command / PTY / robot-camera acceptance.

The selected goal is deliberately small: physically type ls + Enter and observe
the fixture filename through the robot RGB camera. No code-agent claim is made.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

from verify_continuous_typing import verify as verify_physics


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def ocr(path):
    result = subprocess.run(['tesseract', str(path), 'stdout', '--psm', '11'], capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError('OCR_PROCESS_FAILED: ' + result.stderr)
    return result.stdout


def verify(path):
    import numpy as np
    from PIL import Image
    report = json.loads(path.read_text())
    physical = verify_physics(path, require_all_ten=False)
    desktop = report['virtual_computer']
    frames = desktop['camera_frames']
    initial = next(f for f in frames if f['label'] == '04-start-hands')
    final = next(f for f in frames if f['label'] == '03-final')
    before = ocr(initial['rgb'])
    after = ocr(final['rgb'])
    events = desktop['events']
    depth = np.load(final['depth'], allow_pickle=False)
    depth_valid_fraction = float(np.mean(np.isfinite(depth) & (depth >= .02) & (depth <= 20.)))
    with Image.open(final['rgb']) as rgb_image:
        rgb_shape = rgb_image.size[::-1]
    down = [e for e in events if e['event_type'] == 'keydown']
    marker = 'KEYBOARD_OK'
    # OCR can insert spaces around punctuation; preserve letters and digits.
    readable = lambda text: ''.join(c for c in text if c.isalnum())
    gates = {
        'physical_command_and_sitting': physical['succeeded'],
        'requested_command': report['typing']['plan']['text'] == 'ls\n',
        'all_physical_events_forwarded_unchanged': events == report['typing']['events'],
        'correct_keydown_sequence': [e['key'] for e in down] == ['l', 's', 'enter'],
        'contact_event_source': bool(events) and all(e['source'] == 'keyboard_event_bridge' and
            e['reason'] == {'keydown': 'physical_threshold', 'keyup': 'release_threshold'}.get(e['event_type']) for e in events),
        'camera_hashes_match': all(sha(f['rgb']) == f['rgb_sha256'] and sha(f['depth']) == f['depth_sha256'] for f in (initial, final)),
        'camera_output_absent_before_input': readable(marker) not in readable(before),
        'camera_output_visible_after_input': readable(marker) in readable(after),
        'capture_after_enter': bool(down) and final['simulation_time_s'] > down[-1]['monotonic_time'],
        'pty_contains_real_output': marker in desktop['frames'][-1]['text'],
        'texture_pixel_evidence_matches': all(sha(f['png_path']) == f['png_sha256'] and
            hashlib.sha256(Image.open(f['png_path']).convert('RGBA').tobytes()).hexdigest() == f['rgba_sha256'] for f in desktop['frames']),
        'depth_sensor_has_finite_positive_measurements': depth_valid_fraction > .5,
        'depth_aligned_to_rgb_dimensions': depth.ndim in (2, 3) and depth.shape[:2] == rgb_shape,
        'fixed_head_camera_mount': desktop['camera_mount']['pose_source'] == 'measured_physx_link_pose_times_constant_mount',
    }
    return {'succeeded': all(gates.values()), 'gates': gates, 'physical': physical,
            'ocr': {'source': 'tesseract_robot_rgb', 'before': before, 'after': after,
                    'initial_image': initial['rgb'], 'final_image': final['rgb']},
            'camera_ocr_verified': gates['camera_output_absent_before_input'] and gates['camera_output_visible_after_input'] and gates['camera_hashes_match'],
            'all_ten_fingers_in_this_run': False, 'code_agent_executed': False,
            'depth_valid_fraction': depth_valid_fraction,
            'report': str(path), 'report_sha256': sha(path)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        result = verify(args.report)
    except (KeyError, ValueError, OSError, RuntimeError, StopIteration, subprocess.TimeoutExpired) as exc:
        result = {'succeeded': False, 'error': str(exc)}
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    return 0 if result['succeeded'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
