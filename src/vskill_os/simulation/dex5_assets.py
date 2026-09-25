"""Pinned, auditable Unitree G1 + Dex5-1 assets, without simulator imports.

The OEM URDF describes 20 coordinates but omits the four distal couplings.
This simulation adapter adds explicit approximate mimic relations.  Neither the
coupling calibration nor the wrist mount is claimed to be hardware validated.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import asdict, dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import urllib.request
from typing import Any
import xml.etree.ElementTree as ET

from ..robot.g1 import G1Joint


UNITREE_COMMIT = "7d6075f7f58588b189b940130e3edab3c839b2df"
DEFAULT_MANIFEST = "configs/simulation/g1_29dof_5010_dex5_assets.json"
DEFAULT_ASSET_ROOT = "artifacts/simulation-assets/unitree_ros_7d6075f7"
BODY_SOURCE = "robots/g1_description/g1_29dof_mode_15.urdf"
HAND_SOURCES = {
    side: f"robots/dexterous_hand_description/dex5_1/Dex5-URDF-{suffix}/Dex5-URDF-{suffix}.urdf"
    for side, suffix in (("left", "L"), ("right", "R"))
}
FINGERS = ("thumb", "index", "middle", "ring", "little")
EXPECTED_BODY_JOINTS = tuple(f"{joint.name.lower()}_joint" for joint in G1Joint)
EXPECTED_DEX5_JOINTS = tuple(
    f"{side}_hand_{finger}_{index}_joint"
    for side in ("left", "right") for finger in FINGERS for index in range(4)
)
COUPLED_DEX5_JOINTS = tuple(
    f"{side}_hand_{finger}_3_joint"
    for side in ("left", "right") for finger in FINGERS[1:]
)
ACTIVE_DEX5_JOINTS = tuple(name for name in EXPECTED_DEX5_JOINTS if name not in COUPLED_DEX5_JOINTS)
SEATING_FIXTURE_JOINT = "seating_fixture_lift_joint"
SEATING_FIXTURE_SLIDE_JOINT = "seating_fixture_slide_joint"
SEATING_FIXTURE_JOINTS = (SEATING_FIXTURE_SLIDE_JOINT, SEATING_FIXTURE_JOINT)


class Dex5AssetError(ValueError):
    """The requested Dex5-1 asset cannot be independently validated."""


@dataclass(frozen=True, slots=True)
class Dex5UrdfReport:
    path: str
    sha256: str
    link_count: int
    joint_count: int
    body_joint_count: int
    dex5_coordinate_count: int
    dex5_active_joint_count: int
    dex5_coupled_joint_count: int
    fixture_joint_count: int
    mesh_count: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _parse(path: Path) -> ET.Element:
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as exc:
        raise Dex5AssetError(f"cannot parse URDF {path}: {exc}") from exc
    if root.tag != "robot" or not root.get("name"):
        raise Dex5AssetError("URDF must contain a named robot")
    return root


def _source_names(side: str) -> tuple[dict[str, str], dict[str, str]]:
    suffix = {"left": "L", "right": "R"}[side]
    links = {"base_link00L" if side == "left" else "base_link00": f"{side}_hand_palm_link"}
    joints: dict[str, str] = {}
    for digit, finger in enumerate(FINGERS, 1):
        for segment in range(1, 5):
            links[f"Link_{digit}{segment}{suffix}"] = f"{side}_hand_{finger}_{segment - 1}_link"
            prefix = "Yaw" if (digit, segment) == (1, 1) else (
                "Roll" if segment == 1 or (digit, segment) == (1, 2) else "Pitch"
            )
            # Upstream spells this one joint exactly like its child link.
            if (side, digit, segment) == ("left", 4, 1):
                prefix = "Link"
            joints[f"{prefix}_{digit}{segment}{suffix}"] = f"{side}_hand_{finger}_{segment - 1}_joint"
    return links, joints


def _validate_tree(root: ET.Element, source: Path, *, allow_zero_limits: bool = False) -> set[Path]:
    links = [link.get("name", "") for link in root.findall("link")]
    joints = root.findall("joint")
    names = [joint.get("name", "") for joint in joints]
    if not links or "" in links or len(set(links)) != len(links):
        raise Dex5AssetError("link names must be present and unique")
    if "" in names or len(set(names)) != len(names):
        raise Dex5AssetError("joint names must be present and unique")
    children: set[str] = set()
    graph: dict[str, list[str]] = {name: [] for name in links}
    for joint in joints:
        parent, child = joint.find("parent"), joint.find("child")
        p = parent.get("link", "") if parent is not None else ""
        c = child.get("link", "") if child is not None else ""
        if p not in graph or c not in graph or p == c or c in children:
            raise Dex5AssetError(f"invalid or duplicate parent-child graph at {joint.get('name')}")
        graph[p].append(c)
        children.add(c)
        if joint.get("type") != "fixed":
            limit = joint.find("limit")
            try:
                if joint.get("type") not in {"revolute", "prismatic"} or limit is None:
                    raise ValueError
                lower, upper, effort, velocity = (float(limit.attrib[k]) for k in ("lower", "upper", "effort", "velocity"))
                if not all(math.isfinite(v) for v in (lower, upper, effort, velocity)) or lower >= upper:
                    raise ValueError
                if min(effort, velocity) < 0 or (not allow_zero_limits and min(effort, velocity) <= 0):
                    raise ValueError
            except (KeyError, ValueError):
                raise Dex5AssetError(f"invalid limits for {joint.get('name')}") from None
    roots = set(links) - children
    if len(roots) != 1:
        raise Dex5AssetError("URDF must have exactly one root link")
    visited: set[str] = set()
    pending = list(roots)
    while pending:
        current = pending.pop()
        if current in visited:
            raise Dex5AssetError("URDF has a cycle")
        visited.add(current)
        pending.extend(graph[current])
    if visited != set(links):
        raise Dex5AssetError("URDF has a disconnected joint graph")
    meshes: set[Path] = set()
    for mesh in root.findall(".//mesh"):
        name = mesh.get("filename", "")
        if not name or "://" in name:
            raise Dex5AssetError(f"unsupported mesh reference: {name}")
        path = (source.parent / name).resolve()
        if not path.is_file():
            raise Dex5AssetError(f"missing mesh: {path}")
        meshes.add(path)
    return meshes


def audit_dex5_hand(path: str | Path, side: str) -> dict[str, Any]:
    """Inspect the unmodified OEM model, retaining known upstream defects."""
    source = Path(path).resolve()
    root = _parse(source)
    links, joints = _source_names(side)
    if {e.get("name") for e in root.findall("link")} != set(links):
        raise Dex5AssetError(f"{side} hand does not have the official Dex5-1 link set")
    if {e.get("name") for e in root.findall("joint")} != set(joints):
        raise Dex5AssetError(f"{side} hand does not have the official Dex5-1 joint set")
    meshes = _validate_tree(root, source, allow_zero_limits=True)
    zero_effort: list[str] = []
    zero_velocity: list[str] = []
    for joint in root.findall("joint"):
        limit = joint.find("limit")
        if joint.get("type") != "revolute" or limit is None:
            raise Dex5AssetError("official Dex5-1 must have 20 revolute coordinates")
        if float(limit.attrib["effort"]) == 0:
            zero_effort.append(joint.attrib["name"])
        if float(limit.attrib["velocity"]) == 0:
            zero_velocity.append(joint.attrib["name"])
    return {
        "path": str(source), "sha256": _sha256(source), "side": side,
        "coordinate_count": 20, "manufacturer_active_count": 16,
        "manufacturer_coupled_count": 4, "mesh_count": len(meshes),
        "source_mimic_count": len(root.findall("joint/mimic")),
        "zero_effort_joints": zero_effort, "zero_velocity_joints": zero_velocity,
    }


def validate_g1_dex5_urdf(path: str | Path) -> Dex5UrdfReport:
    source = Path(path).resolve()
    root = _parse(source)
    meshes = _validate_tree(root, source)
    joints = {j.attrib["name"]: j for j in root.findall("joint")}
    movable = {n for n, j in joints.items() if j.get("type") != "fixed"}
    fixture = len(set(SEATING_FIXTURE_JOINTS) & movable)
    expected = set(EXPECTED_BODY_JOINTS) | set(EXPECTED_DEX5_JOINTS)
    if fixture:
        expected.update(SEATING_FIXTURE_JOINTS)
    if movable != expected:
        raise Dex5AssetError(f"joint set mismatch: missing={sorted(expected-movable)}, extra={sorted(movable-expected)}")
    if fixture:
        support_specs = (
            (SEATING_FIXTURE_SLIDE_JOINT, "seating_fixture_base", "seating_fixture_carriage", "1 0 0", -0.30, 0.15),
            (SEATING_FIXTURE_JOINT, "seating_fixture_carriage", "pelvis", "0 0 1", -0.27, 0.10),
        )
        for name, parent_name, child_name, axis_value, lower, velocity in support_specs:
            joint = joints[name]
            parent, child, axis, limit = (joint.find(tag) for tag in ("parent", "child", "axis", "limit"))
            if (joint.get("type") != "prismatic" or parent is None or child is None or axis is None or limit is None
                    or parent.get("link") != parent_name or child.get("link") != child_name or axis.get("xyz") != axis_value
                    or tuple(float(limit.attrib[key]) for key in ("lower", "upper", "effort", "velocity")) != (lower, 0, 2000, velocity)):
                raise Dex5AssetError(f"invalid declared support fixture for {name}")
    mimic_joints = {name for name, joint in joints.items() if joint.find("mimic") is not None}
    if mimic_joints != set(COUPLED_DEX5_JOINTS):
        raise Dex5AssetError("Dex5-1 requires exactly eight coupled distal mimic joints")
    for name in COUPLED_DEX5_JOINTS:
        mimic = joints[name].find("mimic")
        assert mimic is not None
        driver = name.replace("_3_joint", "_2_joint")
        limit, driver_limit = joints[name].find("limit"), joints[driver].find("limit")
        assert limit is not None and driver_limit is not None
        ratio = float(limit.attrib["upper"]) / float(driver_limit.attrib["upper"])
        if (mimic.get("joint") != driver or not math.isclose(float(mimic.get("multiplier", "nan")), ratio, abs_tol=1e-12)
                or float(mimic.get("offset", "nan")) != 0):
            raise Dex5AssetError(f"invalid simulation coupling for {name}")
    return Dex5UrdfReport(str(source), _sha256(source), len(root.findall("link")), len(joints),
                          29, 40, 32, 8, fixture, len(meshes))


def _rebase_meshes(root: ET.Element, source: Path, target: Path) -> None:
    for mesh in root.findall(".//mesh"):
        mesh.set("filename", os.path.relpath((source.parent / mesh.attrib["filename"]).resolve(), target.parent))


def _tiny_inertial(link: ET.Element, *, visual: bool) -> None:
    if link.find("inertial") is None:
        inertial = ET.SubElement(link, "inertial")
        ET.SubElement(inertial, "mass", {"value": "0.0001"})
        ET.SubElement(inertial, "inertia", {"ixx": "1e-8", "iyy": "1e-8", "izz": "1e-8", "ixy": "0", "ixz": "0", "iyz": "0"})
    if visual and link.find("visual") is None:
        geometry = ET.SubElement(ET.SubElement(link, "visual"), "geometry")
        ET.SubElement(geometry, "sphere", {"radius": "0.000001"})


def compose_mode15_g1_with_dex5(*, g1_urdf: str | Path, left_hand_urdf: str | Path,
                               right_hand_urdf: str | Path, output: str | Path,
                               seating_fixture: bool = False) -> Dex5UrdfReport:
    """Compose OEM geometry with documented simulation mount/limit/coupling edits."""
    body = Path(g1_urdf).resolve()
    sources = {"left": Path(left_hand_urdf).resolve(), "right": Path(right_hand_urdf).resolve()}
    target = Path(output).resolve()
    if target in {body, *sources.values()}:
        raise Dex5AssetError("output must not overwrite a source URDF")
    root = _parse(body)
    _validate_tree(root, body)
    if {j.get("name") for j in root.findall("joint") if j.get("type") != "fixed"} != set(EXPECTED_BODY_JOINTS):
        raise Dex5AssetError("body must be the 29-DoF G1 mode-15 model")
    for side, source in sources.items():
        audit_dex5_hand(source, side)
    _rebase_meshes(root, body, target)
    for element in tuple(root):
        if ((element.tag == "joint" and element.get("name") in {"left_hand_palm_joint", "right_hand_palm_joint"})
                or (element.tag == "link" and element.get("name") in {"left_rubber_hand", "right_rubber_hand"})):
            root.remove(element)
    for side, source in sources.items():
        hand = _parse(source)
        links, names = _source_names(side)
        _rebase_meshes(hand, source, target)
        for link in hand.findall("link"):
            link.set("name", links[link.attrib["name"]])
        for joint in hand.findall("joint"):
            joint.set("name", names[joint.attrib["name"]])
            for tag in ("parent", "child"):
                element = joint.find(tag)
                assert element is not None
                element.set("link", links[element.attrib["link"]])
            limit = joint.find("limit")
            assert limit is not None
            # The right export contains 16 zero efforts and two zero velocities.
            # Values below match corresponding left export joints; simulation only.
            if float(limit.attrib["effort"]) == 0:
                limit.set("effort", "0.93")
            if float(limit.attrib["velocity"]) == 0:
                limit.set("velocity", "0.76794")
        joint_map = {j.attrib["name"]: j for j in hand.findall("joint")}
        for finger in FINGERS[1:]:
            distal = joint_map[f"{side}_hand_{finger}_3_joint"]
            driver = joint_map[f"{side}_hand_{finger}_2_joint"]
            distal_limit, driver_limit = distal.find("limit"), driver.find("limit")
            assert distal_limit is not None and driver_limit is not None
            ratio = float(distal_limit.attrib["upper"]) / float(driver_limit.attrib["upper"])
            ET.SubElement(distal, "mimic", {"joint": driver.attrib["name"], "multiplier": format(ratio, ".16g"), "offset": "0"})
        for element in hand:
            if element.tag in {"link", "joint", "material"}:
                root.append(deepcopy(element))
        mount = ET.SubElement(root, "joint", {"name": f"{side}_hand_palm_joint", "type": "fixed"})
        ET.SubElement(mount, "parent", {"link": f"{side}_wrist_yaw_link"})
        ET.SubElement(mount, "child", {"link": f"{side}_hand_palm_link"})
        ET.SubElement(mount, "origin", {
            "xyz": "0.0415 0.003 0" if side == "left" else "0.0415 -0.003 0",
            "rpy": f"0 0 {-math.pi / 2}" if side == "left" else f"{math.pi} 0 {math.pi / 2}",
        })
    for link in root.findall("link"):
        if link.attrib["name"] in {"imu_in_torso", "imu_in_pelvis", "d435_link", "mid360_link"}:
            _tiny_inertial(link, visual=True)
    if seating_fixture:
        fixture = ET.SubElement(root, "link", {"name": "seating_fixture_base"})
        _tiny_inertial(fixture, visual=True)
        carriage = ET.SubElement(root, "link", {"name": "seating_fixture_carriage"})
        _tiny_inertial(carriage, visual=True)
        slide = ET.SubElement(root, "joint", {"name": SEATING_FIXTURE_SLIDE_JOINT, "type": "prismatic"})
        ET.SubElement(slide, "parent", {"link": "seating_fixture_base"})
        ET.SubElement(slide, "child", {"link": "seating_fixture_carriage"})
        ET.SubElement(slide, "origin", {"xyz": "0 0 0", "rpy": "0 0 0"})
        ET.SubElement(slide, "axis", {"xyz": "1 0 0"})
        ET.SubElement(slide, "limit", {"lower": "-0.30", "upper": "0", "effort": "2000", "velocity": "0.15"})
        lift = ET.SubElement(root, "joint", {"name": SEATING_FIXTURE_JOINT, "type": "prismatic"})
        ET.SubElement(lift, "parent", {"link": "seating_fixture_carriage"})
        ET.SubElement(lift, "child", {"link": "pelvis"})
        ET.SubElement(lift, "origin", {"xyz": "0 0 0", "rpy": "0 0 0"})
        ET.SubElement(lift, "axis", {"xyz": "0 0 1"})
        ET.SubElement(lift, "limit", {"lower": "-0.27", "upper": "0", "effort": "2000", "velocity": "0.10"})
    root.set("name", "g1_29dof_mode_15_with_dex5_1" + ("_seating_fixture" if seating_fixture else ""))
    ET.indent(root, space="  ")
    target.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(target, encoding="utf-8", xml_declaration=True)
    return validate_g1_dex5_urdf(target)


def _manifest_files(manifest: str | Path, asset_root: str | Path) -> tuple[dict[str, Any], list[tuple[Path, dict[str, Any]]]]:
    data = json.loads(Path(manifest).read_text(encoding="utf-8"))
    if data.get("commit") != UNITREE_COMMIT or data.get("repository") != "https://github.com/unitreerobotics/unitree_ros":
        raise Dex5AssetError("asset manifest must use the pinned official Unitree repository")
    root = Path(asset_root).resolve()
    files = []
    for item in data["files"]:
        path = (root / item["path"]).resolve()
        if not path.is_relative_to(root) or Path(item["path"]).is_absolute():
            raise Dex5AssetError("asset manifest path escapes asset root")
        files.append((path, item))
    return data, files


def audit_pinned_assets(*, manifest: str | Path = DEFAULT_MANIFEST,
                        asset_root: str | Path = DEFAULT_ASSET_ROOT) -> dict[str, Any]:
    data, files = _manifest_files(manifest, asset_root)
    errors = []
    for path, item in files:
        if not path.is_file():
            errors.append(f"missing {item['path']}")
        elif path.stat().st_size != item["size_bytes"] or _sha256(path) != item["sha256"]:
            errors.append(f"SHA-256/size mismatch {item['path']}")
    if errors:
        raise Dex5AssetError("; ".join(errors))
    return {"repository": data["repository"], "commit": UNITREE_COMMIT, "license": data["license"],
            "verified_file_count": len(files), "source_bytes": sum(i["size_bytes"] for _, i in files),
            "hands": {s: audit_dex5_hand(Path(asset_root) / rel, s) for s, rel in HAND_SOURCES.items()}}


def fetch_pinned_assets(*, manifest: str | Path = DEFAULT_MANIFEST,
                        asset_root: str | Path = DEFAULT_ASSET_ROOT) -> dict[str, Any]:
    """Fetch only missing files; refuse to overwrite a local file with a bad hash."""
    _, files = _manifest_files(manifest, asset_root)
    for path, item in files:
        if path.exists():
            if not path.is_file() or _sha256(path) != item["sha256"]:
                raise Dex5AssetError(f"existing source differs from pinned manifest: {path}")
            continue
        url = f"https://raw.githubusercontent.com/unitreerobotics/unitree_ros/{UNITREE_COMMIT}/{item['path']}"
        with urllib.request.urlopen(url, timeout=60) as response:
            content = response.read(item["size_bytes"] + 1)
        if len(content) != item["size_bytes"] or hashlib.sha256(content).hexdigest() != item["sha256"]:
            raise Dex5AssetError(f"download SHA-256/size mismatch: {item['path']}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return audit_pinned_assets(manifest=manifest, asset_root=asset_root)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default=DEFAULT_MANIFEST)
    parser.add_argument("--asset-root", default=DEFAULT_ASSET_ROOT)
    parser.add_argument("--fetch", action="store_true", help="download missing official pinned source files")
    parser.add_argument("--compose", action="store_true")
    parser.add_argument("--seating-fixture", action="store_true", help="add declared simulation-only slide and lift support axes")
    parser.add_argument("--output", default="artifacts/isaac-dex5/assets/g1_mode15_dex5.urdf")
    parser.add_argument("--report")
    args = parser.parse_args()
    try:
        audit = fetch_pinned_assets if args.fetch else audit_pinned_assets
        report = audit(manifest=args.manifest, asset_root=args.asset_root)
        if args.compose:
            root = Path(args.asset_root)
            report["composite"] = compose_mode15_g1_with_dex5(
                g1_urdf=root / BODY_SOURCE, left_hand_urdf=root / HAND_SOURCES["left"],
                right_hand_urdf=root / HAND_SOURCES["right"], output=args.output,
                seating_fixture=args.seating_fixture,
            ).to_dict()
            license_copy = Path(args.output).parent / "UNITREE_LICENSE.txt"
            license_copy.write_bytes((root / "LICENSE").read_bytes())
            report["license_copy"] = str(license_copy.resolve())
        output = json.dumps(report, indent=2)
        if args.report:
            destination = Path(args.report)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(output + "\n", encoding="utf-8")
        print(output)
        return 0
    except (Dex5AssetError, OSError) as exc:
        parser.exit(2, f"Dex5-1 asset audit failed: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
