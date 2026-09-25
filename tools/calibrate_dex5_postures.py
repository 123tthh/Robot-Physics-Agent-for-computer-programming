#!/usr/bin/env python3
"""Reproduce offline G1 + Dex5 posture seeds without starting Isaac Sim.

Requires optional NumPy and SciPy, available in the project's Isaac Lab Python
environment. The result is a kinematic seed, never a dynamic acceptance report.
The default output is separate from the reviewed configuration.

Example:
    /home/descfly/miniconda3/envs/isaaclab-2.3.2/bin/python \
        tools/calibrate_dex5_postures.py --output /tmp/dex5_postures.json
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
from pathlib import Path
import struct
import sys
from typing import Any
import xml.etree.ElementTree as ET


REPO_ROOT = Path(__file__).resolve().parents[1]
FINGERS = ("thumb", "index", "middle", "ring", "little")
SIDES = ("left", "right")
ARM_NAMES = (
    "shoulder_pitch", "shoulder_roll", "shoulder_yaw", "elbow",
    "wrist_roll", "wrist_pitch", "wrist_yaw",
)
HOME_KEYS = {
    "left": ("space", "f", "d", "s", "a"),
    "right": ("space", "j", "k", "l", ";"),
}
TIP_OFFSETS = {
    finger: ((0.055, 0.008, 0.0) if finger == "thumb" else (0.026, 0.006, 0.0))
    for finger in FINGERS
}
REFERENCE_PELVIS = (0.0, 0.0, 0.56)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--urdf", type=Path,
        default=REPO_ROOT / "artifacts/isaac-dex5/assets/g1_mode15_dex5.urdf",
    )
    parser.add_argument(
        "--output", type=Path,
        default=REPO_ROOT / "artifacts/dex5/calibration/dex5_postures.json",
        help="Defaults to a candidate file; does not replace the reviewed configuration.",
    )
    parser.add_argument("--seed", type=int, default=7)
    return parser.parse_args()


class UrdfKinematics:
    """URDF FK and conservative transformed local collision bounding boxes."""

    def __init__(self, path: Path, np: Any, rotation: Any) -> None:
        self.path, self.np, self.rotation = path.resolve(), np, rotation
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
        q = dict(positions)
        for child, (parent, multiplier, offset) in self.mimics.items():
            if parent in q:
                q[child] = multiplier * q[parent] + offset
        pelvis = self.np.eye(4)
        pelvis[:3, 3] = REFERENCE_PELVIS
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


def solve_hand(model: UrdfKinematics, side: str, target_z: float, reference: dict[str, float], rng: Any, least_squares: Any) -> dict[str, Any]:
    # Reuse the same domain layout as the simulator; importing this helper does
    # not import Isaac/USD or start a simulator.
    from vskill_os.adapters.isaac.p1_scene import keyboard_key_center, standard_keyboard_layout

    np = model.np
    centers = {
        spec.name: keyboard_key_center(spec, operator_row_x=0.45, left_origin_y=0.145, top_z=target_z)
        for spec in standard_keyboard_layout()
    }
    targets = np.array([centers[key] for key in HOME_KEYS[side]])
    targets[0, 1] += 0.035 if side == "left" else -0.035
    names = model.joint_names(side)
    lower = np.array([model.limits[name][0] + 0.0001 for name in names])
    upper = np.array([model.limits[name][1] - 0.0001 for name in names])
    reference_q = np.clip(np.array([reference[name] for name in names]), lower, upper)
    collision_floor = target_z - 0.026

    def residual(q: Any) -> Any:
        tips, minima, _ = model.measure(side, dict(zip(names, q)))
        clearance = np.array([max(0.0, collision_floor - z) for z in minima.values()])
        return np.r_[(tips - targets).ravel(), clearance * 1.5, 0.00005 * (q - reference_q)]

    best = None
    for attempt in range(3):
        initial = reference_q if attempt == 0 else np.clip(best.x + rng.normal(0.0, 0.12, len(names)), lower, upper)
        solution = least_squares(
            residual, initial, bounds=(lower, upper), max_nfev=250,
            ftol=1e-9, xtol=1e-9, gtol=1e-9,
        )
        if best is None or np.linalg.norm(solution.fun) < np.linalg.norm(best.fun):
            best = solution
        positions = dict(zip(names, best.x.tolist()))
        tips, minimum_z, palm = model.measure(side, positions)
        errors = np.linalg.norm(tips - targets, axis=1)
        if float(errors.max()) < 0.0003 and min(minimum_z.values()) >= collision_floor - 1e-6:
            break
    if float(errors.max()) >= 0.0003 or min(minimum_z.values()) < collision_floor - 1e-6:
        raise RuntimeError(f"offline IK failed: {side}, z={target_z}, max error={errors.max():.6g}")
    print(f"{side} z={target_z:.3f}: max tip error={errors.max():.3g} m, collider floor={min(minimum_z.values()):.6f} m", flush=True)
    return {
        "joint_positions": positions, "targets": targets.tolist(), "tips": tips.tolist(),
        "errors": errors.tolist(), "min_collider_z": min(minimum_z.values()),
        "lowest_body": min(minimum_z, key=minimum_z.get), "palm_position": palm.tolist(),
    }


def interpolate_check(model: UrdfKinematics, source: dict[str, dict[str, float]], target: dict[str, dict[str, float]], required_floor: float) -> dict[str, Any]:
    minimum = float("inf")
    for alpha in model.np.linspace(0.0, 1.0, 101):
        for side in SIDES:
            positions = {name: (1.0 - alpha) * value + alpha * target[side][name] for name, value in source[side].items()}
            _, minima, _ = model.measure(side, positions)
            minimum = min(minimum, *minima.values())
    if minimum < required_floor - 1e-6:
        raise RuntimeError(f"joint interpolation violates collision floor {required_floor:.4f}: {minimum:.6f}")
    return {"linear_joint_samples": 101, "minimum_hand_and_wrist_aabb_z_m": minimum}


def main() -> int:
    args = parse_args()
    try:
        import numpy as np
        import scipy
        from scipy.optimize import least_squares
        from scipy.spatial.transform import Rotation
    except ImportError as exc:
        raise SystemExit("NumPy/SciPy are optional calibration dependencies; run with the Isaac Lab Python environment.") from exc
    sys.path.insert(0, str(REPO_ROOT / "src"))
    model = UrdfKinematics(args.urdf, np, Rotation)
    rng = np.random.default_rng(args.seed)
    safe = {side: model.safe_seating(side) for side in SIDES}
    home = {side: solve_hand(model, side, 0.731, safe[side], rng, least_squares) for side in SIDES}
    raised = {side: solve_hand(model, side, 0.820, home[side]["joint_positions"], rng, least_squares) for side in SIDES}
    raised_q = {side: raised[side]["joint_positions"] for side in SIDES}
    home_q = {side: home[side]["joint_positions"] for side in SIDES}
    checks = {
        "safe_to_raised": interpolate_check(model, safe, raised_q, 0.794),
        "raised_to_home": interpolate_check(model, raised_q, home_q, 0.705),
    }
    result = {
        "schema_version": 1,
        "status": "offline_kinematic_seeds_not_dynamic_acceptance",
        "method": "SciPy bounded least_squares, URDF forward kinematics with actual approximate mimic ratios; all hand/wrist collision AABB floor constraints",
        "urdf_sha256": hashlib.sha256(args.urdf.read_bytes()).hexdigest(),
        "calibration": {"seed": args.seed, "numpy_version": np.__version__, "scipy_version": scipy.__version__, "tool": "tools/calibrate_dex5_postures.py"},
        "collision_mesh_sha256": model.mesh_hashes,
        "reference_pelvis_position_m": list(REFERENCE_PELVIS),
        "reference_pelvis_quaternion_wxyz": [1, 0, 0, 0],
        "fingers": list(FINGERS), "tip_local_offsets_m": TIP_OFFSETS,
        "assumptions": [
            "exact approximate mounts and coupling from pinned composite URDF",
            "zero waist angles", "target joint states reached without actuator sag",
            "collision checks conservative hand/wrist transformed local AABBs only; no whole-body/self-collision proof",
            "spherical simulation contact pads are not part of the URDF collision AABBs",
            "actual pelvis height and PD/contact error require measured DLS refinement",
            "never bypass runtime safety/contact/event verification",
        ],
        "safe_seating": {"joint_positions": {name: value for positions in safe.values() for name, value in positions.items()}},
        "raised": {"target_tip_z_m": 0.820, "hands": raised},
        "home": {"target_tip_z_m": 0.731, "hands": home},
        "joint_interpolation_checks": checks,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote offline calibration candidate: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
