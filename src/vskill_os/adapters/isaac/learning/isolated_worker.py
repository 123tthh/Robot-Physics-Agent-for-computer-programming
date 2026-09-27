"""Pin one healthy GPU and local Kit state around the unchanged T02 worker.

Only launch settings change. Training, physics, inference, recording and their
source hashes remain those of T02. Kit receives its own arguments via sys.argv;
the original worker parses an explicit, separate argument list.
"""

import argparse
import csv
import importlib
import os
from pathlib import Path
import re
import subprocess
import sys

from .batch_recording import sha
from .single_hand_env import write_json


def healthy_gpu(visible, query_output):
    if not re.fullmatch(r'GPU-[0-9a-fA-F-]{36}', visible):
        raise ValueError('CUDA_VISIBLE_DEVICES must name exactly one full GPU UUID')
    rows = list(csv.reader(query_output.strip().splitlines(), skipinitialspace=True))
    if len(rows) != 1 or len(rows[0]) != 4 or rows[0][0] != visible:
        raise ValueError('GPU inventory does not match the requested UUID')
    uuid, index, name, ecc = rows[0]
    # N/A is allowed for hardware without ECC; an unknown query error is not.
    if ecc not in ('0', '[N/A]', 'N/A', '[Not Supported]'):
        raise ValueError(f'GPU has uncorrectable ECC errors or unknown health: {ecc}')
    return dict(uuid=uuid, physical_index=int(index), name=name,
                volatile_uncorrectable_ecc=None if ecc != '0' else 0)


def kit_arguments(cache):
    return ['--portable-root', str(cache.resolve()),
        '--/renderer/multiGpu/enabled=false', '--/renderer/multiGpu/autoEnable=false',
        '--/plugins/carb.tasking.plugin/threadCount=8',
        '--/plugins/omni.tbb.globalcontrol/maxThreadCount=8',
        '--/app/settings/persistent=false']


def same_cuda_uuid(visible, observed):
    # PyTorch 2.7 returns the raw UUID; nvidia-smi prepends "GPU-".
    return visible.removeprefix('GPU-') == str(observed).removeprefix('GPU-')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kind', choices=('train', 'evaluate'), required=True)
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--receipt', type=Path, required=True)
    parser.add_argument('worker_args', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    forwarded = args.worker_args[1:] if args.worker_args[:1] == ['--'] else args.worker_args
    visible = os.environ.get('CUDA_VISIBLE_DEVICES', '')
    query = subprocess.run(['nvidia-smi', '--id='+visible,
        '--query-gpu=uuid,index,name,ecc.errors.uncorrected.volatile.total', '--format=csv,noheader,nounits'],
        check=True, capture_output=True, text=True, timeout=15)
    gpu = healthy_gpu(visible, query.stdout)
    args.cache.mkdir(parents=True, exist_ok=True)
    if args.receipt.exists():
        raise ValueError('never overwrite a worker launch receipt')
    cache_environment = {name: str(args.cache.resolve()/folder) for name, folder in (
        ('XDG_CACHE_HOME', 'xdg'), ('WARP_CACHE_PATH', 'warp'),
        ('CUDA_CACHE_PATH', 'cuda'), ('TORCHINDUCTOR_CACHE_DIR', 'torchinductor'), ('TMPDIR', 'tmp'))}
    for name, path in cache_environment.items():
        Path(path).mkdir(parents=True, exist_ok=True)
        os.environ[name] = path
    receipt = dict(version='vf-isolated-worker/1', kind=args.kind, gpu=gpu,
        cuda_visible_devices=visible, worker_args=forwarded, kit_args=kit_arguments(args.cache),
        launcher_sha256=sha(Path(__file__)), cache_environment=cache_environment, cuda_probe_passed=False)
    write_json(args.receipt, receipt)
    import torch
    observed_uuid = str(torch.cuda.get_device_properties(0).uuid)
    if torch.cuda.device_count() != 1 or not same_cuda_uuid(visible, observed_uuid):
        raise RuntimeError('Torch did not select exactly the requested GPU')
    value = torch.ones(8, device='cuda:0').sum().item()
    if value != 8:
        raise RuntimeError('CUDA arithmetic probe failed')
    receipt.update(cuda_probe_passed=True, torch_version=str(torch.__version__), cuda_device_uuid=observed_uuid)
    write_json(args.receipt, receipt)
    # SimulationApp appends unknown sys.argv values after its default settings.
    # No monkey-patching of the frozen worker or physics implementation.
    sys.argv = [sys.argv[0], *receipt['kit_args']]
    module = '.ppo_worker' if args.kind == 'train' else '.ppo_evaluate'
    return importlib.import_module(module, __package__).main(forwarded)


if __name__ == '__main__':
    raise SystemExit(main())
