"""Read-only Isaac installation/occupancy checks; never stops another process.

The lease serializes this project's launches. External launchers cannot honor
our lock, so process/GPU checks remain best-effort and fail closed on uncertainty.
No Isaac, CUDA or third-party package is imported by this module.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import fcntl
import json
import os
from pathlib import Path
import subprocess
from typing import Iterable

DEFAULT_ISAAC_ROOT = Path('/home/descfly/isaac-sim-standalone-6.0.1-linux-x86_64')


@dataclass(frozen=True)
class RuntimeCheck:
    root: str
    version: str
    installation_valid: bool
    occupancy_known: bool
    busy_processes: tuple[str, ...]
    reasons: tuple[str, ...]

    @property
    def launch_allowed(self) -> bool:
        return self.installation_valid and self.occupancy_known and not self.busy_processes and not self.reasons

    def to_dict(self) -> dict:
        return {**asdict(self), 'launch_allowed': self.launch_allowed, 'simulation_started': False}


def is_simulator_process(arguments: Iterable[str], *, own_pid: bool = False) -> bool:
    """Inspect actual executable/script tokens, never an embedded shell command."""
    argv = tuple(arguments)
    if own_pid or not argv:
        return False
    executable = Path(argv[0]).name.lower()
    if executable in {'kit', 'isaac-sim', 'isaac-sim.sh', 'isaac-sim.streaming.sh'}:
        return True
    if executable.startswith('python'):
        if any('runtime_guard' in arg for arg in argv[1:]):
            return False
        if 'isaac-sim' in argv[0].lower() or '/kit/python/' in argv[0]:
            return True
        for arg in argv[1:]:
            if arg == '-c':
                break
            if ('isaac' in arg.lower() or 'standing_scene' in arg) and 'runtime_guard' not in arg:
                return True
    return False


def inspect_runtime(root: str | Path = DEFAULT_ISAAC_ROOT) -> RuntimeCheck:
    root = Path(root).resolve()
    version = (root / 'VERSION').read_text().strip() if (root / 'VERSION').is_file() else ''
    valid = version.split('-', 1)[0].split('+', 1)[0] == '6.0.1' and (root / 'python.sh').is_file()
    reasons = [] if valid else ['ISAAC_6_0_1_INSTALLATION_MISSING_OR_VERSION_MISMATCH']
    busy: list[str] = []
    known = True
    try:
        # In a PID sandbox, absence of host processes is not proof of idleness.
        init_command = Path('/proc/1/cmdline').read_bytes().decode(errors='replace')
        if 'codex-linux-sandbox' in init_command:
            known = False
            reasons.append('HOST_PROCESS_VISIBILITY_UNAVAILABLE')
        for process in Path('/proc').iterdir():
            if not process.name.isdigit() or int(process.name) == os.getpid():
                continue
            try:
                argv = process.joinpath('cmdline').read_bytes().decode(errors='replace').split('\0')
            except FileNotFoundError:
                continue
            except PermissionError:
                known = False
                continue
            if is_simulator_process(argv):
                busy.append(f'pid={process.name} executable={Path(argv[0]).name}')
        query = subprocess.run(
            ['nvidia-smi', '--query-compute-apps=pid,process_name', '--format=csv,noheader'],
            capture_output=True, text=True, timeout=5, check=False,
        )
        if query.returncode != 0:
            known = False
            reasons.append('GPU_OCCUPANCY_UNAVAILABLE')
        else:
            busy.extend(f'gpu_compute={line.strip()}' for line in query.stdout.splitlines() if line.strip())
    except (OSError, subprocess.TimeoutExpired):
        known = False
        reasons.append('OCCUPANCY_CHECK_FAILED')
    if busy:
        reasons.append('EXISTING_SIMULATOR_OR_GPU_COMPUTE_WORKLOAD')
    return RuntimeCheck(str(root), version, valid, known, tuple(sorted(set(busy))), tuple(reasons))


class RuntimeLease:
    """Hold for the entire lifetime of Kit, including scene import and cleanup."""
    def __init__(self, root: str | Path = DEFAULT_ISAAC_ROOT):
        self.root = Path(root)
        self._lock = None

    def __enter__(self):
        lock_path = Path('/tmp') / f'vskill-isaac-{os.getuid()}.lock'
        self._lock = lock_path.open('a+')
        try:
            fcntl.flock(self._lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            check = inspect_runtime(self.root)
            if not check.launch_allowed:
                raise RuntimeError('Isaac launch refused: ' + json.dumps(check.to_dict(), ensure_ascii=False))
            return check
        except BaseException:
            self._lock.close()
            self._lock = None
            raise

    def __exit__(self, *_):
        if self._lock is not None:
            self._lock.close()
            self._lock = None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--isaac-root', type=Path, default=DEFAULT_ISAAC_ROOT)
    parser.add_argument('--check-only', action='store_true', help='Report only; never starts Kit')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    check = inspect_runtime(args.isaac_root)
    report = json.dumps(check.to_dict(), ensure_ascii=False, indent=2) + '\n'
    print(report, end='')
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report)
    return 0 if args.check_only or check.launch_allowed else 2


if __name__ == '__main__':
    raise SystemExit(main())
