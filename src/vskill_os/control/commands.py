"""Bounded low-dimensional locomotion commands."""

from __future__ import annotations

from dataclasses import dataclass
import math


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


@dataclass(frozen=True, slots=True)
class RootVelocityCommand:
    """Low-dimensional locomotion intent consumed by a learned executor."""

    vx_mps: float = 0.0
    vy_mps: float = 0.0
    yaw_rate_rps: float = 0.0

    def __post_init__(self) -> None:
        if any(
            not math.isfinite(value)
            for value in (self.vx_mps, self.vy_mps, self.yaw_rate_rps)
        ):
            raise ValueError("root velocity command values must be finite")

    def bounded(
        self,
        *,
        max_linear_mps: float,
        max_yaw_rate_rps: float,
    ) -> "RootVelocityCommand":
        if max_linear_mps <= 0 or max_yaw_rate_rps <= 0:
            raise ValueError("velocity limits must be positive")
        return RootVelocityCommand(
            vx_mps=_clamp(self.vx_mps, -max_linear_mps, max_linear_mps),
            vy_mps=_clamp(self.vy_mps, -max_linear_mps, max_linear_mps),
            yaw_rate_rps=_clamp(
                self.yaw_rate_rps, -max_yaw_rate_rps, max_yaw_rate_rps
            ),
        )
