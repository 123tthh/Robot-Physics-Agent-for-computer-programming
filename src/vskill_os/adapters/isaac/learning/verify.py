"""CPU-only verification of a captured Isaac single-hand evidence bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from ....learning.contracts import EpisodeRecord, KeyBinding, TaskSpec
from ....learning.physical import CoordinateLimit, PhysicsSample, PhysicsTrace
from ....learning.physical_evaluation import check_physical_episode
from ....learning.environment import ResetSpec
from ....learning.serialization import _unique_object
from ....simulation.dex5_assets import EXPECTED_BODY_JOINTS, EXPECTED_DEX5_JOINTS, audit_pinned_assets, validate_g1_dex5_urdf
from .config import SingleHandConfig, control_spec, direct_joint_initialization
from .single_hand_env import DEFAULT_URDF, ROOT, file_sha256, write_json
from .provenance import COLLECTOR_FILES, RECORDING_FILES
from ..p1_scene import keyboard_key_center, standard_keyboard_layout

def verify_bundle(directory: Path, *, expected_config: SingleHandConfig | None = None) -> dict:
    """Hashes bind raw files and the local collector; they do not authenticate sensors.

    Reproduce with the matching code/asset revision. There is intentionally no
    option to trust a controller's success flag or relabel synthetic records.
    """
    config = expected_config or SingleHandConfig()
    directory = directory.resolve()
    report = {"schema_version": "vf-physical-evaluation/1", "capability": "isaac_physx_single_hand_v1",
              "physical_primitive_passed": False, "camera_verified": False, "desktop_verified": False,
              "learned_policy": False, "recording": str(directory), "findings": []}
    try:
        manifest = json.loads((directory / "manifest.json").read_text(), object_pairs_hook=_unique_object)
        if manifest["schema_version"] != "vf-physics-bundle/1" or set(manifest["files"]) != set(RECORDING_FILES):
            raise ValueError("invalid bundle schema or file list")
        for name in RECORDING_FILES:
            if file_sha256(directory / name) != manifest["files"][name]:
                raise ValueError(f"ARTIFACT_HASH: {name}")
        sources = {name: file_sha256(ROOT / name) for name in COLLECTOR_FILES}
        if sources != manifest["sources"]:
            raise ValueError("SOURCE_HASH: verify with the exact collector source revision")
        if (manifest["urdf"] != {"path": str(DEFAULT_URDF.relative_to(ROOT)), "sha256": file_sha256(DEFAULT_URDF)}
                or file_sha256(directory.parent / "scene.usda") != manifest["scene_sha256"]):
            raise ValueError("ASSET_HASH: source URDF or scene snapshot differs")
        usd_dir = ROOT / "artifacts/verification_feedback/assets/usd"
        usd_files = {str(path.relative_to(ROOT)): file_sha256(path) for path in sorted(usd_dir.rglob("*.usd"))}
        if not usd_files or usd_files != manifest["usd_bundle"]:
            raise ValueError("ASSET_HASH: converted USD bundle differs")
        if not manifest["runtime"]["isaac_sim"].startswith("5.1.0") or manifest["runtime"]["num_envs"] != 1:
            raise ValueError("UNSUPPORTED_SOURCE: requires single-environment Isaac 5.1 capture")
        if SingleHandConfig.read(directory / "config.json") != config:
            raise ValueError("FROZEN_CONFIG_MISMATCH: captured configuration differs from expected configuration")
        # Geometry uses a regular dataclass, independent of source labels.
        from dataclasses import asdict
        if manifest["geometry"] != asdict(config.geometry()):
            raise ValueError("FROZEN_CONFIG_MISMATCH: workspace geometry changed")
        reset = ResetSpec.read(directory / "reset.json")
        task = TaskSpec.read(directory / "task.json")
        episode = EpisodeRecord.read(directory / "episode.json")
        trace = PhysicsTrace.read(directory / "trace.json")
        collector_hash = hashlib.sha256(json.dumps(sources, sort_keys=True).encode()).hexdigest()
        if episode.manifest.generator_sha256 != collector_hash or episode.manifest.seed != reset.seed:
            raise ValueError("SOURCE_HASH: run provenance differs from the captured source/seed")
        if (reset.episode_id != episode.episode_id or reset.target_key != "a" or task.target_text != "a"
                or task.initial_text != "" or task.time_budget_s != reset.maximum_time_s
                or task.character_keys != (KeyBinding("a", "a"),) or task.backspace_key != "backspace"
                or task.sensor_manifest != ("joint_state", "key_travel", "key_contact")
                or task.hand != "left" or task.support_mode != "fixed_pelvis_seated_pd"
                or task.monitored_keys != tuple(key.name for key in standard_keyboard_layout())):
            raise ValueError("TASK_MISMATCH: expected fixed-pelvis single left-hand key 'a'")
        report["pinned_assets"] = audit_pinned_assets()
        artifact = validate_g1_dex5_urdf(DEFAULT_URDF)
        if artifact.fixture_joint_count != 0 or len(trace.coordinate_limits) != 69:
            raise ValueError("JOINT_MAPPING: requires the 69-coordinate fixed-pelvis asset")
        names = tuple(limit.name for limit in trace.coordinate_limits)
        if set(names) != set(EXPECTED_BODY_JOINTS) | set(EXPECTED_DEX5_JOINTS) or len(set(names)) != len(names):
            raise ValueError("JOINT_MAPPING: raw tensor coordinate names differ from the pinned asset")
        joints = {joint.get("name"): joint for joint in ET.parse(DEFAULT_URDF).getroot().findall("joint")}
        limits = tuple(CoordinateLimit(
            name, "rad", float(joints[name].find("limit").get("lower")), float(joints[name].find("limit").get("upper")),
            min(float(joints[name].find("limit").get("velocity")), config.maximum_joint_speed_rad_s),
        ) for name in names)
        initial = direct_joint_initialization(DEFAULT_URDF)
        if (trace.initial_joint_positions != tuple(initial[name] for name in names)
                or manifest["initialization"] != {"source": "hand_authored_direct_joint_v1", "positions": initial,
                                                   "settling_steps": config.settling_steps}
                or manifest["fixed_pelvis_xyz_m"] != [0.0, 0.0, 0.625]):
            raise ValueError("INITIALIZATION_SOURCE: initialization was modified or came from an unsupported source")
        raw_lines = tuple(PhysicsSample.from_json(line) for line in (directory / "physics.jsonl").read_text().splitlines())
        if raw_lines != trace.samples:
            raise ValueError("RAW_DERIVATION_MISMATCH: append-only raw checkpoint differs from normalized trace")
        result = check_physical_episode(task, episode, trace, control=control_spec(DEFAULT_URDF, config), limits=limits,
                                        key_body_centers_xyz_m=tuple(v for key in standard_keyboard_layout() for v in keyboard_key_center(
                                            key, operator_row_x=config.keyboard_operator_row_x_m,
                                            left_origin_y=config.keyboard_left_origin_y_m, top_z=config.key_center_z_m)),
                                        maximum_force_n=config.maximum_contact_force_n,
                                        support_tolerance_m=config.support_tolerance_m, physics_dt_s=config.physics_dt_s)
        report.update(result.to_dict())
        report["physical_primitive_passed"] = result.consistent
        report["episode_sha256"] = episode.sha256()
        report["trace_sha256"] = trace.sha256()
        report["source_sha256"] = collector_hash
        report["config_sha256"] = config.sha256()
        report["evaluator_sha256"] = hashlib.sha256(b"".join(path.read_bytes() for path in (
            Path(__file__), ROOT / "src/vskill_os/learning/physical_evaluation.py",
            ROOT / "src/vskill_os/learning/evaluation.py", ROOT / "src/vskill_os/learning/physical.py",
        ))).hexdigest()
    except (OSError, ValueError, KeyError, TypeError, IndexError, StopIteration) as exc:
        report["findings"].append({"code": "INVALID_BUNDLE", "record_id": "bundle", "detail": str(exc)})
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recording", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    config = SingleHandConfig.read(args.config) if args.config else SingleHandConfig()
    result = verify_bundle(args.recording, expected_config=config)
    if args.output:
        write_json(args.output, result)
    print(json.dumps(result, indent=2))
    return 0 if result["physical_primitive_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
