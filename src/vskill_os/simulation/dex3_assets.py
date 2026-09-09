"""Validation and composition helpers for Unitree G1 + Dex3 simulation assets."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
import hashlib
import math
import os
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET

from ..robot.g1 import G1Joint


EXPECTED_BODY_JOINTS = tuple(f"{joint.name.lower()}_joint" for joint in G1Joint)
EXPECTED_DEX3_JOINTS = tuple(
    f"{side}_hand_{name}_joint"
    for side in ("left", "right")
    for name in (
        "thumb_0",
        "thumb_1",
        "thumb_2",
        "middle_0",
        "middle_1",
        "index_0",
        "index_1",
    )
)

ISAAC_FRAME_LINKS = (
    "imu_in_torso",
    "imu_in_pelvis",
    "d435_link",
    "mid360_link",
)


class AssetValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class UrdfValidationReport:
    path: str
    robot_name: str
    link_count: int
    joint_count: int
    movable_joint_count: int
    body_joint_count: int
    dex3_joint_count: int
    mesh_count: int
    sha256: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class UsdValidationReport:
    path: str
    file_size: int
    sha256: str
    format: str
    pxr_parsed: bool
    default_prim: str | None = None
    prim_count: int | None = None
    physics_joint_count: int | None = None
    revolute_joint_count: int | None = None
    body_joint_count: int | None = None
    dex3_joint_count: int | None = None
    used_layer_count: int | None = None
    rigid_body_count: int | None = None
    collision_shape_count: int | None = None
    dex3_collision_shape_count: int | None = None
    positive_mass_count: int | None = None
    minimum_mass_kg: float | None = None
    maximum_mass_kg: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def validate_g1_dex3_urdf(path: str | Path) -> UrdfValidationReport:
    source = Path(path).resolve()
    try:
        root = ET.parse(source).getroot()
    except (ET.ParseError, OSError) as exc:
        raise AssetValidationError(f"cannot parse URDF {source}: {exc}") from exc
    if root.tag != "robot" or not root.attrib.get("name"):
        raise AssetValidationError("URDF root must be a named robot")

    links = [item.attrib.get("name", "") for item in root.findall("link")]
    joints = root.findall("joint")
    joint_names = [item.attrib.get("name", "") for item in joints]
    errors: list[str] = []
    if not links or len(links) != len(set(links)) or "" in links:
        errors.append("link names must be present and unique")
    if not joints or len(joint_names) != len(set(joint_names)) or "" in joint_names:
        errors.append("joint names must be present and unique")

    link_set = set(links)
    movable: set[str] = set()
    children: set[str] = set()
    adjacency: dict[str, list[str]] = {link: [] for link in links}
    for joint in joints:
        name = joint.attrib.get("name", "")
        joint_type = joint.attrib.get("type", "")
        parent = joint.find("parent")
        child = joint.find("child")
        parent_name = "" if parent is None else parent.attrib.get("link", "")
        child_name = "" if child is None else child.attrib.get("link", "")
        if parent_name not in link_set or child_name not in link_set:
            errors.append(f"joint {name!r} references an unknown parent or child")
        elif parent_name == child_name:
            errors.append(f"joint {name!r} forms a self-loop")
        else:
            adjacency[parent_name].append(child_name)
            if child_name in children:
                errors.append(f"link {child_name!r} has multiple parent joints")
            children.add(child_name)
        if joint_type != "fixed":
            movable.add(name)
        if joint_type == "revolute":
            limit = joint.find("limit")
            try:
                if limit is None:
                    raise ValueError
                values = tuple(
                    float(limit.attrib[field])
                    for field in ("lower", "upper", "effort", "velocity")
                )
                if not all(math.isfinite(value) for value in values):
                    raise ValueError
                if values[0] > values[1] or values[2] <= 0.0 or values[3] <= 0.0:
                    raise ValueError
            except (AttributeError, KeyError, TypeError, ValueError):
                errors.append(f"revolute joint {name!r} has invalid limits")

    root_links = link_set - children
    if len(root_links) != 1:
        errors.append(f"URDF must have exactly one root link, found {sorted(root_links)}")
    else:
        pending = list(root_links)
        visited: set[str] = set()
        while pending:
            current = pending.pop()
            if current in visited:
                errors.append(f"URDF joint graph contains a cycle at {current!r}")
                break
            visited.add(current)
            pending.extend(adjacency[current])
        if len(visited) != len(link_set):
            errors.append("URDF joint graph is disconnected")

    mesh_paths: list[Path] = []
    for mesh in root.findall(".//mesh"):
        filename = mesh.attrib.get("filename", "")
        if not filename or filename.startswith(("package://", "http://", "https://")):
            errors.append(f"unsupported or empty mesh reference {filename!r}")
            continue
        mesh_path = Path(filename)
        if not mesh_path.is_absolute():
            mesh_path = source.parent / mesh_path
        mesh_paths.append(mesh_path.resolve())
        if not mesh_path.is_file():
            errors.append(f"missing mesh: {filename}")

    body = set(EXPECTED_BODY_JOINTS)
    dex3 = set(EXPECTED_DEX3_JOINTS)
    if movable != body | dex3:
        missing = sorted((body | dex3) - movable)
        extra = sorted(movable - (body | dex3))
        errors.append(f"movable joint set mismatch; missing={missing}, extra={extra}")
    if errors:
        raise AssetValidationError("; ".join(errors))
    return UrdfValidationReport(
        path=str(source),
        robot_name=root.attrib["name"],
        link_count=len(links),
        joint_count=len(joints),
        movable_joint_count=len(movable),
        body_joint_count=len(movable & body),
        dex3_joint_count=len(movable & dex3),
        mesh_count=len(set(mesh_paths)),
        sha256=_sha256(source),
    )


def compose_mode15_g1_with_dex3(
    *,
    g1_urdf: str | Path,
    left_hand_urdf: str | Path,
    right_hand_urdf: str | Path,
    output: str | Path,
) -> UrdfValidationReport:
    """Attach standalone Dex3-1 descriptions to the mode-15/5010 wrist links."""

    base_path = Path(g1_urdf).resolve()
    target = Path(output).resolve()
    hand_paths = (Path(left_hand_urdf).resolve(), Path(right_hand_urdf).resolve())
    source_paths = {base_path, *hand_paths}
    if target in source_paths:
        raise AssetValidationError("output must not overwrite a source URDF")

    try:
        base_tree = ET.parse(base_path)
    except (ET.ParseError, OSError) as exc:
        raise AssetValidationError(f"cannot parse G1 source {base_path}: {exc}") from exc
    root = base_tree.getroot()
    if root.tag != "robot":
        raise AssetValidationError("G1 source is not a URDF robot")
    base_movable = {
        item.attrib["name"]
        for item in root.findall("joint")
        if item.attrib.get("type") != "fixed"
    }
    if base_movable != set(EXPECTED_BODY_JOINTS):
        raise AssetValidationError("G1 source is not the expected 29-DoF body model")
    _rebase_mesh_paths(root, source_dir=base_path.parent, target_dir=target.parent)

    # The mode-15 body ships with fixed rubber-hand placeholders using the same
    # attachment-joint names. Remove only those placeholders before adding Dex3.
    for element in tuple(root):
        if element.tag == "joint" and element.attrib.get("name") in {
            "left_hand_palm_joint",
            "right_hand_palm_joint",
        }:
            root.remove(element)
        elif element.tag == "link" and element.attrib.get("name") in {
            "left_rubber_hand",
            "right_rubber_hand",
        }:
            root.remove(element)

    existing_links = {item.attrib["name"] for item in root.findall("link")}
    existing_joints = {item.attrib["name"] for item in root.findall("joint")}
    for hand_path in hand_paths:
        try:
            hand_root = ET.parse(hand_path).getroot()
        except (ET.ParseError, OSError) as exc:
            raise AssetValidationError(
                f"cannot parse Dex3 source {hand_path}: {exc}"
            ) from exc
        if hand_root.tag != "robot":
            raise AssetValidationError(f"Dex3 source is not a URDF robot: {hand_path}")
        _rebase_mesh_paths(
            hand_root,
            source_dir=hand_path.parent,
            target_dir=target.parent,
        )
        for element in hand_root:
            if element.tag == "link":
                name = element.attrib["name"]
                if name in existing_links:
                    raise AssetValidationError(f"duplicate hand link {name!r}")
                existing_links.add(name)
                root.append(deepcopy(element))
            elif element.tag == "joint":
                name = element.attrib["name"]
                if name in existing_joints:
                    raise AssetValidationError(f"duplicate hand joint {name!r}")
                existing_joints.add(name)
                root.append(deepcopy(element))

    for side, lateral_offset in (("left", "0.003"), ("right", "-0.003")):
        attachment = ET.Element(
            "joint", {"name": f"{side}_hand_palm_joint", "type": "fixed"}
        )
        ET.SubElement(attachment, "origin", {"xyz": f"0.0415 {lateral_offset} 0", "rpy": "0 0 0"})
        ET.SubElement(attachment, "parent", {"link": f"{side}_wrist_yaw_link"})
        ET.SubElement(attachment, "child", {"link": f"{side}_hand_palm_link"})
        root.append(attachment)

    _make_empty_sensor_frames_importable(root)

    root.attrib["name"] = "g1_29dof_mode_15_with_dex3_1"
    ET.indent(base_tree, space="  ")
    target.parent.mkdir(parents=True, exist_ok=True)
    base_tree.write(target, encoding="utf-8", xml_declaration=True)
    return validate_g1_dex3_urdf(target)


def validate_g1_dex3_usd(
    path: str | Path,
    *,
    expected_sha256: str | None = None,
    require_pxr: bool = False,
    require_positive_masses: bool = True,
) -> UsdValidationReport:
    source = Path(path).resolve()
    if not source.is_file():
        raise AssetValidationError(f"USD file does not exist: {source}")
    digest = _sha256(source)
    if expected_sha256 is not None and digest != expected_sha256:
        raise AssetValidationError(f"USD SHA-256 mismatch: {digest}")
    with source.open("rb") as stream:
        header = stream.read(8)
    if header == b"PXR-USDC":
        format_name = "usdc"
    elif header.startswith(b"#usda"):
        format_name = "usda"
    else:
        raise AssetValidationError(f"unsupported USD header: {header!r}")

    try:
        from pxr import Usd, UsdPhysics
    except ImportError as exc:
        if require_pxr:
            raise AssetValidationError("pxr/OpenUSD is required for stage validation") from exc
        return UsdValidationReport(
            path=str(source),
            file_size=source.stat().st_size,
            sha256=digest,
            format=format_name,
            pxr_parsed=False,
        )

    stage = Usd.Stage.Open(str(source))
    if stage is None:
        raise AssetValidationError("OpenUSD could not open the stage")
    prims = list(stage.Traverse())
    prims_with_instances = list(Usd.PrimRange.Stage(stage, Usd.TraverseInstanceProxies()))
    physics_joints = [prim for prim in prims if prim.IsA(UsdPhysics.Joint)]
    revolute = [prim.GetName() for prim in prims if prim.IsA(UsdPhysics.RevoluteJoint)]
    rigid_bodies = [prim for prim in prims if prim.HasAPI(UsdPhysics.RigidBodyAPI)]
    collision_shapes = [
        prim for prim in prims_with_instances if prim.HasAPI(UsdPhysics.CollisionAPI)
    ]
    masses: list[float] = []
    for prim in rigid_bodies:
        if not prim.HasAPI(UsdPhysics.MassAPI):
            if require_positive_masses:
                raise AssetValidationError(f"USD rigid body lacks mass schema: {prim.GetPath()}")
            continue
        value = UsdPhysics.MassAPI(prim).GetMassAttr().Get()
        if value is None or not math.isfinite(float(value)) or float(value) <= 0.0:
            if require_positive_masses:
                raise AssetValidationError(f"USD rigid body has invalid mass: {prim.GetPath()}")
            continue
        masses.append(float(value))
    body = set(EXPECTED_BODY_JOINTS)
    dex3 = set(EXPECTED_DEX3_JOINTS)
    if len(revolute) != len(body | dex3) or set(revolute) != body | dex3:
        raise AssetValidationError("USD revolute joint set does not match G1 29DoF + dual Dex3")
    default_prim = stage.GetDefaultPrim()
    if not default_prim.IsValid():
        raise AssetValidationError("USD stage has no valid default prim")
    return UsdValidationReport(
        path=str(source),
        file_size=source.stat().st_size,
        sha256=digest,
        format=format_name,
        pxr_parsed=True,
        default_prim=default_prim.GetPath().pathString,
        prim_count=len(prims),
        physics_joint_count=len(physics_joints),
        revolute_joint_count=len(revolute),
        body_joint_count=len(set(revolute) & body),
        dex3_joint_count=len(set(revolute) & dex3),
        used_layer_count=len(stage.GetUsedLayers()),
        rigid_body_count=len(rigid_bodies),
        collision_shape_count=len(collision_shapes),
        dex3_collision_shape_count=sum(
            1 for prim in collision_shapes if "_hand_" in prim.GetPath().pathString
        ),
        positive_mass_count=len(masses),
        minimum_mass_kg=min(masses) if masses else None,
        maximum_mass_kg=max(masses) if masses else None,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _rebase_mesh_paths(root: ET.Element, *, source_dir: Path, target_dir: Path) -> None:
    """Keep relative mesh references valid when URDF fragments are relocated."""

    for mesh in root.findall(".//mesh"):
        filename = mesh.attrib.get("filename", "")
        if not filename or filename.startswith(("package://", "http://", "https://")):
            continue
        source = Path(filename)
        if not source.is_absolute():
            source = (source_dir / source).resolve()
        mesh.attrib["filename"] = os.path.relpath(source, start=target_dir)


def _make_empty_sensor_frames_importable(root: ET.Element) -> None:
    """Give coordinate-only sensor links negligible, invisible import geometry.

    Unitree's mode-15 URDF intentionally models these links as empty frames.
    Isaac Sim's URDF importer otherwise authors dangling ``visuals`` references
    and synthetic inertias for them.  A transparent micrometer sphere preserves
    the frame and produces a warning-free, deterministic USD conversion without
    adding collision geometry.
    """

    for name in ISAAC_FRAME_LINKS:
        link = root.find(f"link[@name='{name}']")
        if link is None:
            continue
        if link.find("inertial") is None:
            inertial = ET.SubElement(link, "inertial")
            ET.SubElement(inertial, "origin", {"xyz": "0 0 0", "rpy": "0 0 0"})
            ET.SubElement(inertial, "mass", {"value": "0.0001"})
            ET.SubElement(
                inertial,
                "inertia",
                {
                    "ixx": "1e-8",
                    "ixy": "0",
                    "ixz": "0",
                    "iyy": "1e-8",
                    "iyz": "0",
                    "izz": "1e-8",
                },
            )
        if link.find("visual") is None:
            visual = ET.SubElement(link, "visual", {"name": "isaac_import_placeholder"})
            ET.SubElement(visual, "origin", {"xyz": "0 0 0", "rpy": "0 0 0"})
            geometry = ET.SubElement(visual, "geometry")
            ET.SubElement(geometry, "sphere", {"radius": "1e-6"})
            material = ET.SubElement(visual, "material", {"name": "transparent"})
            ET.SubElement(material, "color", {"rgba": "0 0 0 0"})
