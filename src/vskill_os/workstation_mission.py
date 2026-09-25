"""Finite-horizon approach → pixel observation → bimanual edit → pixel check.

This reference runner uses an isolated desktop, never the user's OS keyboard.
The default walking and key execution adapters are explicitly synthetic. Real
joint control remains in adapters, and camera observations need a separate
calibrated observer before robot-vision acceptance can be claimed.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import time
from uuid import uuid4
from typing import Protocol

from .control.standing_locomotion import KinematicWalkingAdapter, SE2Goal, StandingNavigationSkill
from .simulation.keyboard_bridge import KeyEvent, KeyEventType
from .simulation.screen_feedback import (
    ScreenFeedbackVerifier, TesseractScreenObserver, assert_plan_current,
    plan_text_edit, publish_frame,
)
from .standing_control import DigitalTextBuffer, StandingTypingController
from .standing_typing import StandingTypingPlan, StandingTypingPlanner


@dataclass(frozen=True)
class TypingExecution:
    succeeded: bool
    failure_code: str | None
    evidence_mode: str
    physical_verified: bool
    event_count: int
    details: dict


class TypingExecutor(Protocol):
    evidence_mode: str
    def run(self, plan: StandingTypingPlan, editor) -> TypingExecution: ...


class SyntheticTypingExecutor:
    """Exercise both-hand event reservations; do not fabricate contact data."""
    evidence_mode = "synthetic_events"

    def run(self, plan: StandingTypingPlan, editor) -> TypingExecution:
        events = []
        execution_id = uuid4().hex
        for i, planned in enumerate(plan.expected_events):
            event = KeyEvent(KeyEventType(planned.event_type), planned.key,
                             time.monotonic(), self.evidence_mode,
                             event_id=f"{execution_id}:{plan.plan_id}:{i}")
            editor.handle_event(event)
            events.append({**asdict(planned), "received_event_id": event.event_id})
        return TypingExecution(not editor.keys_down, None if not editor.keys_down else "KEYS_HELD",
                               self.evidence_mode, False, len(events), {"events": events})


class ForwardingTranscriptSink:
    """Deliver measured key events and retain a fresh controller transcript.

