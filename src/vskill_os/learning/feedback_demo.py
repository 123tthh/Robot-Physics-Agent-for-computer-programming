"""Deterministic CPU contract cases. These events are not physical evidence."""

from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path

from ..adapters.text_feedback import session_from_spec
from .contracts import KeyEventRecord, RecordStamp
from .environment import PolicyObservation
from .feedback import ReadoutFault
from .feedback_evaluation import evaluate_feedback
from .feedback_session import FeedbackSpec
from .text_task import DigitalInputPlan, EpisodeScope, FocusChange

CASES = ("verified_after_wait", "software_drop", "focus_lost", "missing_readout", "finish_before_feedback",
         "stale_readout", "timeout", "low_confidence", "false_positive_readout", "confirmed_retry")


def case_spec(name: str, base: FeedbackSpec | None = None) -> FeedbackSpec:
    spec = base or FeedbackSpec()
    if name in {"software_drop", "false_positive_readout", "confirmed_retry"}:
        spec = replace(spec, input_plan=DigitalInputPlan((0,)))
    if name == "focus_lost":
        spec = replace(spec, input_plan=DigitalInputPlan(focus_changes=(FocusChange(0., False),)))
    disturbances = {
        "missing_readout": ReadoutFault(1, missing=True),
        "stale_readout": ReadoutFault(1, stale_by_s=.3),
        "timeout": ReadoutFault(1, no_response=True),
        "low_confidence": ReadoutFault(1, confidence=.2),
        "false_positive_readout": ReadoutFault(1, text_override="a"),
    }
    return replace(spec, readout_faults=(disturbances[name],)) if name in disturbances else spec


def demo_event(scope: EpisodeScope, sequence: int, time_s: float, key: str = "a", kind: str = "keydown") -> KeyEventRecord:
    stamp = RecordStamp(f"{scope.episode_id}:contract-event:{sequence}", scope.env_id, scope.episode_id, sequence,
                        scope.clock_domain, time_s, time_s, "synthetic.contract")
    return KeyEventRecord(stamp, key, kind, "physical_threshold" if kind == "keydown" else "released", f"fixture:{sequence}")


def demo_observation(scope: EpisodeScope, time_s: float) -> PolicyObservation:
    return PolicyObservation(scope.env_id, scope.episode_id, time_s, "a", (), (), ())


def build_feedback_case(name: str):
    if name not in CASES:
        raise ValueError("unknown feedback contract case")
    spec = case_spec(name)
    scope = EpisodeScope("contract-env", name, "contract_seconds")
    session = session_from_spec(spec, scope=scope)
    session.observe(demo_observation(scope, 0.))
    session.request("intermediate")
    session.observe(demo_observation(scope, 0.))
    session.advance(.25, (demo_event(scope, 0, .2), demo_event(scope, 1, .25, kind="keyup")))
    session.observe(demo_observation(scope, .25))
    if name == "confirmed_retry":
        session.request("intermediate")
        session.advance(.4)
        visible = session.observe(demo_observation(scope, .4))
        # A post-action readout plus completed release permits this one bounded
        # retry. No access to the supervisor's text state is needed.
        if visible.feedback[-1].status != "readable" or visible.feedback[-1].text != "":
            raise RuntimeError("retry requires an explicit fresh empty readout")
        session.advance(.65, (demo_event(scope, 2, .6), demo_event(scope, 3, .65, kind="keyup")))
        session.observe(demo_observation(scope, .65))
    session.request("final")
    if name != "finish_before_feedback":
        session.advance(session.queue.time_s + (.35 if name == "timeout" else .15))
    session.observe(demo_observation(scope, session.queue.time_s))
    return spec, scope, session.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("artifacts/verification_feedback/vf01/feedback_contracts"))
    args = parser.parse_args(argv)
    results = []
    for name in CASES:
        spec, scope, episode = build_feedback_case(name)
        directory = args.output / name
        spec.write(directory / "feedback_spec.json")
        episode.write(directory / "feedback_episode.json")
        report = evaluate_feedback(episode, expected=spec, scope=scope)
        expected_pass = name in {"verified_after_wait", "confirmed_retry"}
        report.update(case=name, expected_text_task_passed=expected_pass,
                      acceptance_passed=report["isolation_passed"] and report["text_task_passed"] == expected_pass)
        (directory / "evaluation.json").write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")
        results.append(report)
    summary = dict(scope="contract_feedback", physical_success_claimed=False, camera_verified=False,
                   cases=results, passed=all(item["acceptance_passed"] for item in results))
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
