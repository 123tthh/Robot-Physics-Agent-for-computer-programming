"""Skill execution boundary with preconditions, resource locks, verification, and logs."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from contextlib import AsyncExitStack
from uuid import uuid4

from .domain import SkillRequest, SkillResult, SkillStatus, StateSnapshot
from .ledger import EventLedger
from .recovery import RecoveryManager
from .registry import SkillRegistry
from .verifier import RuleBasedVerifier


class ResourceLockManager:
    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def acquire(self, resources: tuple[str, ...]) -> AsyncExitStack:
        stack = AsyncExitStack()
        for resource in sorted(set(resources)):
            await stack.enter_async_context(self._locks[resource])
        return stack


class SkillRuntime:
    def __init__(
        self,
        *,
        registry: SkillRegistry,
        verifier: RuleBasedVerifier,
        ledger: EventLedger,
        recovery: RecoveryManager,
    ) -> None:
        self.registry = registry
        self.verifier = verifier
        self.ledger = ledger
        self.recovery = recovery
        self.resources = ResourceLockManager()

    async def execute(self, request: SkillRequest, state: StateSnapshot) -> SkillResult:
        call_id = uuid4().hex
        skill = self.registry.get(request.skill)
        contract = skill.contract

        self.ledger.append(
            task_id=request.task_id,
            call_id=call_id,
            kind="skill_requested",
            payload={
                "skill": contract.name,
                "version": contract.version,
                "arguments": dict(request.arguments),
                "required_postconditions": list(request.required_postconditions),
            },
        )

        missing_preconditions = tuple(sorted(state.missing(contract.preconditions)))
        if missing_preconditions:
            result = SkillResult(
                call_id=call_id,
                task_id=request.task_id,
                skill=request.skill,
                status=SkillStatus.REJECTED,
                missing=missing_preconditions,
                failure_code="PRECONDITION_FAILED",
                recommended_recovery=self.recovery.recommend("PRECONDITION_FAILED"),
                message="skill was not started because preconditions were not verified",
            )
            self._record_result(result)
            return result

        async with await self.resources.acquire(contract.resources):
            execution = await skill.execute(state, request.arguments)

        if execution.status is not SkillStatus.SUCCEEDED:
            result = SkillResult(
                call_id=call_id,
                task_id=request.task_id,
                skill=request.skill,
                status=execution.status,
                failure_code=execution.failure_code,
                recommended_recovery=self.recovery.recommend(execution.failure_code),
                evidence=execution.evidence,
                message=execution.message,
            )
            self._record_result(result)
            return result

        raw_claims = tuple(
            dict.fromkeys(
                execution.claimed_states
                + contract.expected_states
                + request.required_postconditions
            )
        )
        report = self.verifier.verify(raw_claims, state, execution.evidence)
        state.facts.update(report.verified)

        required = set(contract.expected_states) | set(request.required_postconditions)
        missing = tuple(sorted(required - set(report.verified)))
        status = SkillStatus.SUCCEEDED if not missing else SkillStatus.FAILED
        failure_code = None if not missing else "POSTCONDITION_FAILED"
        result = SkillResult(
            call_id=call_id,
            task_id=request.task_id,
            skill=request.skill,
            status=status,
            achieved=report.verified,
            missing=missing,
            failure_code=failure_code,
            recommended_recovery=self.recovery.recommend(failure_code),
            evidence=execution.evidence,
            message=execution.message,
        )
        self._record_result(result)
        return result

    def _record_result(self, result: SkillResult) -> None:
        self.ledger.append(
            task_id=result.task_id,
            call_id=result.call_id,
            kind="skill_result",
            payload=result.to_dict(),
        )
