"""Deterministic CONTRACT PROXY: command-driven kinematics, no contact physics.

Never use this backend to train a useful policy or to claim physical success.
Separate executor/observer objects illustrate the boundary, not sensor independence.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from uuid import uuid4

from ..control.commands import RootVelocityCommand
from .model import ChairSample


@dataclass(slots=True)
class ProxyWorld:
    fault: str = "none"
    time_s: float = 0.0
    x: float = 0.0
    y: float = 0.0
    yaw: float = 0.0
    stopped: bool = False
    episode_id: str = ""

    def __post_init__(self) -> None:
        if self.fault not in {"none", "stationary", "seat_loss", "wrong_way", "chair_stuck", "stale", "work_pose"}:
            raise ValueError("unknown proxy fault")
        self.episode_id = uuid4().hex


class ProxyExecutor:
    def __init__(self, world: ProxyWorld) -> None:
        self.world = world

    async def advance(self, command: RootVelocityCommand, dt: float) -> None:
        w = self.world
        if w.stopped:
            raise RuntimeError("stopped proxy requires a fresh episode")
        gain = 0 if w.fault == "stationary" else (-1 if w.fault == "wrong_way" else 1)
        c, s = math.cos(w.yaw), math.sin(w.yaw)
        w.x += gain * (c * command.vx_mps - s * command.vy_mps) * dt
        w.y += gain * (s * command.vx_mps + c * command.vy_mps) * dt
        w.yaw += gain * command.yaw_rate_rps * dt
        w.time_s += dt

    def stop(self) -> None:
        self.world.stopped = True


class ProxyObserver:
    source = "chair_contract_proxy"

    def __init__(self, world: ProxyWorld) -> None:
        self.world = world

    async def sample(self) -> ChairSample:
        w = self.world
        pose = (w.x, w.y, w.yaw)
        loss = w.fault == "seat_loss" and w.time_s >= 0.3
        return ChairSample(
            w.episode_id, self.source, 0.0 if w.fault == "stale" else w.time_s,
            pose, (0.0, 0.0, 0.0) if w.fault == "chair_stuck" else pose,
            0.0 if loss else 0.98, w.time_s - 0.3 if loss else 0.0,
            0.01, 0.02, 0.02, (True, True), True, w.fault != "work_pose",
        )
