"""Ports for hardware, perception, and digital-agent adapters."""

from __future__ import annotations

from typing import Any, Mapping, Protocol

from .domain import Evidence, StateSnapshot


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
