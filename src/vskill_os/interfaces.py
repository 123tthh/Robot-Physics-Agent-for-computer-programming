"""Ports for hardware, perception, and digital-agent adapters."""

from __future__ import annotations

from typing import Any, Mapping, Protocol

from .control import RootVelocityCommand, SeatedLocomotionObservation, SitObjective
from .domain import Evidence, StateSnapshot
from .ipc import WorkerMessage
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


class WorkerAdapter(Protocol):
    async def invoke(
        self,
        request: WorkerMessage,
        *,
        timeout_seconds: float,
    ) -> tuple[WorkerMessage, ...]:
        """Invoke an Isaac, desktop, or model worker through the P0 protocol."""

    async def close(self) -> None:
        """Cancel outstanding work and release the worker boundary."""


class LocomotionPolicyAdapter(Protocol):
    async def infer(
        self,
        command: RootVelocityCommand,
        observation: Mapping[str, Any],
    ) -> Mapping[str, float]:
        """Map safe root intent to controller-internal action targets."""

    async def stop(self) -> None:
        """Issue a bounded zero/hold command through the controller backend."""


class SeatedLocomotionPolicyAdapter(Protocol):
    async def track(
        self,
        command: RootVelocityCommand,
        observation: SeatedLocomotionObservation,
    ) -> tuple[Evidence, ...]:
        """Track safe planar intent while retaining unfixed pelvis-seat contact."""

    async def stop_and_stabilize(self) -> tuple[Evidence, ...]:
        """Stop chair motion and return evidence for the stable work-pose guard."""


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
