"""Optional URDF hand FK for measured-base IK; no simulator state writes.

Derived from the reproducible offline calibration utility. Collision AABBs
are only a seed constraint: the live PhysX safety guard remains authoritative.
"""
from pathlib import Path
from typing import Any
import itertools
import hashlib
import struct
import xml.etree.ElementTree as ET
from .dex5_demo import FINGERS, TIP_OFFSETS
SIDES = ("left", "right")
ARM_NAMES = ("shoulder_pitch", "shoulder_roll", "shoulder_yaw", "elbow", "wrist_roll", "wrist_pitch", "wrist_yaw")

class UrdfKinematics:
    """URDF FK and conservative transformed local collision bounding boxes."""

    def __init__(self, path: Path, np: Any, rotation: Any) -> None:
        self.path, self.np, self.rotation = path.resolve(), np, rotation
        self.base_transform = np.eye(4)
        self.base_transform[:3, 3] = (0., 0., .56)
        self.fixed_positions = {}
        self.root = ET.parse(self.path).getroot()
        self.joints: list[tuple[Any, ...]] = []
        self.limits: dict[str, tuple[float, float]] = {}
        self.mimics: dict[str, tuple[str, float, float]] = {}
        for joint in self.root.findall("joint"):
            name, kind = joint.attrib["name"], joint.attrib["type"]
            parent = joint.find("parent").attrib["link"]
            child = joint.find("child").attrib["link"]
            axis = joint.find("axis")
            axis_v = self.vector(axis.get("xyz", "1 0 0")) if axis is not None else np.array([1., 0., 0.])
            self.joints.append((name, kind, parent, child, self.origin(joint.find("origin")), axis_v))
            limit = joint.find("limit")
            if limit is not None:
                self.limits[name] = (float(limit.attrib["lower"]), float(limit.attrib["upper"]))
            mimic = joint.find("mimic")
            if mimic is not None:
                self.mimics[name] = (
                    mimic.attrib["joint"], float(mimic.get("multiplier", "1")),
                    float(mimic.get("offset", "0")),
                )
        self.corners: dict[str, Any] = {}
        self.mesh_hashes: dict[str, str] = {}
        for link in self.root.findall("link"):
            name = link.attrib["name"]
            if "hand_" not in name and "wrist_" not in name:
                continue
            all_corners = []
            for collision in link.findall("collision"):
                mesh = collision.find("geometry/mesh")
                if mesh is None:
                    raise ValueError(f"expected the pinned mesh collision geometry for {name}")
                mesh_path = (self.path.parent / mesh.attrib["filename"]).resolve()
                content = mesh_path.read_bytes()
                self.mesh_hashes[mesh.attrib["filename"]] = hashlib.sha256(content).hexdigest()
                vertices = self.stl_vertices(content, mesh_path)
                vertices = vertices * self.vector(mesh.get("scale", "1 1 1"))
                bounds = zip(vertices.min(axis=0), vertices.max(axis=0))
                box = np.array(list(itertools.product(*bounds)))
                homogeneous = np.c_[box, np.ones(len(box))]
                all_corners.append((self.origin(collision.find("origin")) @ homogeneous.T).T)
            if all_corners:
                self.corners[name] = np.concatenate(all_corners)
        for side in SIDES:
            names = self.joint_names(side)
            missing = set(names) - self.limits.keys()
            if missing:
                raise ValueError(f"missing required joint limits: {sorted(missing)}")
            required = {f"{side}_hand_{finger}_3_joint" for finger in FINGERS[1:]}
            if not required.issubset(self.mimics):
                raise ValueError("the composite URDF must explicitly include distal mimic relations")

    def vector(self, value: str) -> Any:
        result = self.np.array([float(item) for item in value.split()])
        if result.shape != (3,) or not self.np.isfinite(result).all():
            raise ValueError(f"expected a finite 3-vector, got {value!r}")
        return result

    def origin(self, element: ET.Element | None) -> Any:
        result = self.np.eye(4)
        if element is not None:
            result[:3, :3] = self.rotation.from_euler("xyz", self.vector(element.get("rpy", "0 0 0"))).as_matrix()
            result[:3, 3] = self.vector(element.get("xyz", "0 0 0"))
        return result

    def stl_vertices(self, content: bytes, path: Path) -> Any:
        if len(content) >= 84:
            count = struct.unpack_from("<I", content, 80)[0]
            if len(content) == 84 + count * 50 and count:
                dtype = self.np.dtype([
                    ("normal", "<f4", (3,)), ("vertices", "<f4", (3, 3)),
                    ("attribute", "<u2"),
                ])
                return self.np.frombuffer(content[84:], dtype=dtype)["vertices"].reshape(-1, 3)
        raise ValueError(f"expected a nonempty binary STL from the pinned asset package: {path}")

    @staticmethod
    def joint_names(side: str) -> list[str]:
        return [f"{side}_{name}_joint" for name in ARM_NAMES] + [
            f"{side}_hand_{finger}_{index}_joint"
            for finger in FINGERS for index in range(4 if finger == "thumb" else 3)
        ]

    def safe_seating(self, side: str) -> dict[str, float]:
        positions = dict.fromkeys(self.joint_names(side), 0.0)
        positions.update({
            f"{side}_shoulder_pitch_joint": -1.45,
            f"{side}_shoulder_roll_joint": 0.25 if side == "left" else -0.25,
            f"{side}_elbow_joint": 1.15,
        })
        return positions

    def forward(self, positions: dict[str, float]) -> dict[str, Any]:
        q = {**self.fixed_positions, **positions}
        for child, (parent, multiplier, offset) in self.mimics.items():
            if parent in q:
                q[child] = multiplier * q[parent] + offset
        pelvis = self.base_transform.copy()
        world = {"pelvis": pelvis}
        pending = list(self.joints)
        while pending:
            remaining = []
            for name, kind, parent, child, origin, axis in pending:
                if parent not in world:
                    remaining.append((name, kind, parent, child, origin, axis))
                    continue
                motion = self.np.eye(4)
                if kind in {"revolute", "continuous"}:
                    motion[:3, :3] = self.rotation.from_rotvec(axis * q.get(name, 0.0)).as_matrix()
                elif kind == "prismatic":
                    motion[:3, 3] = axis * q.get(name, 0.0)
                world[child] = world[parent] @ origin @ motion
            if len(remaining) == len(pending):
                break  # The support fixture ancestors are deliberately outside pelvis-relative FK.
            pending = remaining
        return world

    def measure(self, side: str, positions: dict[str, float]) -> tuple[Any, dict[str, float], Any]:
        world = self.forward(positions)
        tips = self.np.array([
            (world[f"{side}_hand_{finger}_3_link"] @ self.np.r_[TIP_OFFSETS[finger], 1])[:3]
            for finger in FINGERS
        ])
        minimum_z = {
            name: float(self.np.min((world[name] @ corners.T)[2]))
            for name, corners in self.corners.items() if name.startswith(side)
        }
        return tips, minimum_z, world[f"{side}_hand_palm_link"][:3, 3]
