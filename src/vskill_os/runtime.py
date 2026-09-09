"""Skill execution boundary with preconditions, resource locks, verification, and logs."""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from contextlib import AsyncExitStack
from dataclasses import dataclass
import math
from typing import Callable, Mapping
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


@dataclass(frozen=True, slots=True)
class StateValidityPolicy:
    """Runtime-owned lifetime policy for a verified task fact."""

    ttl_seconds: float | None = None
    dependencies: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.ttl_seconds is not None and (
            not math.isfinite(self.ttl_seconds) or self.ttl_seconds <= 0.0
        ):
            raise ValueError("state validity TTL must be finite and positive")
        if len(self.dependencies) != len(set(self.dependencies)):
            raise ValueError("state validity dependencies contain duplicate entries")


class SkillRuntime:
    def __init__(
        self,
        *,
        registry: SkillRegistry,
        verifier: RuleBasedVerifier,
        ledger: EventLedger,
        recovery: RecoveryManager,
        clock: Callable[[], float] = time.time,
        state_validity: Mapping[str, StateValidityPolicy] | None = None,
    ) -> None:
        self.registry = registry
        self.verifier = verifier
        self.ledger = ledger
        self.recovery = recovery
        self.resources = ResourceLockManager()
        self.clock = clock
        self.state_validity = dict(state_validity or {})

    async def execute(self, request: SkillRequest, state: StateSnapshot) -> SkillResult:
        started_at = time.perf_counter()
        state_now = self.clock()
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
                "timeout_seconds": request.timeout_seconds,
                "metadata": dict(request.metadata),
            },
        )

        expired = state.expire(state_now)
        if expired:
            state.revision += 1
            self.ledger.append(
                task_id=request.task_id,
                call_id=call_id,
                kind="state_invalidated",
                payload={"reason": "expired_or_dependency_missing", "states": list(expired)},
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
                duration_seconds=time.perf_counter() - started_at,
            )
            self._record_result(result)
            return result

        execution_state = state.copy()
        try:
            async with await self.resources.acquire(contract.resources):
                execution = await asyncio.wait_for(
                    skill.execute(execution_state, request.arguments),
                    timeout=request.timeout_seconds,
                )
        except TimeoutError:
            result = SkillResult(
                call_id=call_id,
                task_id=request.task_id,
                skill=request.skill,
                status=SkillStatus.FAILED,
                failure_code="SKILL_TIMEOUT",
                recommended_recovery=self.recovery.recommend("SKILL_TIMEOUT"),
                message=f"skill exceeded {request.timeout_seconds:.3f} seconds",
                duration_seconds=time.perf_counter() - started_at,
            )
            self._record_result(result)
            return result
        except asyncio.CancelledError:
            result = SkillResult(
                call_id=call_id,
                task_id=request.task_id,
                skill=request.skill,
                status=SkillStatus.CANCELED,
                failure_code="SKILL_CANCELED",
                message="skill execution was canceled",
                duration_seconds=time.perf_counter() - started_at,
            )
            self._record_result(result)
            return result
        except Exception as exc:  # controller exceptions must not cross the safety boundary
            result = SkillResult(
                call_id=call_id,
                task_id=request.task_id,
                skill=request.skill,
                status=SkillStatus.FAILED,
                failure_code="SKILL_EXCEPTION",
                recommended_recovery=self.recovery.recommend("SKILL_EXCEPTION"),
                message=f"{type(exc).__name__}: {exc}",
                duration_seconds=time.perf_counter() - started_at,
            )
            self._record_result(result)
            return result

        if execution.invalidated_states:
            invalidated = state.invalidate(execution.invalidated_states)
            if invalidated:
                state.revision += 1
                self.ledger.append(
                    task_id=request.task_id,
                    call_id=call_id,
                    kind="state_invalidated",
                    payload={"reason": "skill_execution", "states": list(invalidated)},
                )

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
                duration_seconds=time.perf_counter() - started_at,
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
        report = self.verifier.verify(
            raw_claims,
            state,
            execution.evidence,
            known_states=set(state.facts),
        )
        if report.verified:
            verified_at = self.clock()
            for fact in report.verified:
                policy = self.state_validity.get(fact, StateValidityPolicy())
                state.mark_verified(
                    fact,
                    verified_at=verified_at,
                    ttl_seconds=policy.ttl_seconds,
                    dependencies=policy.dependencies,
                )
            state.revision += 1

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
            duration_seconds=time.perf_counter() - started_at,
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
