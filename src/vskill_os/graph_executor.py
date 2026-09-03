"""Checkpointed execution of a V-SkillGraph with evidence-grounded recovery."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .domain import SkillRequest, SkillResult, SkillStatus, StateSnapshot
from .graph import SkillGraph, SkillNode
from .ledger import EventLedger
from .runtime import SkillRuntime


@dataclass(frozen=True, slots=True)
class GraphRunResult:
    task_id: str
    graph: str
    status: SkillStatus
    completed_nodes: tuple[str, ...]
    skill_results: tuple[SkillResult, ...]
    final_state: StateSnapshot
    resumed: bool = False
    failure_node: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "graph": self.graph,
            "status": self.status.value,
            "completed_nodes": list(self.completed_nodes),
            "failure_node": self.failure_node,
            "resumed": self.resumed,
            "final_state": self.final_state.to_dict(),
            "skill_results": [result.to_dict() for result in self.skill_results],
        }


class GraphSupervisor:
    def __init__(
        self,
        *,
        runtime: SkillRuntime,
        ledger: EventLedger,
        max_recovery_attempts: int = 2,
        resume_hook: Callable[[StateSnapshot], None] | None = None,
    ) -> None:
        self.runtime = runtime
        self.ledger = ledger
        self.max_recovery_attempts = max_recovery_attempts
        self.resume_hook = resume_hook

    async def run(
        self,
        *,
        task_id: str,
        graph: SkillGraph,
        initial_state: StateSnapshot,
        resume: bool = False,
        stop_after_nodes: int | None = None,
    ) -> GraphRunResult:
        state = initial_state
        completed: set[str] = set()
        resumed = False
        if resume:
            checkpoint = self.ledger.load_latest_checkpoint(task_id)
            if checkpoint is not None:
                state = StateSnapshot.from_dict(checkpoint.state)
                completed = set(checkpoint.completed_nodes)
                if not completed.issubset(graph.node_ids):
                    raise ValueError("checkpoint contains nodes absent from the current graph")
                if self.resume_hook is not None:
                    self.resume_hook(state)
                resumed = True

        self.ledger.append(
            task_id=task_id,
            kind="graph_started",
            payload={
                "graph": graph.name,
                "resume_requested": resume,
                "resumed": resumed,
                "completed_nodes": sorted(completed),
            },
        )
        results: list[SkillResult] = []
        nodes_completed_this_run = 0

        while completed != graph.node_ids:
            ready = graph.ready_nodes(completed)
            if not ready:
                return self._finish(
                    task_id,
                    graph,
                    SkillStatus.FAILED,
                    completed,
                    results,
                    state,
                    resumed,
                    failure_node="GRAPH_DEADLOCK",
                )

            node = ready[0]
            result = await self._execute_node(task_id, node, state)
            results.append(result)

            if result.status is not SkillStatus.SUCCEEDED:
                result = await self._recover_and_retry(
                    task_id=task_id,
                    node=node,
                    state=state,
                    initial_result=result,
                    all_results=results,
                )

            if result.status is not SkillStatus.SUCCEEDED:
                self._checkpoint(task_id, state, completed)
                return self._finish(
                    task_id,
                    graph,
                    result.status,
                    completed,
                    results,
                    state,
                    resumed,
                    failure_node=node.node_id,
                )

            completed.add(node.node_id)
            nodes_completed_this_run += 1
            self.ledger.append(
                task_id=task_id,
                call_id=result.call_id,
                kind="graph_node_completed",
                payload={"node_id": node.node_id, "skill": node.skill},
            )
            self._checkpoint(task_id, state, completed)

            if stop_after_nodes is not None and nodes_completed_this_run >= stop_after_nodes:
                return self._finish(
                    task_id,
                    graph,
                    SkillStatus.CANCELED,
                    completed,
                    results,
                    state,
                    resumed,
                    failure_node="INTENTIONAL_CHECKPOINT_STOP",
                )

        return self._finish(
            task_id,
            graph,
            SkillStatus.SUCCEEDED,
            completed,
            results,
            state,
            resumed,
        )

    async def _execute_node(
        self,
        task_id: str,
        node: SkillNode,
        state: StateSnapshot,
    ) -> SkillResult:
        return await self.runtime.execute(
            SkillRequest(
                task_id=task_id,
                skill=node.skill,
                arguments=node.arguments,
                required_postconditions=node.required_postconditions,
                timeout_seconds=node.timeout_seconds,
                metadata={"graph_node": node.node_id},
            ),
            state,
        )

    async def _recover_and_retry(
        self,
        *,
        task_id: str,
        node: SkillNode,
        state: StateSnapshot,
        initial_result: SkillResult,
        all_results: list[SkillResult],
    ) -> SkillResult:
        result = initial_result
        for attempt in range(1, self.max_recovery_attempts + 1):
            recovery_skill = self._select_recovery(result)
            if recovery_skill is None or recovery_skill not in self.runtime.registry:
                return result

            self.ledger.append(
                task_id=task_id,
                call_id=result.call_id,
                kind="recovery_selected",
                payload={
                    "failed_skill": result.skill,
                    "failure_code": result.failure_code,
                    "recovery_skill": recovery_skill,
                    "attempt": attempt,
                },
            )
            recovery_result = await self.runtime.execute(
                SkillRequest(
                    task_id=task_id,
                    skill=recovery_skill,
                    metadata={"recovery_for": result.call_id, "graph_node": node.node_id},
                ),
                state,
            )
            all_results.append(recovery_result)
            self.ledger.remember_recovery(
                failed_skill=result.skill,
                failure_code=result.failure_code or "UNKNOWN",
                recovery_skill=recovery_skill,
                outcome=(
                    "success"
                    if recovery_result.status is SkillStatus.SUCCEEDED
                    else "failure"
                ),
                context={"graph_node": node.node_id, "state_revision": state.revision},
            )
            if recovery_result.status is not SkillStatus.SUCCEEDED:
                result = recovery_result
                continue

            result = await self._execute_node(task_id, node, state)
            all_results.append(result)
            if result.status is SkillStatus.SUCCEEDED:
                return result
        return result

    def _select_recovery(self, result: SkillResult) -> str | None:
        if result.failure_code:
            memories = self.ledger.find_successful_recoveries(
                failed_skill=result.skill,
                failure_code=result.failure_code,
                limit=1,
            )
            if memories:
                return memories[0].recovery_skill
        return result.recommended_recovery

    def _checkpoint(
        self,
        task_id: str,
        state: StateSnapshot,
        completed: set[str],
    ) -> None:
        self.ledger.save_checkpoint(
            task_id=task_id,
            state=state.to_dict(),
            completed_nodes=completed,
        )

    def _finish(
        self,
        task_id: str,
        graph: SkillGraph,
        status: SkillStatus,
        completed: set[str],
        results: list[SkillResult],
        state: StateSnapshot,
        resumed: bool,
        failure_node: str | None = None,
    ) -> GraphRunResult:
        self.ledger.append(
            task_id=task_id,
            kind="graph_finished",
            payload={
                "graph": graph.name,
                "status": status.value,
                "completed_nodes": sorted(completed),
                "failure_node": failure_node,
            },
        )
        return GraphRunResult(
            task_id=task_id,
            graph=graph.name,
            status=status,
            completed_nodes=tuple(sorted(completed)),
            skill_results=tuple(results),
            final_state=state.copy(),
            resumed=resumed,
            failure_node=failure_node,
        )
