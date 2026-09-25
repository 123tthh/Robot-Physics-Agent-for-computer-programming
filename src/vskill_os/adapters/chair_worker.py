"""Linux JSONL bridge to an isolated mjlab/Isaac training worker.

This is a transport adapter, not an implementation of chair contact physics.
Worker stdout is protocol only; diagnostics go to stderr. No shell execution.
"""

from __future__ import annotations

from dataclasses import asdict
import json
import math
import os
import selectors
import subprocess
import time
from typing import Any

from ..chair.model import ChairSample, Observation
from ..chair.training import BackendFrame, ResetSpec, TrainingConfig
from ..control.commands import RootVelocityCommand


class ChairWorkerBackend:
    def __init__(self, argv: list[str], *, timeout_s: float = 30.0) -> None:
        if not argv or not all(isinstance(s, str) and s for s in argv):
            raise ValueError("worker argv must be a nonempty string list")
        if not math.isfinite(timeout_s) or not 0 < timeout_s <= 60:
            raise ValueError("worker timeout must be in (0, 60] seconds")
        self.timeout_s, self.counter, self.buffer = timeout_s, 0, b""
        self.process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.process.stdout, selectors.EVENT_READ)
        self.closed = False
        try:
            self.identity = self._call("hello", {"schema": "chair-worker-v1"})
            for key, expected in {"schema": "chair-worker-v1", "actor_dimension": 96,
                                  "critic_dimension": 217, "action_dimension": 29,
                                  "policy_dt_s": 0.02}.items():
                if self.identity.get(key) != expected:
                    raise ValueError(f"worker handshake mismatch: {key}")
            self.evidence_level = self.identity.get("evidence_level")
            if self.evidence_level != "simulation_oracle":
                raise ValueError("training worker must explicitly identify simulation oracle evidence")
            if self.identity.get("pelvis_fixed") is not False or self.identity.get("chair_actuated") is not False:
                raise ValueError("training requires an unfixed pelvis and a passive chair")
            for key in ("robot_asset_sha256", "chair_asset_sha256"):
                digest = self.identity.get(key, "")
                if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                    raise ValueError(f"worker must pin {key}")
            for key in ("simulator_version", "worker_commit", "joint_mapping_version"):
                if not self.identity.get(key) or "PIN_" in str(self.identity[key]):
                    raise ValueError(f"worker must pin {key}")
        except BaseException:
            self.close()
            raise

    def _call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if self.closed:
            raise RuntimeError("worker is closed")
        self.counter += 1
        request = json.dumps({"id": self.counter, "method": method, "params": params}, allow_nan=False).encode() + b"\n"
        if len(request) > 4096:
            raise ValueError("worker request exceeds atomic pipe budget")
        self.process.stdin.write(request)
        self.process.stdin.flush()
        deadline = time.monotonic() + self.timeout_s
        while b"\n" not in self.buffer:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not self.selector.select(remaining):
                self.close()
                raise TimeoutError(f"chair worker timed out during {method}")
            data = os.read(self.process.stdout.fileno(), 65536)
            if not data:
                self.close()
                raise RuntimeError("chair worker exited before responding")
            self.buffer += data
            if len(self.buffer) > 2_000_000:
                self.close()
                raise ValueError("chair worker response too large")
        line, self.buffer = self.buffer.split(b"\n", 1)
        response = json.loads(line)
        if response.get("id") != self.counter or "error" in response:
            raise RuntimeError(f"invalid worker response: {response.get('error', 'request id mismatch')}")
        value = response.get("result")
        if not isinstance(value, dict):
            raise ValueError("worker result must be an object")
        return value

    @staticmethod
    def _frame(value: dict) -> BackendFrame:
        raw = dict(value["sample"])
        for key in ("robot_pose", "chair_pose", "foot_contacts"):
            raw[key] = tuple(raw[key])
        return BackendFrame(
            Observation.pack(value["actor"], value["clean_actor"], value["privileged"]),
            ChairSample(**raw), tuple(value["joint_torques_nm"]),
            tuple(value["joint_velocities_rps"]), tuple(value["foot_slip_speed_mps"]),
        )

    def reset(self, spec: ResetSpec, command: RootVelocityCommand) -> BackendFrame:
        return self._frame(self._call("reset", {"reset": asdict(spec), "command": asdict(command)}))

    def read(self, command: RootVelocityCommand) -> BackendFrame:
        return self._frame(self._call("read", {"command": asdict(command)}))

    def step(self, action: tuple[float, ...], command: RootVelocityCommand) -> BackendFrame:
        return self._frame(self._call("step", {"action": action, "command": asdict(command)}))

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.selector.close()
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
        self.process.stdin.close()
        self.process.stdout.close()


def create_backend(config: TrainingConfig, options: dict) -> ChairWorkerBackend:
    del config
    return ChairWorkerBackend(options["argv"], timeout_s=options.get("timeout_s", 30.0))