The transcript verifies this finite skill's input sequence. Existing document
contents are only observed through pixels by the outer feedback verifier.
"""
    source = "physical_events_forwarded_to_isolated_desktop"

    def __init__(self, editor):
        self.editor = editor
        self.transcript = DigitalTextBuffer()

    def consume(self, event: KeyEvent) -> None:
        self.editor.handle_event(event)
        self.transcript.consume(event)

    def snapshot(self) -> str:
        return self.transcript.snapshot()


class PhysicalTypingExecutor:
    evidence_mode = "physical"

    def __init__(self, driver, *, maximum_total_steps: int = 30000):
        self.driver, self.maximum_total_steps = driver, maximum_total_steps

    def run(self, plan: StandingTypingPlan, editor) -> TypingExecution:
        result = StandingTypingController(self.driver, maximum_total_steps=self.maximum_total_steps,
                    maximum_phase_steps=1200,
                    tip_contact_offset_m=getattr(self.driver, 'tip_contact_offset_m', 0.013),
                    maximum_press_depth_m=getattr(self.driver, 'maximum_press_depth_m', 0.012),
                    text_sink=ForwardingTranscriptSink(editor)).run(plan)
        return TypingExecution(result.succeeded, result.failure_code, self.evidence_mode,
                               result.succeeded and result.verification.physical_acceptance,
                               len(result.events), result.to_dict())


class WorkstationMission:
    version = "1.0.0"
    preconditions = ("isolated_editor", "navigation_adapter_ready", "pixel_observer_ready")
    postconditions = ("arrival_and_stop_verified", "exact_text_from_fresh_pixels", "no_keys_held")
    resources = ("locomotion", "both_arms", "both_index_fingers", "keyboard", "desktop")
    failure_codes = ("NAVIGATION_REJECTED", "PHYSICAL_NAVIGATION_REQUIRED", "STALE_EDIT_PLAN",
                     "TYPING_REJECTED", "KEYS_HELD", "SCREEN_VERIFICATION_FAILED", "MISSION_EXCEPTION")

    def __init__(self, navigation: StandingNavigationSkill, desktop, observer,
                 executor: TypingExecutor, *, output: Path):
        self.navigation, self.desktop, self.observer = navigation, desktop, observer
        self.executor, self.output = executor, Path(output)
        self._used = False

    def run(self, goal: SE2Goal, target: str) -> dict:
        if self._used:
            raise ValueError("create a fresh mission for each attempt")
        self._used = True
        result = {"schema": "workstation_feedback_mission", "version": self.version,
                  "succeeded": False, "failure_code": None, "phases": [],
                  "target": target, "chair_present": False,
                  "robot_camera_verified": False, "physical_end_to_end_verified": False,
                  "visual_evidence_scope": "actual_isolated_desktop_pixels",
                  "typing_evidence_mode": self.executor.evidence_mode,
                  "skill_contract": {"preconditions": self.preconditions,
                      "postconditions": self.postconditions, "resources": self.resources,
                      "failure_codes": self.failure_codes}}
        self.output.mkdir(parents=True, exist_ok=True)
        editor = self.desktop.editor
        try:
            result["phases"].append("approach_workstation")
            navigation = self.navigation.run(goal)
            result["navigation"] = navigation.to_dict()
            if not all((navigation.succeeded, navigation.arrived, navigation.stabilized, navigation.stop_verified)):
                result["failure_code"] = "NAVIGATION_REJECTED"
                return result
            if self.executor.evidence_mode == "physical" and not navigation.physical_walking_verified:
                result["failure_code"] = "PHYSICAL_NAVIGATION_REQUIRED"
                return result
            result["phases"].append("observe_desktop")
            before = self.desktop.capture()
            publish_frame(before, self.output / "before")
            observation = self.observer.observe(before)
            edit = plan_text_edit(observation, target)
            result["edit"] = asdict(edit)
            current_frame = self.desktop.capture()
            current = self.observer.observe(current_frame)
            try:
                if (current.frame_id != current_frame.frame_id
                        or current.captured_at_s != current_frame.captured_at_s
                        or current.pixel_sha256 != current_frame.pixel_sha256
                        or current.consumed_event_count != current_frame.consumed_event_count):
                    raise ValueError("SCREEN_CHANGED_BEFORE_EDIT: observation is not bound to the latest captured frame")
                assert_plan_current(edit, current)
            except ValueError as exc:
                result["failure_code"] = "STALE_EDIT_PLAN"
                result["error"] = str(exc)
                return result
            result["phases"].append("bimanual_edit")
            if edit.typing_text:
                plan = StandingTypingPlanner().plan_text(edit.typing_text)
                result["typing_plan"] = plan.to_dict()
                result["typing_plan_id"] = plan.plan_id
                executed = self.executor.run(plan, editor)
                result["typing_execution"] = asdict(executed)
                if not executed.succeeded:
                    result["failure_code"] = "TYPING_REJECTED"
                    return result
                if editor.consumed_event_count - edit.consumed_event_count != executed.event_count:
                    raise ValueError("event delivery watermark differs from execution evidence")
            else:
                result["typing_execution"] = {"succeeded": True, "event_count": 0,
                                              "evidence_mode": "no_edit_required", "physical_verified": False}
            if editor.keys_down:
                result["failure_code"] = "KEYS_HELD"
                return result
            result["phases"].append("verify_new_pixels")
            after = self.desktop.capture()
            publish_frame(after, self.output / "after")
            observed_after = self.observer.observe(after)
            verified = ScreenFeedbackVerifier().verify(expected_text=target, before=observation,
                       after=observed_after, frame=after, last_event_at_s=editor.last_event_at_s)
            result["screen_verification"] = asdict(verified)
            result["succeeded"] = verified.passed
            result["failure_code"] = None if verified.passed else "SCREEN_VERIFICATION_FAILED"
            result["phases"].append("complete" if verified.passed else "verification_rejected")
        except Exception as exc:
            result["failure_code"] = "MISSION_EXCEPTION"
            result["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            # Held keys are reported, never artificially released to fake proof.
            result["keys_down_at_end"] = sorted(editor.keys_down)
            result["finished_at_monotonic_s"] = time.monotonic()
            (self.output / "report.json").write_text(json.dumps(result, indent=2, ensure_ascii=False)+"\n")
        return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("reports/workstation_mission"))
    parser.add_argument("--initial", default="helo world")
    parser.add_argument("--target", default="hello world")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--backend", choices=("pygame", "pillow", "auto"), default="pygame")
    parser.add_argument("--visible", action="store_true")
    args = parser.parse_args(argv)
    from .adapters.pygame_desktop import PixelDesktop, SafeTextEditor
    desktop = PixelDesktop(SafeTextEditor(args.initial), backend=args.backend, visible=args.visible)
    try:
        mission = WorkstationMission(StandingNavigationSkill(KinematicWalkingAdapter(seed=args.seed)),
                    desktop, TesseractScreenObserver(), SyntheticTypingExecutor(), output=args.output)
        report = mission.run(SE2Goal(0.4, 0.1, 0.2), args.target)
        print(json.dumps({"succeeded": report["succeeded"], "failure_code": report["failure_code"],
                          "report": str(args.output / "report.json"), "physical_end_to_end_verified": False}))
        return 0 if report["succeeded"] else 1
    finally:
        desktop.close()


if __name__ == "__main__":
    raise SystemExit(main())
