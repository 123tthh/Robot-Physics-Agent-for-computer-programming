"""Ports for hardware, perception, and digital-agent adapters."""

from __future__ import annotations

from typing import Any, Mapping, Protocol

from .control import RiskAssessment, RootVelocityCommand, SitObjective
from .domain import Evidence, StateSnapshot
from .typing import Keystroke, KeyboardCalibration


class RobotAdapter(Protocol):
    async def execute_action(self, name: str, arguments: Mapping[str, Any]) -> tuple[Evidence, ...]:
        """Execute a bounded robot action through ROS 2 or a vendor SDK."""

    async def snapshot(self) -> StateSnapshot:
        """Return the latest physical state estimate."""


class PerceptionAdapter(Protocol):
    async def observe(self, target: str) -> tuple[Evidence, ...]:
        """Observe keyboard, screen, chair, or another task-relevant target."""


class CodeAgentAdapter(Protocol):
    async def submit(self, prompt: str) -> tuple[Evidence, ...]:
        """Submit a task to an isolated digital code agent."""

    async def request_fix(self, failure_evidence: tuple[Evidence, ...]) -> tuple[Evidence, ...]:
        """Ask the code agent to repair an independently detected failure."""


class TestRunnerAdapter(Protocol):
    async def run(self, specification: Mapping[str, Any]) -> tuple[Evidence, ...]:
        """Run an isolated test specification and return observable evidence."""


class LocomotionPolicyAdapter(Protocol):
    async def infer(
        self,
        command: RootVelocityCommand,
        observation: Mapping[str, Any],
    ) -> Mapping[str, float]:
        """Map safe root intent to controller-internal action targets."""

    async def stop(self) -> None:
        """Issue a bounded zero/hold command through the controller backend."""


class TerrainRiskAdapter(Protocol):
    async def assess(
        self,
        command: RootVelocityCommand,
        observation: Mapping[str, Any],
    ) -> RiskAssessment:
        """Evaluate a candidate command before locomotion policy execution."""


class WholeBodyPolicyAdapter(Protocol):
    async def track(
        self,
        objective: SitObjective,
        observation: Mapping[str, Any],
    ) -> tuple[Evidence, ...]:
        """Execute one guarded sit/work-pose objective."""


class DexterousHandAdapter(Protocol):
    async def calibrate_keyboard(self) -> KeyboardCalibration:
        """Estimate the keyboard frame from close-range perception."""

    async def execute_keystroke(
        self,
        stroke: Keystroke,
        calibration: KeyboardCalibration,
    ) -> tuple[Evidence, ...]:
        """Execute one physical key objective and return contact evidence."""
