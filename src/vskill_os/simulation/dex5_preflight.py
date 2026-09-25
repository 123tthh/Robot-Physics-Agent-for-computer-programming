"""Preflight checks for the exact Dex5 assisted-typing experiment.

All source and joint-name checks run without Isaac, PyTorch, OpenUSD, or a GPU.
The optional stage inspection accepts an already-open USD stage and checks its
authored mimic properties without importing simulation modules into the core.
"""

from __future__ import annotations

from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Any, Iterable, Mapping
import xml.etree.ElementTree as ET

from .dex5_assets import (
    ACTIVE_DEX5_JOINTS,
    BODY_SOURCE,
    COUPLED_DEX5_JOINTS,
    DEFAULT_ASSET_ROOT,
    DEFAULT_MANIFEST,
    EXPECTED_BODY_JOINTS,
    EXPECTED_DEX5_JOINTS,
    HAND_SOURCES,
    SEATING_FIXTURE_JOINTS,
    Dex5AssetError,
    audit_pinned_assets,
    compose_mode15_g1_with_dex5,
    validate_g1_dex5_urdf,
)


EXPECTED_DEX5_FIXTURE_JOINTS = (*EXPECTED_BODY_JOINTS, *EXPECTED_DEX5_JOINTS, *SEATING_FIXTURE_JOINTS)


def validate_dex5_runtime_joint_names(names: Iterable[str]) -> dict[str, Any]:
    """Require the exact 71 generalized coordinates, including 8 mimics."""
    names = tuple(names)
    duplicates = sorted(name for name, count in Counter(names).items() if count != 1)
    expected = set(EXPECTED_DEX5_FIXTURE_JOINTS)
    missing, extra = sorted(expected - set(names)), sorted(set(names) - expected)
    if duplicates or missing or extra:
        raise Dex5AssetError(f"runtime joint set mismatch: missing={missing}, extra={extra}, duplicates={duplicates}")
    return {"generalized_joint_count": len(names), "body_joint_count": 29,
            "dex5_coordinate_count": 40, "dex5_active_joint_count": 32,
            "dex5_coupled_joint_count": 8, "fixture_joint_count": 2}


def validate_dex5_source_preflight(
    *, urdf: str | Path, postures: str | Path,
    manifest: str | Path = DEFAULT_MANIFEST, asset_root: str | Path = DEFAULT_ASSET_ROOT,
    tip_offsets: Mapping[str, Iterable[float]] | None = None,
) -> dict[str, Any]:
    """Check OEM hashes, exact regenerated composite, and calibrated posture inputs."""
    urdf, postures, asset_root = Path(urdf).resolve(), Path(postures).resolve(), Path(asset_root).resolve()
    sources = audit_pinned_assets(manifest=manifest, asset_root=asset_root)
    composite = validate_g1_dex5_urdf(urdf)
    if composite.fixture_joint_count != 2:
        raise Dex5AssetError("the assisted-typing demo requires both slide and lift fixture coordinates")
    # Same output directory guarantees identical portable mesh rebasing. The
    # output filename itself is not serialized into the deterministic URDF.
    handle, temporary_name = tempfile.mkstemp(prefix=".dex5-preflight-", suffix=".urdf", dir=urdf.parent)
    os.close(handle)
    temporary = Path(temporary_name)
    try:
        expected = compose_mode15_g1_with_dex5(
            g1_urdf=asset_root / BODY_SOURCE,
            left_hand_urdf=asset_root / HAND_SOURCES["left"],
            right_hand_urdf=asset_root / HAND_SOURCES["right"],
            output=temporary, seating_fixture=True,
        )
        if expected.sha256 != composite.sha256:
            raise Dex5AssetError("composite URDF differs from deterministic composition of pinned OEM sources")
    finally:
        temporary.unlink(missing_ok=True)
    data = json.loads(postures.read_text(encoding="utf-8"))
    if data.get("urdf_sha256") != composite.sha256:
        raise Dex5AssetError("posture calibration URDF hash does not match the checked composite")
    if tip_offsets is not None:
        calibrated = {key: tuple(value) for key, value in data.get("tip_local_offsets_m", {}).items()}
        if calibrated != {key: tuple(value) for key, value in tip_offsets.items()}:
            raise Dex5AssetError("runtime fingertip offsets do not match posture calibration")
    joints = {joint.attrib["name"]: joint for joint in ET.parse(urdf).getroot().findall("joint")}
    counts = {}
    controlled = set(EXPECTED_BODY_JOINTS) | set(ACTIVE_DEX5_JOINTS)
    for stage in ("raised", "home"):
        stage_data = data.get(stage, {}).get("hands", {})
        if set(stage_data) != {"left", "right"}:
            raise Dex5AssetError(f"posture {stage} must contain both hands")
        for side, hand in stage_data.items():
            positions = hand.get("joint_positions", {})
            if not positions:
                raise Dex5AssetError(f"posture {stage}/{side} has no joint positions")
            for name, value in positions.items():
                if name not in controlled or not name.startswith(side + "_"):
                    raise Dex5AssetError(f"posture directly controls a coupled, fixture, or unknown joint: {name}")
                limit = joints[name].find("limit")
                assert limit is not None
                if not isinstance(value, (float, int)) or not math.isfinite(value) or not float(limit.attrib["lower"]) <= value <= float(limit.attrib["upper"]):
                    raise Dex5AssetError(f"posture target outside source joint limits: {name}")
            counts[f"{stage}/{side}"] = len(positions)
    return {"pinned_sources": sources, "composite": composite.to_dict(),
            "postures_sha256": hashlib.sha256(postures.read_bytes()).hexdigest(),
            "posture_joint_counts": counts, "deterministic_composition_verified": True}


