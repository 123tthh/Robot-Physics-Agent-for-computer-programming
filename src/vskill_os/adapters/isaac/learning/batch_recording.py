"""Bounded raw tensor chunks; NumPy is confined to the optional adapter."""

import gzip
import hashlib
import json
from pathlib import Path


RAW_FIELDS = ("q", "v", "targets", "key_q", "key_targets", "forces", "key_xyz", "tip", "pelvis", "torso")


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda:handle.read(1024*1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_gzip(path: Path, value: dict):
    path.parent.mkdir(parents=True,exist_ok=True)
    with gzip.open(path,"wt",encoding="utf-8") as stream:
        json.dump(value,stream,allow_nan=False,separators=(",",":"))


def read_gzip(path: Path) -> dict:
    from ....learning.serialization import _unique_object
    with gzip.open(path,"rt",encoding="utf-8") as stream:
        return json.load(stream,object_pairs_hook=_unique_object)


class RawChunks:
    def __init__(self, directory: Path, capacity: int):
        self.directory, self.capacity = directory, capacity
        self.pending, self.files, self.count = [], [], 0
        (directory/"raw").mkdir(parents=True,exist_ok=True)

    def append(self, raw: dict, global_tick: int, generations: list[int], local_ticks: list[int]):
        import numpy as np
        self.pending.append(dict(**raw,global_tick=np.asarray(global_tick,dtype=np.int64),
                                 generations=np.asarray(generations,dtype=np.int64),local_ticks=np.asarray(local_ticks,dtype=np.int64)))
        self.count += 1
        if len(self.pending) >= self.capacity:
            self.flush()

    def flush(self):
        import numpy as np
        if not self.pending:
            return
        path = self.directory/"raw"/f"chunk-{len(self.files):05d}.npz"
        np.savez_compressed(path,**{key:np.stack([sample[key] for sample in self.pending]) for key in self.pending[0]})
        self.files.append(dict(path=str(path.relative_to(self.directory)),samples=len(self.pending),sha256=sha(path)))
        self.pending.clear()


def save_snapshot(path: Path, raw: dict):
    import numpy as np
    np.savez_compressed(path,**raw)
