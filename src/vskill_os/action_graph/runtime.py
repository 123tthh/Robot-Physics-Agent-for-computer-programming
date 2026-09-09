"""Executor and independent verifier boundary for an ActionGraph."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import StrEnum
import time
from typing import Protocol

from ..domain import Evidence
from .model import ActionGraph, ActionKind, ActionNode, EvidenceRequirement


class ActionStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"


@dataclass(frozen=True, slots=True)
class ActionExecution:
    """Raw adapter output.  It is a claim until the verifier accepts it."""

    succeeded: bool
    evidence: tuple[Evidence, ...] = ()
    failure_code: str | None = None
    message: str = ""


@dataclass(frozen=True, slots=True)
class ActionVerification:
    accepted: bool
    failure_code: str | None = None
    message: str = ""


@dataclass(frozen=True, slots=True)
class ActionNodeResult:
    node_id: str
    kind: ActionKind
    status: ActionStatus
    evidence: tuple[Evidence, ...]
    started_at: float
    finished_at: float
    failure_code: str | None = None
    message: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "node_id": self.node_id,
            "kind": self.kind.value,
            "status": self.status.value,
            "failure_code": self.failure_code,
            "message": self.message,
            "duration_seconds": self.finished_at - self.started_at,
            "evidence": [item.to_dict() for item in self.evidence],
        }


@dataclass(frozen=True, slots=True)
class ActionGraphRunResult:
    graph_name: str
    graph_version: str
    status: ActionStatus
    completed_nodes: tuple[str, ...]
    node_results: tuple[ActionNodeResult, ...]
    cleanup_evidence: tuple[Evidence, ...] = ()
    failure_node: str | None = None
    failure_code: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "graph": self.graph_name,
            "version": self.graph_version,
            "status": self.status.value,
            "completed_nodes": list(self.completed_nodes),
            "failure_node": self.failure_node,
            "failure_code": self.failure_code,
            "node_results": [result.to_dict() for result in self.node_results],
            "cleanup_evidence": [item.to_dict() for item in self.cleanup_evidence],
        }


class ActionAdapter(Protocol):
    """Simulation and robot backends implement the same action contract."""

    async def execute(self, node: ActionNode) -> ActionExecution:
        """Attempt one finite-horizon action."""

    async def emergency_release(self, reason: str) -> tuple[Evidence, ...]:
        """Release every key and return independent cleanup observations."""


class ActionVerifier(Protocol):
    def verify(
        self, node: ActionNode, execution: ActionExecution
    ) -> ActionVerification:
        """Accept or reject raw adapter output using independent evidence."""


class IndependentActionVerifier:
    """Strict deterministic verifier for contact, event, and screen evidence."""

    def __init__(self, *, untrusted_sources: set[str] | None = None) -> None:
        self.untrusted_sources = set(
            untrusted_sources
            or {
                "controller",
                "action_controller",
                "cartesian_controller",
                "residual_policy",
            }
        )

    def verify(
        self, node: ActionNode, execution: ActionExecution
    ) -> ActionVerification:
        if not execution.succeeded:
            return ActionVerification(
                False,
                execution.failure_code or "ACTION_FAILED",
                execution.message or "adapter reported an action failure",
            )
        for requirement in node.evidence:
            matching = self._matching_evidence(requirement, execution.evidence)
            if len(matching) < requirement.minimum_count:
                return ActionVerification(
                    False,
                    "EVIDENCE_MISSING",
                    f"missing independent {requirement.domain.value}/{requirement.kind} evidence",
                )
        return self._verify_semantics(node, execution.evidence)

    def _matching_evidence(
        self,
        requirement: EvidenceRequirement,
        evidence: tuple[Evidence, ...],
    ) -> tuple[Evidence, ...]:
        return tuple(
            item
            for item in evidence
            if item.domain is requirement.domain
            and item.kind == requirement.kind
            and item.confidence >= requirement.minimum_confidence
            and item.source not in self.untrusted_sources
            and (
                not requirement.allowed_sources
                or item.source in requirement.allowed_sources
            )
        )

    def _verify_semantics(
        self, node: ActionNode, evidence: tuple[Evidence, ...]
    ) -> ActionVerification:
        target = str(node.parameters.get("target_key", ""))
        if node.kind is ActionKind.CARTESIAN_MOVE:
            values = _dict_evidence(evidence, "fingertip_pose")
            if not any(
                item.get("target_key") == target
                and item.get("phase") == node.parameters.get("phase")
                and item.get("within_tolerance") is True
                for item in values
            ):
                return ActionVerification(False, "POSE_NOT_REACHED", "pose oracle rejected target")
        elif node.kind is ActionKind.CONTACT_GATE:
            values = _dict_evidence(evidence, "fingertip_key_contact")
            valid = [item for item in values if item.get("target_key") == target]
            if not valid or not all(item.get("in_contact") is True for item in valid):
                return ActionVerification(False, "MISSED_CONTACT", "target contact was not observed")
            if any(item.get("adjacent_contacts") for item in valid):
                return ActionVerification(
                    False, "ADJACENT_KEY_CONTACT", "an adjacent key contact was observed"
                )
            force_limit = float(node.parameters.get("maximum_contact_force_n", float("inf")))
            if any(float(item.get("force_n", 0.0)) > force_limit for item in valid):
                return ActionVerification(False, "FORCE_LIMIT", "contact force exceeded limit")
        elif node.kind in {ActionKind.KEY_ACTUATE, ActionKind.KEY_RELEASE}:
            expected_type = str(node.parameters.get("event_type", ""))
            events = [
                item
                for item in _dict_evidence(evidence, "key_event")
                if item.get("event_type") == expected_type
            ]
            if len(events) != 1:
                code = (
                    "DUPLICATE_EVENT"
                    if len(events) > 1
                    else (
                        "MISSING_KEYUP"
                        if node.kind is ActionKind.KEY_RELEASE
                        else "MISSED_EVENT"
                    )
                )
                return ActionVerification(False, code, "expected exactly one keyboard event")
            if events[0].get("key") != target:
                return ActionVerification(False, "WRONG_KEY", "keyboard event key mismatched target")
            if node.kind is ActionKind.KEY_ACTUATE:
                travel_values = [
                    item
                    for item in _dict_evidence(evidence, "keycap_travel")
                    if item.get("target_key") == target
                ]
                required_travel = float(node.parameters.get("target_travel_m", 0.0))
                if not travel_values or max(
                    float(item.get("travel_m", 0.0)) for item in travel_values
                ) < required_travel:
                    return ActionVerification(
                        False,
                        "INSUFFICIENT_TRAVEL",
                        "keycap did not cross the required travel",
                    )
                force_limit = float(
                    node.parameters.get("maximum_contact_force_n", float("inf"))
                )
                if any(
                    float(item.get("force_n", float("inf"))) > force_limit
                    for item in travel_values
                ):
                    return ActionVerification(
                        False, "FORCE_LIMIT", "actuation force exceeded limit"
                    )
        elif node.kind is ActionKind.KEY_RELEASE_GROUP:
            expected_type = str(node.parameters.get("event_type", "keyup"))
            target_keys = tuple(str(key) for key in node.parameters.get("target_keys", ()))
            events = [
                item
                for item in _dict_evidence(evidence, "key_event")
                if item.get("event_type") == expected_type
            ]
            observed_keys = tuple(str(item.get("key", "")) for item in events)
            if len(events) < len(target_keys):
                return ActionVerification(False, "MISSING_KEYUP", "group release missed a key event")
            if len(events) > len(target_keys):
                return ActionVerification(False, "DUPLICATE_EVENT", "group release emitted extra events")
            if observed_keys != target_keys:
                return ActionVerification(False, "WRONG_KEY", "group release order mismatched")
            states = _dict_evidence(evidence, "key_release_state")
            cleared = {
                str(item.get("target_key"))
                for item in states
                if item.get("released") is True and item.get("clear") is True
            }
            if cleared != set(target_keys):
                return ActionVerification(
                    False, "STUCK_MODIFIER", "group release did not clear every modifier"
                )
        elif node.kind is ActionKind.KEY_HOLD:
            values = _dict_evidence(evidence, "key_hold_state")
            if not any(
                target in item.get("keys_down", ()) and item.get("stable") is True
                for item in values
            ):
                return ActionVerification(False, "HOLD_LOST", "key hold was not stable")
            force_limit = float(node.parameters.get("maximum_contact_force_n", float("inf")))
            if any(float(item.get("force_n", 0.0)) > force_limit for item in values):
                return ActionVerification(False, "FORCE_LIMIT", "hold force exceeded limit")
        elif node.kind is ActionKind.CLEARANCE_GATE:
            values = _dict_evidence(evidence, "key_release_state")
            if not any(
                item.get("target_key") == target
                and item.get("released") is True
                and item.get("clear") is True
                for item in values
            ):
                role = str(node.parameters.get("role", ""))
                code = "STUCK_MODIFIER" if role == "modifier" else "STUCK_KEY"
                return ActionVerification(False, code, "key or fingertip did not clear")
        elif node.kind is ActionKind.BARRIER:
            values = _dict_evidence(evidence, "barrier_state")
            if not any(item.get("ready") is True for item in values):
                return ActionVerification(False, "PREPOSE_TIMEOUT", "barrier was not satisfied")
        elif node.kind is ActionKind.SCREEN_GATE:
            values = _dict_evidence(evidence, "screen_state")
            if not values:
                return ActionVerification(False, "SCREEN_EVIDENCE_MISSING", "screen was not observed")
            observed = values[-1]
            if node.parameters.get("terminal_required") is True:
                if observed.get("visible") is not True:
                    return ActionVerification(False, "TERMINAL_NOT_VISIBLE", "terminal is not visible")
                if observed.get("focused") is not True:
                    return ActionVerification(False, "TERMINAL_NOT_FOCUSED", "terminal is not focused")
            if node.parameters.get("all_keys_released") is True and observed.get("keys_down"):
                return ActionVerification(False, "STUCK_MODIFIER", "a key remained held")
            expected = str(node.parameters.get("expected_text", ""))
            text = str(observed.get("text", ""))
            mode = str(node.parameters.get("match_mode", "contains"))
            if expected and not (
                (mode == "contains" and expected in text)
                or (mode == "suffix" and text.endswith(expected))
                or (mode == "exact" and text == expected)
            ):
                return ActionVerification(False, "SCREEN_TEXT_MISMATCH", "screen text mismatched")
        return ActionVerification(True)


class _ResourceLocks:
    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}

    async def run(self, node: ActionNode, operation) -> ActionExecution:
        locks = [self._locks.setdefault(name, asyncio.Lock()) for name in sorted(node.resources)]
        for lock in locks:
            await lock.acquire()
        try:
            return await operation()
        finally:
            for lock in reversed(locks):
                lock.release()


class ActionGraphRunner:
    """Execute independent DAG layers with resource locks and fail-safe release."""

    def __init__(
        self,
        *,
        adapter: ActionAdapter,
        verifier: ActionVerifier,
    ) -> None:
        self.adapter = adapter
        self.verifier = verifier
        self._resources = _ResourceLocks()

    async def run(self, graph: ActionGraph) -> ActionGraphRunResult:
        completed: set[str] = set()
        results: list[ActionNodeResult] = []
        for layer in graph.parallel_layers():
            layer_results = await asyncio.gather(
                *(self._run_node(graph.node(node_id)) for node_id in layer)
            )
            results.extend(layer_results)
            failed = next(
                (result for result in layer_results if result.status is not ActionStatus.SUCCEEDED),
                None,
            )
            for result in layer_results:
                if result.status is ActionStatus.SUCCEEDED:
                    completed.add(result.node_id)
            if failed is not None:
                cleanup = await self.adapter.emergency_release(
                    failed.failure_code or "ACTION_GRAPH_FAILURE"
                )
                return ActionGraphRunResult(
                    graph_name=graph.name,
                    graph_version=graph.version,
                    status=ActionStatus.FAILED,
                    completed_nodes=tuple(sorted(completed)),
                    node_results=tuple(results),
                    cleanup_evidence=cleanup,
                    failure_node=failed.node_id,
                    failure_code=failed.failure_code,
                )
        return ActionGraphRunResult(
            graph_name=graph.name,
            graph_version=graph.version,
            status=ActionStatus.SUCCEEDED,
            completed_nodes=tuple(sorted(completed)),
            node_results=tuple(results),
        )

    async def _run_node(self, node: ActionNode) -> ActionNodeResult:
        started = time.monotonic()

        async def operation() -> ActionExecution:
            try:
                return await asyncio.wait_for(
                    self.adapter.execute(node), timeout=node.timeout_seconds
                )
            except TimeoutError:
                return ActionExecution(False, failure_code="ACTION_TIMEOUT")
            except Exception as exc:  # adapter boundary must not escape the runner
                return ActionExecution(
                    False,
                    failure_code="ADAPTER_EXCEPTION",
                    message=f"{type(exc).__name__}: {exc}",
                )

        execution = await self._resources.run(node, operation)
        verification = self.verifier.verify(node, execution)
        finished = time.monotonic()
        if verification.accepted:
            return ActionNodeResult(
                node_id=node.node_id,
                kind=node.kind,
                status=ActionStatus.SUCCEEDED,
                evidence=execution.evidence,
                started_at=started,
                finished_at=finished,
            )
        code = verification.failure_code or execution.failure_code or "ACTION_REJECTED"
        if execution.failure_code and execution.failure_code not in node.failure_codes:
            # Adapter-specific failures still remain visible, but the graph
            # records that its contract did not declare the outcome.
            code = f"UNDECLARED_FAILURE_CODE:{execution.failure_code}"
        return ActionNodeResult(
            node_id=node.node_id,
            kind=node.kind,
            status=ActionStatus.FAILED,
            evidence=execution.evidence,
            started_at=started,
            finished_at=finished,
            failure_code=code,
            message=verification.message or execution.message,
        )


def _dict_evidence(evidence: tuple[Evidence, ...], kind: str) -> list[dict[str, object]]:
    return [
        dict(item.value)
        for item in evidence
        if item.kind == kind and isinstance(item.value, dict)
    ]