def validate_dex5_mimic_stage(
    stage: Any, *, urdf: str | Path, robot_path: str = "/World/G1",
    require_hard_coupling: bool = False,
) -> dict[str, Any]:
    """Check each authored PhysX coupling, not just a stage-global API count.

    PhysX encodes q_child + gearing*q_reference + offset = 0, hence its
    gearing must be the negative of the URDF multiplier. When hard coupling
    is required, every distal joint must additionally resolve both its mimic
    natural frequency and damping ratio to exactly zero. Missing properties
    do not constitute evidence of a hard constraint.
    """
    root = ET.parse(urdf).getroot()
    expected = {
        name: root.find(f"joint[@name='{name}']/mimic")
        for name in COUPLED_DEX5_JOINTS
    }
    found: dict[str, dict[str, Any]] = {}
    prefix = robot_path.rstrip("/") + "/"
    for prim in stage.Traverse():
        if not str(prim.GetPath()).startswith(prefix):
            continue
        gearing = prim.GetAttribute("physxMimicJoint:rotZ:gearing")
        if not gearing or gearing.Get() is None:
            continue
        name = prim.GetName()
        if name not in expected or name in found:
            raise Dex5AssetError(f"unexpected or duplicate PhysX mimic joint {name}")
        mimic = expected[name]
        if mimic is None:
            raise Dex5AssetError(f"missing URDF coupling for {name}")
        offset = prim.GetAttribute("physxMimicJoint:rotZ:offset")
        relationship = prim.GetRelationship("physxMimicJoint:rotZ:referenceJoint")
        targets = tuple(str(path) for path in relationship.GetTargets()) if relationship else ()
        required_reference = str(prim.GetPath()).rsplit("/", 1)[0] + "/" + mimic.attrib["joint"]
        if (targets != (required_reference,) or not math.isclose(float(gearing.Get()), -float(mimic.attrib["multiplier"]), abs_tol=1e-6)
                or not offset or not math.isclose(float(offset.Get()), 0.0, abs_tol=1e-9)):
            raise Dex5AssetError(f"PhysX coupling differs from checked URDF: {name}")
        found[name] = {"gearing": float(gearing.Get()), "reference": required_reference, "offset": float(offset.Get())}
        if require_hard_coupling:
            properties = {}
            for field in ("naturalFrequency", "dampingRatio"):
                attribute = prim.GetAttribute(f"physxMimicJoint:rotZ:{field}")
                value = attribute.Get() if attribute else None
                if not isinstance(value, (int, float)) or value != 0.0:
                    raise Dex5AssetError(f"hard PhysX coupling requires {field}=0 for {name}, got {value!r}")
                properties[field] = float(value)
            found[name].update(properties)
    if set(found) != set(expected):
        raise Dex5AssetError(f"missing PhysX mimic joints: {sorted(set(expected) - set(found))}")
    return {"mimic_joint_count": len(found), "mimic_joints": found,
            "hard_coupling_verified": require_hard_coupling}
