"""Recheck saved Isaac telemetry independently of the motion controller.

Run with PYTHONPATH=src. This audits recorded evidence, not sensor authenticity
or hardware validity. Full acceptance requires the default ten-finger plan and
the executed seating transition; diagnostic runs cannot meet that contract.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import json
from pathlib import Path

from vskill_os.simulation.dex5_typing import (
    DEFAULT_DEMO_TEXT, Dex5Contact, Dex5Observation, Dex5Phase,
    Dex5TypingPlanner, Dex5TypingVerifier,
)


def verify_recording(path: Path) -> dict:
    recorded = json.loads(path.read_text())
    raw_plan = recorded["plan"]
    plan = replace(Dex5TypingPlanner().plan(
        raw_plan["text"], initial_text=raw_plan["initial_text"],
        phase_timeout_s=raw_plan["stages"][0]["maximum_duration_s"],
    ), maximum_setup_duration_s=raw_plan["maximum_setup_duration_s"])
    if json.loads(json.dumps(plan.to_dict())) != raw_plan:
        raise ValueError("recorded plan differs from the current versioned contract")
    observations = []
    for raw in recorded["observations"]:
        item = dict(raw)
        item["phase"] = Dex5Phase(item["phase"])
        item["contacts"] = tuple(Dex5Contact(**contact) for contact in item["contacts"])
        for name in ("key_travel_m", "events", "fingertip_clearance_m"):
            item[name] = tuple(tuple(pair) for pair in item[name])
        item["keys_down"] = tuple(item["keys_down"])
        observations.append(Dex5Observation(**item))
    verdict = Dex5TypingVerifier().verify(plan, observations, capability="isaac_sim_5_1")
    gates = {
        "recorded_status_passed": recorded["status"] == "passed",
        "independent_telemetry_verification": verdict.succeeded,
        "full_default_ten_finger_plan": plan.text == DEFAULT_DEMO_TEXT and len(verdict.executors_observed) == 10,
        "seating_transition_executed": recorded.get("seating_transition_executed") is True,
        "source_preflight": recorded.get("asset_preflight", {}).get("deterministic_composition_verified") is True,
        "hard_mimic_preflight": recorded.get("mimic_preflight", {}).get("hard_coupling_verified") is True,
        "audited_distal_contacts": recorded.get("distal_collision_model") == "audited_single_sphere_per_distal_link",
        "no_keys_down_at_end": recorded.get("keys_down_at_end") == [],
    }
    # The source report stores every event frame. Cross-check its separate
    # event stream so a missing/extra event cannot disappear in serialization.
    sample_events = [(kind,key,o.time_s) for o in observations for kind,key in o.events]
    report_events = [(e["event_type"],e["key"],e["monotonic_time"]) for e in recorded["events"]]
    gates["event_stream_matches_observations"] = sample_events == report_events
    stroke_metrics = []
    for stroke in plan.strokes:
        samples = [o for o in observations if o.stroke_index == stroke.index]
        down = next((o for o in samples if ("keydown",stroke.key) in o.events), None)
        up = next((o for o in samples if ("keyup",stroke.key) in o.events), None)
        released = [o for o in samples if o.phase is Dex5Phase.RELEASE]
        stroke_metrics.append({
            "stroke":stroke.index, "key":stroke.key, "executor":stroke.executor,
            "keydown_time_s":down.time_s if down else None,
            "keyup_time_s":up.time_s if up else None,
            "keydown_travel_mm":1000*dict(down.key_travel_m)[stroke.key] if down else None,
            "keydown_contact_n":sum(c.force_n for c in down.contacts if c.key==stroke.key and c.executor==stroke.executor) if down else None,
            "final_minimum_fingertip_clearance_mm":1000*min(v for _,v in released[-1].fingertip_clearance_m) if released else None,
        })
    return {
        "passed": all(gates.values()), "gates": gates,
        "report_path": str(path), "report_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "runtime": recorded.get("runtime"), "seed": recorded["seed"],
        "physical_duration_s": recorded["physical_duration_s"],
        "max_keycap_force_n": recorded["max_keycap_force_n"],
        "verification": verdict.to_dict(), "assumptions": recorded["assumptions"],
        "implementation_sha256": recorded["implementation_sha256"],
        "stroke_metrics":stroke_metrics,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", type=Path, nargs="+")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--video", type=Path, help="optional associated video to fingerprint")
    args = parser.parse_args()
    runs = [verify_recording(path) for path in args.reports]
    result = {"schema_version": 1, "scope": "assisted_seating_and_ten_finger_typing",
              "passed": all(run["passed"] for run in runs), "runs": runs,
              "dynamic_sitting_verified": False, "camera_ocr_verified": False, "real_robot_verified": False}
    result["audit_tool_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    if args.video:
        result["video"] = {"path":str(args.video),"sha256":hashlib.sha256(args.video.read_bytes()).hexdigest()}
    text = json.dumps(result, indent=2, ensure_ascii=False)+"\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text)
    print(text, end="")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
