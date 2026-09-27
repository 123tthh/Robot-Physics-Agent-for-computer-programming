"""Offline FK proximity audit for T04 planning, not a physical reachability gate.

Sweep the existing single enabled joint from hand-authored initialization. No
IK solver, calibrated posture, optimizer, trajectory or robot command is used.
"""

import argparse
from dataclasses import asdict
import math
from pathlib import Path

from .batch_recording import sha
from .config import SingleHandConfig, direct_joint_initialization
from .single_hand_env import DEFAULT_URDF, ROOT, write_json
from ..p1_scene import keyboard_key_center, standard_keyboard_layout


def audit(urdf, config, *, samples=301):
    import numpy as np
    from scipy.spatial.transform import Rotation
    from ..dex5_kinematics import UrdfKinematics

    if len(config.enabled_joints) != 1 or samples < 2:
        raise ValueError('this audit only samples the existing single-joint baseline')
    fk = UrdfKinematics(urdf, np, Rotation)
    fk.base_transform[:3, 3] = (0., 0., .625)
    initial = direct_joint_initialization(urdf)
    joint = config.enabled_joints[0]
    lower, upper = fk.limits[joint]
    centers = []
    # The offset is the actual LearningPad offset, not the old IK tip offset.
    for angle in np.linspace(lower, upper, samples):
        world = fk.forward(dict(initial, **{joint: float(angle)}))
        pad = world['left_hand_index_3_link'] @ np.array([.026, .006, 0., 1.])
        centers.append(pad[:3])
    centers = np.array(centers)
    geometry = config.geometry()
    layout = {key.name: key for key in standard_keyboard_layout()}

    def center(key):
        return keyboard_key_center(key, operator_row_x=geometry.keyboard_operator_row_x_m,
            left_origin_y=geometry.keyboard_left_origin_y_m, top_z=geometry.key_center_z_m,
            key_pitch=geometry.key_pitch_m)

    rows = []
    for name in ('a', 's', 'd', 'f', 'backspace'):
        key = layout[name]
        xyz = center(key)
        half = np.array([geometry.key_pitch_m-geometry.key_gap_m,
            geometry.key_pitch_m*key.width_units-geometry.key_gap_m, geometry.key_height_m])/2
        # Euclidean distance to the resting keycap box, minus sphere radius.
        gaps = np.linalg.norm(np.maximum(np.abs(centers-np.array(xyz))-half, 0.), axis=1)-config.pad_radius_m
        rows.append(dict(key=name, key_center_xyz_m=xyz, distance_from_a_m=math.dist(xyz, center(layout['a'])),
            sampled_minimum_pad_box_gap_m=float(gaps.min()),
            sampled_geometric_overlap=bool((gaps <= 0).any()), physical_reachability_verified=False))
    sources = ('src/vskill_os/adapters/isaac/learning/few_key_preflight.py',
        'src/vskill_os/adapters/isaac/learning/config.py',
        'src/vskill_os/adapters/isaac/learning/single_hand_env.py',
        'src/vskill_os/adapters/isaac/dex5_kinematics.py',
        'src/vskill_os/adapters/isaac/dex5_scene_geometry.py', 'src/vskill_os/adapters/isaac/p1_scene.py')
    return dict(version='vf-few-key-preflight/1', task='VF-02-T04', stage='offline_geometry_only',
        accepted=False, physical_reachability_verified=False, ik_used=False, policy_trained=False,
        urdf_sha256=sha(urdf), source_hashes={name: sha(ROOT/name) for name in sources},
        physical_config=asdict(config), initialization='hand_authored_direct_joint_v1',
        sampled_joint=joint, sampled_range_rad=[lower, upper], samples=samples,
        pad_local_xyz_m=[.026, .006, 0.], pad_radius_m=config.pad_radius_m,
        pad_world_min_xyz_m=centers.min(axis=0).tolist(), pad_world_max_xyz_m=centers.max(axis=0).tolist(),
        candidates=rows,
        limitations=['discrete FK samples are not proof of reachability or unreachability',
            'no dynamics, key travel, self/table collision, joint tracking or physical release evaluated',
            'resting keycap boxes and the left index pad only; other contacts are not checked',
            'no candidate key is approved for training or runtime use by this audit'])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--urdf', type=Path, default=DEFAULT_URDF)
    parser.add_argument('--config', type=Path, default=ROOT/'configs/learning/verification_feedback/single_hand_physics_v1.json')
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args(argv)
    report = audit(args.urdf, SingleHandConfig.read(args.config))
    write_json(args.report, report)
    print(f"Offline geometry audit written to {args.report}; physical gate remains unverified.")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
