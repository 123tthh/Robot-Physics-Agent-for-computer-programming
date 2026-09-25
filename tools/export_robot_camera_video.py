"""Export recorded robot RGB frames at their measured simulation timestamps."""
import argparse
import json
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = json.loads(args.report.read_text())
    frames = [f for f in report['virtual_computer']['camera_frames'] if f['label'].isdigit()]
    final = next(f for f in report['virtual_computer']['camera_frames'] if f['label'] == '03-final')
    frames.append(final)
    frames.sort(key=lambda f: f['simulation_time_s'])
    if len(frames) < 2:
        raise ValueError('at least two timestamped camera frames required')
    listing = args.output.with_suffix('.concat.txt')
    entries = []
    for i, frame in enumerate(frames):
        path = str(Path(frame['rgb']).resolve())
        if "'" in path or '\n' in path:
            raise ValueError('unsupported filename for ffmpeg concat')
        entries.append(f"file '{path}'")
        end = frames[i + 1]['simulation_time_s'] if i + 1 < len(frames) else report['request']['duration']
        entries.append(f"duration {max(.005, end - frame['simulation_time_s']):.6f}")
    entries.append(f"file '{Path(frames[-1]['rgb']).resolve()}'")
    listing.write_text('\n'.join(entries) + '\n')
    subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'concat', '-safe', '0', '-i', str(listing),
                    '-vf', 'fps=20', '-c:v', 'libx264', '-crf', '20', '-pix_fmt', 'yuv420p',
                    '-movflags', '+faststart', '-t', str(report['request']['duration']), str(args.output)], check=True)
    args.output.with_suffix('.json').write_text(json.dumps({
        'source': 'recorded_robot_rgb', 'source_sampling_hz': 1,
        'display_fps': 20, 'intermediate_frames': 'previous_observation_held_no_synthetic_interpolation',
        'simulation_start_s': frames[0]['simulation_time_s'],
        'report': str(args.report), 'frame_count': len(frames)}, indent=2) + '\n')


if __name__ == '__main__':
    main()
