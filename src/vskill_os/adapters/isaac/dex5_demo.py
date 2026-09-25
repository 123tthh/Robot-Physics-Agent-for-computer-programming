"""Isaac Sim 5.1 assisted seating and physical Dex5-1 typing experiment.

All joint commands live in this optional adapter. The artificial pelvis lift,
approximate hand mounting/coupling, and contact pads are explicit experimental
fixtures, not a claim of an independently balancing real robot.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, replace
import hashlib
import json
import math
import os
from pathlib import Path
import random
import subprocess
import time
import traceback
from typing import Any

from vskill_os.simulation.dex5_typing import (
    DEFAULT_DEMO_TEXT, Dex5Contact, Dex5Observation, Dex5Phase,
    Dex5SafetyGuard, Dex5TypingPlanner, Dex5TypingVerifier,
)
from vskill_os.simulation.keyboard_bridge import KeycapSample, KeyboardEventBridge
from vskill_os.simulation.dex5_preflight import (
    validate_dex5_source_preflight, validate_dex5_runtime_joint_names,
    validate_dex5_mimic_stage,
)


ROOT = Path(__file__).resolve().parents[4]
FINGERS = ("thumb", "index", "middle", "ring", "little")
EXECUTORS = tuple(f"{side}.{finger}" for side in ("left", "right") for finger in FINGERS)
PAD_RADIUS = 0.006
TIP_OFFSETS = {finger: ((0.055, 0.008, 0.0) if finger == "thumb" else (0.026, 0.006, 0.0)) for finger in FINGERS}
HOME_KEYS = dict(zip(EXECUTORS, ("space", "f", "d", "s", "a", "space", "j", "k", "l", ";")))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--urdf", type=Path, default=ROOT / "artifacts/isaac-dex5/assets/g1_mode15_dex5.urdf")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts/dex5/run")
    parser.add_argument("--text", default=DEFAULT_DEMO_TEXT)
    parser.add_argument("--headless", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--rebuild-assets", action="store_true")
    parser.add_argument("--inspect-only", action="store_true")
    parser.add_argument("--start-seated", action="store_true", help="diagnostic initialized seated state; no sit-down trajectory is claimed")
    parser.add_argument("--no-video", action="store_true")
    parser.add_argument("--phase-timeout", type=float, default=12.0)
    return parser.parse_args()


class PhysicalTypingAdapter:
    def __init__(self, *, robot, keyboard, sensors, net_sensor, cameras, sim, geometry, torch, quat_apply, output, video):
        self.robot, self.keyboard, self.sensors = robot, keyboard, sensors
        self.net_sensor, self.cameras, self.sim = net_sensor, cameras, sim
        self.geometry, self.torch, self.quat_apply = geometry, torch, quat_apply
        self.output, self.video = output, video
        self.dt = 1.0 / 240.0
        self.frame = 0
        self.targets = robot.data.default_joint_pos.clone()
        self.support_trace = []
        self.bridge = KeyboardEventBridge()
        self.events: list[Any] = []
        self.event_cursor = 0
        self.observations = []
        self.phase_context = None
        self.active_stroke = None
        self.text = ""
        self.phase_label = "ASSISTED SEATING"
        self.writer = None
        self.guard = Dex5SafetyGuard()
        self.tip_ids = {e: robot.find_bodies([e.replace('.', '_hand_') + '_3_link'], preserve_order=True)[0][0] for e in EXECUTORS}
        self.key_names = tuple(geometry["key_centers"])
        self.key_ids = {key: keyboard.find_joints([geometry["key_joint_names"][key]], preserve_order=True)[0][0] for key in self.key_names}
        self._travels = {key: 0.0 for key in self.key_names}
        self.net_ids = {key: net_sensor.body_names.index(geometry["key_paths"][key].rsplit('/', 1)[-1]) for key in self.key_names}
        self.pelvis_id = robot.find_bodies(["pelvis"], preserve_order=True)[0][0]
        self.torso_id = robot.find_bodies(["torso_link"], preserve_order=True)[0][0]
        self.arm_ids = {}
        self.mimic_pairs = {}
        for side in ("left", "right"):
            names = [f"{side}_{name}_joint" for name in ("shoulder_pitch", "shoulder_roll", "shoulder_yaw", "elbow", "wrist_roll", "wrist_pitch", "wrist_yaw")]
            names += [f"{side}_hand_{finger}_{i}_joint" for finger in FINGERS for i in range(4 if finger == "thumb" else 3)]
            self.arm_ids[side] = robot.find_joints(names, preserve_order=True)[0]
            self.mimic_pairs[side] = [(robot.find_joints([f"{side}_hand_{finger}_2_joint"])[0][0], robot.find_joints([f"{side}_hand_{finger}_3_joint"])[0][0]) for finger in FINGERS if finger != "thumb"]
        self.goals: dict[str, Any] = {}
        self.maximum_force = 0.0
        self.press_feedforward_n = 0.0
        self.trace = (output / "physics.jsonl").open("w", encoding="utf-8")

    def point(self, executor):
        body = self.tip_ids[executor]
        local = self.torch.tensor(TIP_OFFSETS[executor.split('.')[1]], device=self.robot.device)
        offset = self.quat_apply(self.robot.data.body_quat_w[0, body], local)
        return self.robot.data.body_pos_w[0, body] + offset, offset

    def travel(self, key):
        return self._travels[key]

    def point_jacobian(self, executor, all_jacobians=None):
        torch = self.torch
        _, offset = self.point(executor)
        if all_jacobians is None:
            all_jacobians = self.robot.root_physx_view.get_jacobians()
        body_id = self.tip_ids[executor] - (1 if self.robot.is_fixed_base else 0)
        full = all_jacobians[0,body_id]
        rx,ry,rz = offset.unbind()
        zero = torch.zeros((),device=self.robot.device)
        skew = torch.stack((torch.stack((zero,-rz,ry)),torch.stack((rz,zero,-rx)),torch.stack((-ry,rx,zero))))
        return full[:3]-skew@full[3:]

    def contacts(self):
        result = []
        matrix = self.torch.stack([s.data.force_matrix_w[0, 0] for s in self.sensors.values()])
        all_forces = self.torch.linalg.vector_norm(matrix, dim=-1).tolist()
        for executor, forces in zip(self.sensors, all_forces):
            side, finger = executor.split('.')
            result.extend(Dex5Contact(key, side, finger, force) for key, force in zip(self.key_names, forces) if force > 0.01)
        return tuple(result)

    def step(self):
        self.robot.set_joint_position_target(self.targets)
        # Model-based gravity feedforward is confined to the low-level adapter.
        gravity = self.robot.root_physx_view.get_gravity_compensation_forces().clone()
        if self.phase_context is not None and self.phase_context[1] == Dex5Phase.PRESS and self.active_stroke is not None:
            # Bounded, slowly ramped Cartesian force through articulated joints.
            # It never applies force to a key or commands keyboard displacement.
            self.press_feedforward_n = min(1.2,self.press_feedforward_n+2.*self.dt)
            jac = self.point_jacobian(self.active_stroke.executor)
            point,_ = self.point(self.active_stroke.executor)
            velocity = jac@self.robot.data.joint_vel[0]
            force = self.torch.zeros(3,device=self.robot.device)
            # A tangential impedance holds the center of the key while the
            # normal load ramps; a position-only measured-state target loses
            # several millimeters laterally under finger contact load.
            force[:2] = self.torch.clamp(500.*(self.goals[self.active_stroke.executor][:2]-point[:2])-10.*velocity[:2],-2.,2.)
            force[2] = -self.press_feedforward_n
            gravity[0] += jac.T@force
        else:
            self.press_feedforward_n = 0.
        for pairs in self.mimic_pairs.values():
            for parent, child in pairs:
                gravity[0,parent] += (1.3963 / 1.684242699)*gravity[0,child]
                gravity[0,child] = 0.
        self.robot.set_joint_effort_target(gravity)
        self.robot.write_data_to_sim()
        # Keys receive only a zero-rest spring, never a commanded depression.
        self.keyboard.set_joint_position_target(self.keyboard.data.default_joint_pos * 0)
        self.keyboard.write_data_to_sim()
        render = self.video and self.frame % 16 == 0
        self.sim.step(render=render)
        self.robot.update(self.dt)
        self.keyboard.update(self.dt)
        positions = self.keyboard.data.joint_pos[0].tolist()
        self._travels = {key: max(0.0, -positions[index]) for key, index in self.key_ids.items()}
        for sensor in (*self.sensors.values(), self.net_sensor):
            sensor.update(self.dt, force_recompute=True)
        self.frame += 1
        now = self.frame * self.dt
        previous_event_count = len(self.events)
        forces = self.torch.linalg.vector_norm(self.net_sensor.data.net_forces_w[0], dim=-1).tolist()
        self.maximum_force = max(self.maximum_force, max(forces))
        for key in self.key_names:
            for event in self.bridge.process(KeycapSample(key, self.travel(key), forces[self.net_ids[key]] > 0.01, now, source="isaac_physx_all_keycaps"), now=now):
                self.events.append(event)
                if event.event_type.value == "keydown":
                    if len(key) == 1:
                        self.text += key
                    elif key == "space":
                        self.text += " "
                    elif key == "enter":
                        self.text += "\n"
                    elif key == "backspace":
                        self.text = self.text[:-1]
        context = self.phase_context or (-1, Dex5Phase.SEATED)
        sample = self.observe(*context, consume_events=False)
        failure = self.guard.check(sample)
        if failure is not None:
            if self.phase_context is not None:
                self.record(*self.phase_context)
            raise RuntimeError(f"{failure.value}: independent per-step safety guard")
        if self.phase_context is not None and self.active_stroke is not None:
            stroke = self.active_stroke
            unexpected = [c for c in sample.contacts if c.key != stroke.key or c.executor != stroke.executor]
            stray_travel = [k for k,v in sample.key_travel_m if k != stroke.key and v > self.bridge.config.release_threshold_m]
            if unexpected or stray_travel:
                self.record(*self.phase_context)
                raise RuntimeError(f"WRONG_KEY_OR_FINGER: unintended contact/travel {unexpected!r} {stray_travel!r}")
        if self.phase_context is not None and len(self.events) != previous_event_count:
            self.record(*self.phase_context)
        if self.frame % 16 == 1:
            self.trace.write(json.dumps(asdict(sample)) + "\n")
            support_ids = self.robot.find_joints(["seating_fixture_.*_joint"])[0]
            self.support_trace.append({"time_s":now,"pelvis_xyz_m":self.robot.data.body_pos_w[0,self.pelvis_id].tolist(),
                "fixture_position_m":self.robot.data.joint_pos[0,support_ids].tolist(),"fixture_target_m":self.targets[0,support_ids].tolist()})
            if self.video:
                self.capture()
        if max(forces) > 12.0:
            raise RuntimeError("EXCESS_FORCE: keycap contact exceeded 12 N")
        if not bool(self.torch.isfinite(self.robot.data.joint_pos).all()):
            raise RuntimeError("INVALID_EVIDENCE: nonfinite articulation state")

    def capture(self, filename=None):
        from PIL import Image, ImageDraw
        if filename:
            self.sim.render()
        images = []
        for camera in self.cameras:
            camera.update(self.dt * 16, force_recompute=True)
            array = camera.data.output["rgb"][0, ..., :3].detach().cpu().numpy()
            images.append(Image.fromarray(array.astype('uint8')))
        canvas = Image.new("RGB", (1280, 560), (14, 20, 28))
        for i, frame in enumerate(images):
            canvas.paste(frame.resize((640, 480)), (i * 640, 0))
        draw = ImageDraw.Draw(canvas)
        draw.text((20, 491), f"G1 + DEX5-1 | ISAAC SIM 5.1 | {self.phase_label}", fill="white")
        draw.text((20, 514), f"Event display: {self.text!r}    Simulation: {self.frame*self.dt:.2f}s", fill=(100, 220, 255))
        draw.text((20, 537), "Assisted pelvis fixture / approximate mounts & coupling / contact pads / PhysX oracle", fill=(255, 205, 100))
        if filename:
            canvas.save(self.output / filename)
        if self.video:
            if self.writer is None:
                self.writer = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pixel_format", "rgb24", "-video_size", "1280x560", "-framerate", "15", "-i", "-", "-an", "-c:v", "libx264", "-preset", "fast", "-crf", "22", "-pix_fmt", "yuv420p", str(self.output / "demo.mp4")], stdin=subprocess.PIPE)
            self.writer.stdin.write(canvas.tobytes())

    def observe(self, index, phase, consume_events=True):
        events = tuple((e.event_type.value, e.key) for e in self.events[self.event_cursor:])
        if consume_events:
            self.event_cursor = len(self.events)
        # Clearance is measured from the lowest point of each spherical pad to
        # the resting key surface. All 10 fingers must lift before another key.
        top = next(iter(self.geometry["key_centers"].values()))[2]
        ids = [self.tip_ids[e] for e in EXECUTORS]
        offsets = self.torch.tensor([TIP_OFFSETS[e.split('.')[1]] for e in EXECUTORS], device=self.robot.device)
        points = self.robot.data.body_pos_w[0,ids] + self.quat_apply(self.robot.data.body_quat_w[0,ids], offsets)
        clearances = tuple((e, z - PAD_RADIUS - top) for e,z in zip(EXECUTORS,points[:,2].tolist()))
        q = self.robot.data.body_quat_w[0, self.torso_id]
        up = self.quat_apply(q, self.torch.tensor([0., 0., 1.], device=self.robot.device))
        tilt = math.acos(max(-1., min(1., float(up[2]))))
        return Dex5Observation(self.frame*self.dt, index, phase,
            tuple((k, self.travel(k)) for k in self.key_names), self.contacts(), events,
            self.text, self.bridge.keys_down, clearances, 0.0,
            float(self.robot.data.body_pos_w[0, self.pelvis_id, 2]), tilt,
            bool(self.robot.is_fixed_base), "isaac_articulation_contact", "isaac_virtual_keyboard_display")

    def record(self, index, phase):
        if self.observations and self.observations[-1].time_s == self.frame*self.dt:
            return
        self.observations.append(self.observe(index, phase))

    def seated_transition(self):
        initial = self.targets.clone()
        final = initial.clone()
        poses = {"seating_fixture_lift_joint": -0.23, "seating_fixture_slide_joint": -0.25}
        for side in ("left", "right"):
            poses.update({f"{side}_hip_pitch_joint": -1.25, f"{side}_knee_joint": 1.6, f"{side}_ankle_pitch_joint": -0.35,
                          f"{side}_shoulder_pitch_joint": -1.45, f"{side}_shoulder_roll_joint": 0.25 if side == "left" else -0.25,
                          f"{side}_elbow_joint": 1.15})
        for name, value in poses.items():
            ids, _ = self.robot.find_joints([name], preserve_order=True)
            if len(ids) != 1:
                raise RuntimeError(f"missing required fixture/body joint {name}")
            final[0, ids[0]] = value
        fixture_ids = self.robot.find_joints(["seating_fixture_.*_joint"])[0]
        for i in range(1200):
            t = min(1., (i + 1) / 360.)
            alpha = t*t*(3. - 2.*t)
            self.targets = initial + alpha * (final - initial)
            support_t = max(0., min(1., (i - 360) / 720.))
            support_alpha = support_t*support_t*(3. - 2.*support_t)
            self.targets[0, fixture_ids] = initial[0,fixture_ids] + support_alpha*(final[0,fixture_ids]-initial[0,fixture_ids])
            self.step()
        self.capture("01-seated.png")
        measured = float(self.robot.data.body_pos_w[0,self.pelvis_id,2])
        if not .52 <= measured <= .60:
            raise RuntimeError(f"SEATED_PRECONDITION: pelvis measured {measured:.4f} m, expected 0.56 +/- 0.04 m")

    def set_home_goals(self, height=.045):
        for executor in EXECUTORS:
            x, y, z = self.geometry["key_centers"][HOME_KEYS[executor]]
            if executor.endswith("thumb"):
                y += 0.035 if executor.startswith("left") else -0.035
            self.goals[executor] = self.torch.tensor([x, y, z + PAD_RADIUS + height], device=self.robot.device)

    def follow_posture(self, positions, duration_s=2.0):
        """Bounded joint reference used only inside the physical adapter."""
        initial = self.targets.clone()
        final = initial.clone()
        for name,value in positions.items():
            ids,_ = self.robot.find_joints([name],preserve_order=True)
            if len(ids) != 1:
                raise RuntimeError(f"posture references unknown joint {name}")
            final[0,ids[0]] = value
        steps = int(duration_s/self.dt)
        for i in range(steps):
            t = (i+1)/steps
            self.targets = initial + (t*t*(3-2*t))*(final-initial)
            self.step()

    def move(self, side, steps, tolerance=0.003, maximum_delta=0.018):
        torch = self.torch
        executors = [e for e in EXECUTORS if e.startswith(side)]
        ids = self.arm_ids[side]
        base_offset = 0 if self.robot.is_fixed_base else 6
        for _ in range(steps):
            errors, jacobians = [], []
            all_jacobians = self.robot.root_physx_view.get_jacobians()
            for executor in executors:
                point, _ = self.point(executor)
                errors.append(self.goals[executor] - point)
                jac = self.point_jacobian(executor,all_jacobians)
                selected = jac[:, [i + base_offset for i in ids]].clone()
                for parent, child in self.mimic_pairs[side]:
                    selected[:, ids.index(parent)] += (1.3963 / 1.684242699) * jac[:, child + base_offset]
                jacobians.append(selected)
            error = torch.cat(errors)
            norm = max(float(torch.linalg.vector_norm(e)) for e in errors)
            if norm < tolerance:
                self.step()
                return True, norm
            jac = torch.cat(jacobians)
            # Keep lateral alignment tight during compliant vertical contact;
            # a few millimeters of sideways drift can brush the adjacent row.
            weights = torch.tensor([4.,4.,1.]*5, device=self.robot.device)
            error = error*weights
            jac = jac*weights[:,None]
            delta = jac.T @ torch.linalg.solve(jac @ jac.T + torch.eye(15, device=self.robot.device)*0.025**2, error*0.45)
            delta *= min(1., maximum_delta / max(1.e-9, float(delta.abs().max())))
            measured = self.robot.data.joint_pos[0, ids]
            # Gravity is compensated explicitly. A measured-state DLS target
            # avoids accumulating a large position lead while joints respond.
            desired = measured + delta
            limits = self.robot.data.joint_pos_limits[0, ids]
            self.targets[0, ids] = torch.minimum(torch.maximum(desired, limits[:,0]+0.001), limits[:,1]-0.001)
            self.step()
            if (self.phase_context is not None and self.phase_context[1] == Dex5Phase.PRESS
                    and self.active_stroke is not None and self.active_stroke.key in self.bridge.keys_down):
                return True, norm
        return False, norm

    def release(self):
        self.phase_label = "RELEASE / SAFE STOP"
        index = self.active_stroke.index if self.active_stroke is not None else -1
        self.phase_context = (index,Dex5Phase.RELEASE)
        self.active_stroke = None
        self.press_feedforward_n = 0.
        if self.goals:
            for e in EXECUTORS:
                self.goals[e][2] = max(float(self.goals[e][2]), .74)
            for side in ("left", "right"):
                self.move(side, 180, tolerance=.008)

    def close(self):
        self.trace.close()
        if self.writer is not None:
            self.writer.stdin.close()
            self.writer.wait(timeout=30)
            if self.writer.returncode:
                raise RuntimeError("video encoder failed")


def main() -> int:
    args = parse_args()
    plan = replace(Dex5TypingPlanner().plan(args.text, phase_timeout_s=args.phase_timeout), maximum_setup_duration_s=30.)
    if not args.urdf.is_file():
        raise FileNotFoundError(f"Dex5 assets not prepared: {args.urdf}; run make dex5-assets first")
    source_preflight = validate_dex5_source_preflight(urdf=args.urdf,
        postures=ROOT/'configs/simulation/dex5_postures.json',
        manifest=ROOT/'configs/simulation/g1_29dof_5010_dex5_assets.json',
        asset_root=ROOT/'artifacts/simulation-assets/unitree_ros_7d6075f7',tip_offsets=TIP_OFFSETS)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    random.seed(args.seed)
    from isaaclab.app import AppLauncher
    app = AppLauncher(headless=args.headless, enable_cameras=True).app
    report = {"schema_version": 1, "status": "failed", "seed": args.seed, "plan": plan.to_dict(),
              "capability": "isaac_sim_5_1", "dynamic_sitting_verified": False,
              "assumptions": ["actuated horizontal/vertical pelvis support fixture", "fixed chair", "approximate G1-Dex5 mount", "approximate distal coupling", "distal mesh collisions replaced by rigidly attached 6 mm spherical pads", "model-based gravity feedforward; total joint torque not independently verified", "PhysX oracle; no camera OCR"],
              "urdf_sha256": _sha(args.urdf), "physics_dt_s": 1/240, "observations": []}
    report['asset_preflight'] = source_preflight
    report["implementation_sha256"] = {p.name:_sha(p) for p in (Path(__file__), ROOT/'src/vskill_os/adapters/isaac/dex5_scene_geometry.py', ROOT/'src/vskill_os/simulation/dex5_typing.py', ROOT/'configs/simulation/g1_29dof_5010_dex5_assets.json',ROOT/'configs/simulation/dex5_postures.json')}
    adapter = None
    try:
        import torch
        from pxr import UsdPhysics
        import isaaclab.sim as sim_utils
        from isaaclab.actuators import ImplicitActuatorCfg
        from isaaclab.assets import Articulation, ArticulationCfg
        from isaaclab.sensors import Camera, CameraCfg, ContactSensor, ContactSensorCfg
        from isaaclab.sim.converters import UrdfConverter, UrdfConverterCfg
        from isaaclab.utils.math import quat_apply
        from .dex5_scene_geometry import author_dex5_workstation
        torch.manual_seed(args.seed)
        version_path = Path(os.environ.get("ISAAC_PATH", "")) / "VERSION"
        version = version_path.read_text().strip() if version_path.is_file() else "unknown"
        if not version.startswith("5.1.0"):
            raise RuntimeError(f"requires Isaac Sim 5.1.0, found {version}")
        report["runtime"] = {"isaac_sim": version, "torch": torch.__version__, "gpu": torch.cuda.get_device_name(0), "device": args.device}
        usd_dir = ROOT / "artifacts/dex5/assets/usd"
        converter = UrdfConverter(UrdfConverterCfg(asset_path=str(args.urdf.resolve()), usd_dir=str(usd_dir), usd_file_name="g1_dex5_seating.usd",
            fix_base=True, merge_fixed_joints=False, make_instanceable=False, force_usd_conversion=args.rebuild_assets,
            # IsaacLab 2.3.2 passes this field directly to set_parse_mimic;
            # despite its name, True preserves PhysX mimic in Isaac Sim 5.1.
            convert_mimic_joints_to_normal_joints=True,
            joint_drive=UrdfConverterCfg.JointDriveCfg(gains=UrdfConverterCfg.JointDriveCfg.PDGainsCfg(stiffness=80., damping=4.))))
        report["usd_sha256"] = _sha(Path(converter.usd_path))
        report["usd_bundle_sha256"] = {str(p.relative_to(usd_dir)):_sha(p) for p in sorted(usd_dir.rglob('*')) if p.is_file() and p.suffix in {'.usd','.usda','.usdc'}}
        sim = sim_utils.SimulationContext(sim_utils.SimulationCfg(dt=1/240, device=args.device, render_interval=16))
        stage = sim_utils.get_current_stage()
        geometry = author_dex5_workstation(stage=stage, sim_utils=sim_utils)
        report["geometry"] = geometry["config"]
        report['press_feedforward'] = {'maximum_n':1.2,'ramp_n_per_s':2.,'lateral_stiffness_n_per_m':500.,'lateral_damping_ns_per_m':10.,'lateral_axis_force_limit_n':2.,'application':'fingertip Jacobian transpose to robot joint effort only'}
        cfg = ArticulationCfg(prim_path="/World/G1", spawn=sim_utils.UsdFileCfg(usd_path=converter.usd_path, activate_contact_sensors=True,
            rigid_props=sim_utils.RigidBodyPropertiesCfg(disable_gravity=False), articulation_props=sim_utils.ArticulationRootPropertiesCfg(enabled_self_collisions=False, solver_position_iteration_count=16, solver_velocity_iteration_count=4)),
            init_state=ArticulationCfg.InitialStateCfg(pos=(.25,0.,.79)), actuators={
                "body": ImplicitActuatorCfg(joint_names_expr=["(left|right)_(hip|knee|ankle|shoulder|elbow|wrist).*", "waist.*"], stiffness=180., damping=12., effort_limit_sim=None),
                "hands": ImplicitActuatorCfg(joint_names_expr=[".*_hand_thumb_.*", ".*_hand_(index|middle|ring|little)_[012]_joint"], stiffness=10., damping=.3, effort_limit_sim=None),
                "fixture": ImplicitActuatorCfg(joint_names_expr=["seating_fixture_.*_joint"], stiffness=25000., damping=1500., effort_limit_sim=2000.)})
        postures = json.loads((ROOT/'configs/simulation/dex5_postures.json').read_text())
        if args.start_seated:
            joints = {".*":0.,"seating_fixture_slide_joint":-.25,"seating_fixture_lift_joint":-.23}
            for side in ('left','right'):
                joints.update({f'{side}_hip_pitch_joint':-1.25,f'{side}_knee_joint':1.6,f'{side}_ankle_pitch_joint':-.35})
                joints.update(postures['home']['hands'][side]['joint_positions'])
                for finger in FINGERS[1:]:
                    joints[f'{side}_hand_{finger}_3_joint'] = joints[f'{side}_hand_{finger}_2_joint']*(1.3963/1.684242699)
            # Exact names avoid regex overlap in IsaacLab joint initialization.
            joints.pop('.*')
            cfg.init_state.joint_pos = joints
        report['seating_transition_executed'] = not args.start_seated
        robot = Articulation(cfg)
        mimic_prims = [p for p in stage.Traverse() if any('Mimic' in api for api in p.GetAppliedSchemas())]
        if len(mimic_prims) != 8:
            raise RuntimeError(f"Dex5 coupling import failed: expected 8 PhysX mimic joints, got {len(mimic_prims)}")
        for prim in mimic_prims:
            for schema in prim.GetAppliedSchemas():
                if schema.startswith('PhysxMimicJointAPI:'):
                    prefix = 'physxMimicJoint:'+schema.split(':',1)[1]
                    # PhysX defines zero frequency/damping as a hard coupling.
                    # This matches the explicitly approximate URDF ratio used
                    # by offline FK and the reduced-coordinate Jacobian.
                    prim.GetAttribute(prefix+':naturalFrequency').Set(0.)
                    prim.GetAttribute(prefix+':dampingRatio').Set(0.)
        report['mimic_compliance'] = {'natural_frequency':0.,'damping_ratio':0.,'model':'hard_approximate_ratio'}
        report["mimic_joint_count"] = len(mimic_prims)
        report['mimic_preflight'] = validate_dex5_mimic_stage(stage,urdf=args.urdf,require_hard_coupling=True)
        # Contact proxies are rigid children of real Dex5 distal bodies. Their
        # positions are documented local offsets, never independently animated.
        for executor in EXECUTORS:
            name = executor.replace('.', '_hand_') + '_3_link'
            bodies = [p for p in stage.Traverse() if p.GetName() == name and str(p.GetPath()).startswith('/World/G1/') and p.HasAPI(UsdPhysics.RigidBodyAPI)]
            if len(bodies) != 1:
                raise RuntimeError(f"expected one Dex5 distal rigid body for {executor}, got {len(bodies)}")
            body_path = str(bodies[0].GetPath())
            # Dedicated fingertip proxy model: retain the OEM inertial/visual
            # and articulated link, but replace its mesh collision surface.
            from pxr import Usd
            native_collisions = bodies[0].GetChild('collisions')
            if not native_collisions.IsValid():
                raise RuntimeError(f"missing expected native collision subtree for {executor}")
            # URDF mesh collision subtrees are USD instances even when the
            # articulation isn't instanceable. Ordinary PrimRange skips their
            # proxies, so disable the subtree explicitly, then audit proxies.
            native_collisions.SetActive(False)
            pad_cfg = sim_utils.SphereCfg(radius=PAD_RADIUS, collision_props=sim_utils.CollisionPropertiesCfg(contact_offset=.0003, rest_offset=0.), visual_material=sim_utils.PreviewSurfaceCfg(diffuse_color=(.1,.7,.9)))
            pad_cfg.func(body_path + '/TypingPad', pad_cfg, translation=TIP_OFFSETS[executor.split('.')[1]])
            active_shapes = [str(p.GetPath()) for p in Usd.PrimRange(bodies[0],Usd.TraverseInstanceProxies())
                if p.HasAPI(UsdPhysics.CollisionAPI) and UsdPhysics.CollisionAPI(p).GetCollisionEnabledAttr().Get() is not False]
            if active_shapes != [body_path+'/TypingPad/geometry/mesh']:
                raise RuntimeError(f"fingertip collision proxy audit failed for {executor}: {active_shapes}")
        report['distal_collision_model'] = 'audited_single_sphere_per_distal_link'
        keyboard = Articulation(geometry["keyboard_cfg"])
        sensors = {}
        for executor in EXECUTORS:
            name = executor.replace('.', '_hand_') + '_3_link'
            prim = next(p for p in stage.Traverse() if p.GetName() == name and p.HasAPI(UsdPhysics.RigidBodyAPI))
            sensors[executor] = ContactSensor(ContactSensorCfg(prim_path=str(prim.GetPath()), filter_prim_paths_expr=list(geometry["key_paths"].values()), update_period=0., history_length=1))
        net_sensor = ContactSensor(ContactSensorCfg(prim_path="/World/Keyboard/Keys/.*", update_period=0., history_length=1))
        cameras = [Camera(CameraCfg(prim_path=f"/World/Camera{i}", height=480, width=640, data_types=["rgb"], spawn=sim_utils.PinholeCameraCfg(focal_length=24., clipping_range=(.02,20.)))) for i in range(2)]
        sim.reset()
        for camera, eye, look in zip(cameras, ((1.65,-1.65,1.40),(.75,-.40,1.10)), ((.18,0.,.7),(.45,0.,.70))):
            camera.set_world_poses_from_view(torch.tensor([eye], device=args.device), torch.tensor([look], device=args.device))
        sim.set_camera_view((1.65,-1.65,1.40),(.18,0.,.7))
        for _ in range(5):
            sim.step()
            robot.update(1/240)
            keyboard.update(1/240)
            for sensor in (*sensors.values(),net_sensor): sensor.update(1/240, force_recompute=True)
        report["robot"] = {"joints": robot.joint_names, "bodies": robot.body_names, "fixed_base": bool(robot.is_fixed_base), "generalized_joint_count": robot.num_joints}
        report['joint_preflight'] = validate_dex5_runtime_joint_names(robot.joint_names)
        stage.GetRootLayer().Export(str((args.output_dir / "scene.usda").resolve()))
        adapter = PhysicalTypingAdapter(robot=robot, keyboard=keyboard, sensors=sensors, net_sensor=net_sensor, cameras=cameras, sim=sim, geometry=geometry, torch=torch, quat_apply=quat_apply, output=args.output_dir, video=not args.no_video)
        adapter.capture("00-standing.png")
        if args.start_seated:
            for _ in range(120): adapter.step()
        else:
            adapter.seated_transition()
        adapter.phase_label = "BIMANUAL PREPOSE"
        if postures['urdf_sha256'] != report['urdf_sha256']:
            raise RuntimeError("calibrated postures do not match the composed URDF")
        for name in (() if args.start_seated else ('raised','home')):
            positions = {joint:value for side in ('left','right') for joint,value in postures[name]['hands'][side]['joint_positions'].items()}
            adapter.follow_posture(positions)
        report['seed_state'] = {'joint_positions':dict(zip(robot.joint_names,robot.data.joint_pos[0].tolist())),
            'tip_positions':{e:adapter.point(e)[0].tolist() for e in EXECUTORS}}
        report['seed_mimic_error_rad'] = {robot.joint_names[child]:float(robot.data.joint_pos[0,child]-(1.3963/1.684242699)*robot.data.joint_pos[0,parent])
            for pairs in adapter.mimic_pairs.values() for parent,child in pairs}
        if max(abs(v) for v in report['seed_mimic_error_rad'].values()) > .02:
            raise RuntimeError('MIMIC_CONSTRAINT_FAILED: measured coupling residual exceeded 0.02 rad')
        adapter.set_home_goals()
        reach = {}
        for side in ("left", "right"):
            reach[side] = adapter.move(side, 1800, tolerance=.006)
        report["prepose"] = reach
        adapter.capture("02-prepose.png")
        if args.inspect_only:
            report["status"] = "inspected"
            report["tip_positions"] = {e: adapter.point(e)[0].tolist() for e in EXECUTORS}
        else:
            if not all(value[0] for value in reach.values()):
                raise RuntimeError("UNREACHABLE: one or more fingertips did not reach prepose")
            if adapter.frame*adapter.dt > plan.maximum_setup_duration_s:
                raise RuntimeError("TIMEOUT: seating and prepose exceeded setup horizon")
            adapter.record(-1, Dex5Phase.SEATED)
            for stroke in plan.strokes:
                adapter.active_stroke = stroke
                side = stroke.hand.value
                x,y,z = geometry["key_centers"][stroke.key]
                if stroke.key == "space": y += .035 if side == "left" else -.035
                adapter.goals[stroke.executor] = torch.tensor([x,y,z+PAD_RADIUS+.015], device=args.device)
                adapter.phase_label = f"{stroke.executor.upper()} / {stroke.key.upper()} / HOVER"
                adapter.phase_context = (stroke.index, Dex5Phase.HOVER)
                ok,error = adapter.move(side, int(args.phase_timeout*240), tolerance=.003)
                if not ok: raise RuntimeError(f"UNREACHABLE {stroke.executor}: {error:.4f}m")
                adapter.record(stroke.index,Dex5Phase.HOVER)
                adapter.phase_label = f"{stroke.executor.upper()} / {stroke.key.upper()} / PRESS"
                adapter.phase_context = (stroke.index, Dex5Phase.PRESS)
                adapter.goals[stroke.executor][2] = z + PAD_RADIUS - .0032
                for _ in range(int(args.phase_timeout*240)//8):
                    adapter.move(side,8,tolerance=.0003,maximum_delta=.004)
                    if stroke.key in adapter.bridge.keys_down: break
                    # Bounded contact admittance compensates PD deflection;
                    # travel is measured from the passive key, never commanded.
                    if adapter.travel(stroke.key) < adapter.bridge.config.press_threshold_m:
                        adapter.goals[stroke.executor][2] = max(z+PAD_RADIUS-.020,
                            float(adapter.goals[stroke.executor][2])-.0005)
                adapter.record(stroke.index,Dex5Phase.PRESS)
                adapter.capture(f"key-{stroke.index:02d}-press.png")
                adapter.phase_label = f"{stroke.executor.upper()} / {stroke.key.upper()} / RELEASE"
                adapter.phase_context = (stroke.index, Dex5Phase.RELEASE)
                adapter.goals[stroke.executor][2] = z + PAD_RADIUS + .045
                adapter.move(side, max(1,int(args.phase_timeout*240)-24), tolerance=.004)
                for _ in range(24): adapter.step()
                adapter.record(stroke.index,Dex5Phase.RELEASE)
                # Full verifier runs below; do not silently continue missed keys.
                if adapter.text != stroke.expected_prefix or adapter.bridge.keys_down:
                    raise RuntimeError(f"KEY_CYCLE_FAILED at {stroke.executor}: observed {adapter.text!r}")
            verdict = Dex5TypingVerifier().verify(plan, adapter.observations, capability="isaac_sim_5_1")
            report["verification"] = verdict.to_dict()
            report["status"] = "passed" if verdict.succeeded else "failed"
            adapter.capture("03-finished.png")
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        traceback.print_exc()
        if adapter is not None:
            report["failure_time_s"] = adapter.frame*adapter.dt
            report["failure_tip_positions"] = {e:adapter.point(e)[0].tolist() for e in EXECUTORS}
            report["failure_goals"] = {e:g.tolist() for e,g in adapter.goals.items()}
            try: adapter.release()
            except Exception as recovery: report["recovery_error"] = str(recovery)
    finally:
        if adapter is not None:
            report["observations"] = [asdict(o) for o in adapter.observations]
            report["support_trajectory"] = adapter.support_trace
            report["physical_duration_s"] = adapter.frame*adapter.dt
            report["last_tip_positions"] = {e:adapter.point(e)[0].tolist() for e in EXECUTORS}
            report["events"] = [asdict(e) for e in adapter.events]
            report["observed_text"] = adapter.text
            report["max_keycap_force_n"] = adapter.maximum_force
            report["keys_down_at_end"] = list(adapter.bridge.keys_down)
            report['last_joint_positions'] = dict(zip(robot.joint_names,robot.data.joint_pos[0].tolist()))
            try: adapter.capture("last-frame.png"); adapter.close()
            except Exception as exc: report["recording_error"] = str(exc); report["status"] = "failed"
        (args.output_dir / "report.json").write_text(json.dumps(report,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
        print(json.dumps({"status":report["status"],"error":report.get("error"),"report":str(args.output_dir / 'report.json')},ensure_ascii=False),flush=True)
        app.close(wait_for_replicator=False, skip_cleanup=True)
    return 0 if report["status"] in {"passed","inspected"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
