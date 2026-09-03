"""Safe low-dimensional intent commands and terrain-risk authority gating."""

from __future__ import annotations

from dataclasses import dataclass


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


@dataclass(frozen=True, slots=True)
class RootVelocityCommand:
    """Low-dimensional locomotion intent consumed by a learned executor."""

    vx_mps: float = 0.0
    vy_mps: float = 0.0
    yaw_rate_rps: float = 0.0

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

    def scaled(self, scale: float) -> "RootVelocityCommand":
        if not 0.0 <= scale <= 1.0:
            raise ValueError("command scale must be within [0, 1]")
        return RootVelocityCommand(
            self.vx_mps * scale,
            self.vy_mps * scale,
            self.yaw_rate_rps * scale,
        )

    @property
    def stopped(self) -> bool:
        return (
            abs(self.vx_mps) < 1e-9
            and abs(self.vy_mps) < 1e-9
            and abs(self.yaw_rate_rps) < 1e-9
        )


@dataclass(frozen=True, slots=True)
class RiskAssessment:
    """Direct-rollout risk estimate produced by a terrain safety critic."""

    probability_of_failure: float
    uncertainty: float = 0.0
    source: str = "risk_critic"

    def __post_init__(self) -> None:
        for name, value in (
            ("probability_of_failure", self.probability_of_failure),
            ("uncertainty", self.uncertainty),
        ):
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be within [0, 1]")

    @property
    def conservative_risk(self) -> float:
        return _clamp(self.probability_of_failure + self.uncertainty, 0.0, 1.0)


@dataclass(frozen=True, slots=True)
class RiskGatedCommand:
    requested: RootVelocityCommand
    issued: RootVelocityCommand
    risk: RiskAssessment
    authority: float
    emergency_stop: bool
    reason: str


@dataclass(frozen=True, slots=True)
class RiskGate:
    """Independent authority allocator in front of a locomotion policy."""

    slow_threshold: float = 0.25
    stop_threshold: float = 0.65
    max_linear_mps: float = 0.8
    max_yaw_rate_rps: float = 1.2

    def __post_init__(self) -> None:
        if not 0.0 <= self.slow_threshold < self.stop_threshold <= 1.0:
            raise ValueError("risk thresholds must satisfy 0 <= slow < stop <= 1")
        if self.max_linear_mps <= 0 or self.max_yaw_rate_rps <= 0:
            raise ValueError("velocity limits must be positive")

    def apply(
        self,
        command: RootVelocityCommand,
        assessment: RiskAssessment,
    ) -> RiskGatedCommand:
        bounded = command.bounded(
            max_linear_mps=self.max_linear_mps,
            max_yaw_rate_rps=self.max_yaw_rate_rps,
        )
        risk = assessment.conservative_risk
        if risk >= self.stop_threshold:
            return RiskGatedCommand(
                requested=command,
                issued=RootVelocityCommand(),
                risk=assessment,
                authority=0.0,
                emergency_stop=True,
                reason="terrain risk exceeded stop threshold",
            )
        if risk <= self.slow_threshold:
            authority = 1.0
            reason = "full executor authority"
        else:
            span = self.stop_threshold - self.slow_threshold
            authority = (self.stop_threshold - risk) / span
            reason = "executor authority reduced by terrain risk"
        return RiskGatedCommand(
            requested=command,
            issued=bounded.scaled(authority),
            risk=assessment,
            authority=authority,
            emergency_stop=False,
            reason=reason,
        )

