"""Finite-horizon L2 skill; low-level actuation stays behind adapters."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
import math
from typing import Any, Mapping, Protocol

from ..contracts import SkillContract
from ..control.commands import RootVelocityCommand
from ..domain import Evidence, EvidenceDomain, SkillExecution, SkillStatus, StateSnapshot
from ..verifier import RuleBasedVerifier
from .model import DT, ChairCommand, ChairSample, Trace
from .verification import ChairVerifier, safety_failure


class VelocityExecutor(Protocol):
    async def advance(self, command: RootVelocityCommand, dt: float) -> None:
        """Execute one policy tick; enforce a separate expiring command watchdog."""
        ...

    def stop(self) -> None:
        """Nonblocking, idempotent handoff to a safe seated hold controller.

        Must not zero joint torque or wait for a policy/network response.
        """
        ...


class ChairObserver(Protocol):
    source: str

    async def sample(self) -> ChairSample:
        """Read independent estimation/sensing, never controller claimed states."""
        ...


INVALIDATED = ("seated_reposition_verified", "work_pose_ready", "seated_stable",
               "screen_visible", "hands_reachable", "keyboard_calibrated",
               "target_keys_reachable", "terminal_ready")


class ChairMotionError(RuntimeError):
    pass


class ChairRepositionSkill:
    contract = SkillContract(
        name="reposition_while_seated", version="0.2.0",
        preconditions=("robot_safe", "seated_stable", "work_pose_ready"),
        local_postconditions=("seated_reposition_verified",),
        next_skill_readiness=("seated_stable", "screen_visible", "hands_reachable", "work_pose_ready"),
        resources=("whole_body", "locomotion", "head_camera", "upper_body", "left_arm", "right_arm",
                   "left_dex3", "right_dex3", "keyboard", "keyboard_camera"),
        failure_codes=("INVALID_ARGUMENT", "PRECONDITION_FAILED", "SEAT_CONTACT_LOST",
                       "SEATED_POSTURE_UNSTABLE", "EMERGENCY_STOP", "COLLISION",
                       "STALE_OBSERVATION", "UNTRUSTED_EVIDENCE", "ADAPTER_ERROR",
                       "SKILL_CANCELED", "STOP_FAILED", "POSTCONDITION_FAILED"),
        recoveries=("recover_seated_contact", "stabilize_work_pose"),
    )

    def __init__(self, executor: VelocityExecutor, observer: ChairObserver) -> None:
        self.executor, self.observer = executor, observer

    async def execute(self, state: StateSnapshot, arguments: Mapping[str, Any]) -> SkillExecution:
        if not state.has_all(self.contract.preconditions):
            return SkillExecution(SkillStatus.REJECTED, failure_code="PRECONDITION_FAILED")
        try:
            if set(arguments) - {"vx_mps", "vy_mps", "yaw_rate_rps", "duration_s"}:
                raise ValueError("only planar velocity and duration arguments are accepted")
            request = ChairCommand(RootVelocityCommand(
                arguments.get("vx_mps", 0.0), arguments.get("vy_mps", 0.0),
                arguments.get("yaw_rate_rps", 0.0)), arguments.get("duration_s", 2.0))
        except (ValueError, TypeError) as exc:
            return SkillExecution(SkillStatus.REJECTED, failure_code="INVALID_ARGUMENT", message=str(exc))
        samples: list[ChairSample] = []
        problem: str | None = None
        message = "motion trace collected; independent verification required"
        end = round(request.duration_s / DT)
        try:
            initial = await asyncio.wait_for(self.observer.sample(), 0.25)
            if initial.source != self.observer.source:
                raise ChairMotionError("UNTRUSTED_EVIDENCE")
            guard = safety_failure(initial)
            if guard or initial.seat_confidence < 0.9:
                raise ChairMotionError(guard or "SEAT_CONTACT_LOST")
            samples.append(initial)
            for tick in range(end + 25):
                command = request.velocity if tick < end else RootVelocityCommand()
                await asyncio.wait_for(self.executor.advance(command, DT), 0.25)
                sample = await asyncio.wait_for(self.observer.sample(), 0.25)
                previous = samples[-1]
                if sample.source != self.observer.source or sample.episode_id != initial.episode_id:
                    raise ChairMotionError("UNTRUSTED_EVIDENCE")
                if not math.isclose(sample.time_s - previous.time_s, DT, abs_tol=1e-6):
                    raise ChairMotionError("STALE_OBSERVATION")
                samples.append(sample)
                guard = safety_failure(sample)
                if guard:
                    raise ChairMotionError(guard)
        except ChairMotionError as exc:
            problem = str(exc)
        except asyncio.CancelledError:
            # Return through Runtime's normal invalidation path, including timeout cancellation.
            problem, message = "SKILL_CANCELED", "motion canceled; safe hold requested"
        except TimeoutError:
            problem, message = "STALE_OBSERVATION", "adapter tick or sensor timed out"
        except Exception as exc:
            problem, message = "ADAPTER_ERROR", f"{type(exc).__name__}: {exc}"
        finally:
            try:
                self.executor.stop()
            except Exception as exc:
                problem, message = "STOP_FAILED", f"safe hold failed: {exc}"
        trace = Trace(request, tuple(samples), end)
        evidence = (Evidence(EvidenceDomain.PHYSICAL, "chair_raw_trace", asdict(trace), self.observer.source),)
        return SkillExecution(
            SkillStatus.FAILED if problem else SkillStatus.SUCCEEDED,
            claimed_states=() if problem else self.contract.expected_states,
            invalidated_states=INVALIDATED + (("robot_safe",) if problem in {"STOP_FAILED", "COLLISION", "EMERGENCY_STOP"} else ()),
            evidence=evidence, failure_code=problem, message=message,
        )


def build_chair_verifier(observer_source: str, evidence_level: str) -> RuleBasedVerifier:
    """Explicit composition; proxy sources are never added to the default verifier."""
    verifier = RuleBasedVerifier()
    checker = ChairVerifier(observer_source=observer_source, evidence_level=evidence_level)

    def rule(state: StateSnapshot, evidence: tuple[Evidence, ...]) -> bool:
        if "robot_safe" not in state.facts:
            return False
        matching = [e for e in evidence if e.kind == "chair_raw_trace"
                    and e.source == observer_source and e.confidence >= 0.95]
        if len(matching) != 1:
            return False
        try:
            return checker.verify(Trace.from_dict(matching[0].value)).accepted
        except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
            return False

    for fact in ChairRepositionSkill.contract.expected_states:
        verifier.register(fact, rule)
    return verifier
