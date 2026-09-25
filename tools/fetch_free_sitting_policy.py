#!/usr/bin/env python3
"""Fetch one hash-pinned official model; never silently accept changed weights."""
import argparse
import hashlib
import json
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def fetch(*, download: bool) -> Path:
    manifest = json.loads((ROOT / 'configs/simulation/free_sitting_policy.json').read_text())
    license_path = ROOT / manifest['license_file']
    if hashlib.sha256(license_path.read_bytes()).hexdigest() != manifest['license_sha256']:
        raise ValueError('model license fingerprint mismatch')
    path = ROOT / manifest['local_path']
    if not path.exists():
        if not download:
            raise FileNotFoundError(f'{path}: run with --fetch')
        with urllib.request.urlopen(manifest['url'], timeout=60) as response:
            content = response.read(manifest['size_bytes'] + 1)
        if len(content) != manifest['size_bytes'] or hashlib.sha256(content).hexdigest() != manifest['sha256']:
            raise ValueError('download does not match the pinned model')
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix('.download')
        temporary.write_bytes(content)
        temporary.replace(path)
    if path.stat().st_size != manifest['size_bytes'] or hashlib.sha256(path.read_bytes()).hexdigest() != manifest['sha256']:
        raise ValueError('local model does not match the pinned model; preserving it for inspection')
    return path


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fetch', action='store_true')
    args = parser.parse_args()
    print(fetch(download=args.fetch))
