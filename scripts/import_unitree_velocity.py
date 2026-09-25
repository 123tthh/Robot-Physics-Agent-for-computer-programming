"""Restore the pinned official donor from an existing checkout; no simulator."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

ROOT=Path(__file__).resolve().parents[1]


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,default=Path('/home/descfly/projects/unitree_rl_lab'))
    args=p.parse_args()
    cfg=json.loads((ROOT/'configs/robots/unitree_g1_velocity_candidate.json').read_text())
    verified=[]
    for name,entry in cfg['files'].items():
        source=args.source/entry['repository_path']
        if hashlib.sha256(source.read_bytes()).hexdigest()!=entry['sha256']:
            raise ValueError(f'{name} does not match the pinned donor')
        destination=ROOT/entry['project_path']
        verified.append((source,destination))
    for source,destination in verified:
        destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(source,destination)
    print(json.dumps({'imported_files':len(verified),'simulation_started':False,'physical_walking_verified':False}))


if __name__=='__main__':main()
