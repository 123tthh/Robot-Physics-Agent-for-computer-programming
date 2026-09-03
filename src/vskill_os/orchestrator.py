"""Minimal deterministic supervisor baseline for Agent-driven workflows."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from .domain import SkillRequest, SkillResult, SkillStatus, StateSnapshot
from .runtime import SkillRuntime


@dataclass(frozen=True, slots=True)
class WorkflowStep:
    skill: str
    arguments: Mapping[str, Any] = field(default_factory=dict)
    required_postconditions: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class WorkflowResult:
    task_id: str
    status: SkillStatus
    steps: tuple[SkillResult, ...]
    final_facts: tuple[str, ...]


class SupervisorAgent:
    """Runs a plan and applies one registered recovery before retrying a failed step.

    This is intentionally deterministic. A Foundation Model planner can later emit
    WorkflowStep objects while keeping the runtime and safety boundary unchanged.
    """

    def __init__(self, runtime: SkillRuntime, *, max_recovery_attempts: int = 1) -> None:
        self.runtime = runtime
        self.max_recovery_attempts = max_recovery_attempts

    async def run(
        self,
        *,
        task_id: str,
        state: StateSnapshot,
        plan: tuple[WorkflowStep, ...],
    ) -> WorkflowResult:
        results: list[SkillResult] = []

        for step in plan:
            result = await self._execute_step(task_id, state, step)
            results.append(result)
            attempts = 0

            while result.status is not SkillStatus.SUCCEEDED:
                recovery_skill = result.recommended_recovery
                if (
                    recovery_skill is None
                    or recovery_skill not in self.runtime.registry
                    or attempts >= self.max_recovery_attempts
                ):
                    return WorkflowResult(
                        task_id=task_id,
                        status=result.status,
                        steps=tuple(results),
                        final_facts=tuple(sorted(state.facts)),
                    )

                recovery_result = await self.runtime.execute(
                    SkillRequest(task_id=task_id, skill=recovery_skill),
                    state,
                )
                results.append(recovery_result)
                attempts += 1
                if recovery_result.status is not SkillStatus.SUCCEEDED:
                    return WorkflowResult(
                        task_id=task_id,
                        status=recovery_result.status,
                        steps=tuple(results),
                        final_facts=tuple(sorted(state.facts)),
                    )

                result = await self._execute_step(task_id, state, step)
                results.append(result)

        return WorkflowResult(
            task_id=task_id,
            status=SkillStatus.SUCCEEDED,
            steps=tuple(results),
            final_facts=tuple(sorted(state.facts)),
        )

    async def _execute_step(
        self,
        task_id: str,
        state: StateSnapshot,
        step: WorkflowStep,
    ) -> SkillResult:
        return await self.runtime.execute(
            SkillRequest(
                task_id=task_id,
                skill=step.skill,
                arguments=step.arguments,
                required_postconditions=step.required_postconditions,
            ),
            state,
        )
