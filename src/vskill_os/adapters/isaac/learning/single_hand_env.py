"""Single-instance G1/Dex5 direct-joint environment for a passive physical key.

Start Isaac AppLauncher before constructing this optional adapter. The scene
uses an explicit fixed pelvis and collision-pad approximation. It
does not import IK code, old calibration poses or demonstration trajectories.
"""

from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
import math
import os
from pathlib import Path
import xml.etree.ElementTree as ET
from uuid import uuid4

from ....learning.contracts import (
    EpisodeRecord, JointRecord, KeyBinding, KeyEventRecord, ObservationFrame,
    PhysicalKeyRecord, RecordStamp, RunManifest, SCHEMA_VERSION, TaskSpec, TextObservation,
)
from ....learning.environment import (
    ActionRejected, CleanupRecord, EpisodeMetadata, JointTargetAction, JointTargetGuard,
    PolicyObservation, ResetSpec, StepResult,
)
from ....learning.physical import AppliedAction, CoordinateLimit, PhysicsSample, PhysicsSafetyMonitor, PhysicsTrace
from ....simulation.keyboard_bridge import KeyboardEventBridge, KeycapConfig, KeycapSample
from ....simulation.dex5_preflight import validate_dex5_mimic_stage
from ....simulation.dex5_assets import EXPECTED_BODY_JOINTS, EXPECTED_DEX5_JOINTS, validate_g1_dex5_urdf
from ..dex5_scene_geometry import author_dex5_workstation
from .config import FINGERS, SingleHandConfig, control_spec, direct_joint_initialization, prepare_fixed_pelvis_asset
from .provenance import COLLECTOR_FILES, RECORDING_FILES

ROOT = Path(__file__).resolve().parents[5]
DEFAULT_URDF = ROOT / "artifacts/verification_feedback/assets/g1_dex5_fixed_pelvis.urdf"


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


class SingleHandKeyboardEnv:
    def __init__(self, *, output_dir: Path, config: SingleHandConfig | None = None,
                 urdf: Path | None = None, device: str = "cuda:0", bridge_config: KeycapConfig | None = None) -> None:
        self.config = config or SingleHandConfig()
        self.urdf = (urdf or prepare_fixed_pelvis_asset(DEFAULT_URDF)).resolve()
        if validate_g1_dex5_urdf(self.urdf).fixture_joint_count != 0:
            raise ValueError("single-hand v1 requires the fixed-pelvis asset without movable support joints")
        self.output = output_dir.resolve()
        self.output.mkdir(parents=True, exist_ok=True)
        self.control_spec = control_spec(self.urdf, self.config)
        self.guard = JointTargetGuard(self.control_spec)
        self.initial_positions = direct_joint_initialization(self.urdf)
        self.bridge_config = bridge_config or KeycapConfig()
        self.env_id = "isaac-single-hand-0"
        self.running = False
        self.closed = False
        self.reset_spec = None
        self.sealed = False
        self.source_digests = {name: file_sha256(ROOT / name) for name in COLLECTOR_FILES}
        self._construct_scene(device)

    def _construct_scene(self, device: str) -> None:
        import torch
        import isaaclab.sim as sim_utils
        from isaaclab.actuators import ImplicitActuatorCfg
        from isaaclab.assets import Articulation, ArticulationCfg
        from isaaclab.sensors import ContactSensor, ContactSensorCfg
        from isaaclab.sim.converters import UrdfConverter, UrdfConverterCfg
        from isaaclab.utils.math import quat_apply
        from pxr import PhysxSchema, Usd, UsdPhysics

        self.torch, self.quat_apply = torch, quat_apply
        version = (Path(os.environ["ISAAC_PATH"]) / "VERSION").read_text().strip()
        if not version.startswith("5.1.0"):
            raise RuntimeError(f"VF-01-T02 requires Isaac Sim 5.1.0, found {version}")
        if sim_utils.SimulationContext.instance() is not None:
            raise RuntimeError("only one single-hand environment may own this simulation context")
        self.runtime = {"isaac_sim": version, "torch": torch.__version__, "device": device,
                        "gpu": torch.cuda.get_device_name(0), "num_envs": 1}
        converter = UrdfConverter(UrdfConverterCfg(
            asset_path=str(self.urdf), usd_dir=str(ROOT / "artifacts/verification_feedback/assets/usd"),
            usd_file_name="g1_dex5_direct_joint.usd", fix_base=True, merge_fixed_joints=False,
            make_instanceable=False, convert_mimic_joints_to_normal_joints=True,
            joint_drive=UrdfConverterCfg.JointDriveCfg(
                gains=UrdfConverterCfg.JointDriveCfg.PDGainsCfg(stiffness=80.0, damping=4.0)),
        ))
        self.usd_dir = Path(converter.usd_path).parent
        self.sim = sim_utils.SimulationContext(sim_utils.SimulationCfg(
            dt=self.config.physics_dt_s, device=device, render_interval=16,
        ))
        stage = sim_utils.get_current_stage()
        self.geometry = author_dex5_workstation(stage=stage, sim_utils=sim_utils, config=self.config.geometry())
        robot_cfg = ArticulationCfg(
            prim_path="/World/G1", spawn=sim_utils.UsdFileCfg(
                usd_path=converter.usd_path, activate_contact_sensors=True,
                rigid_props=sim_utils.RigidBodyPropertiesCfg(disable_gravity=False),
                articulation_props=sim_utils.ArticulationRootPropertiesCfg(
                    enabled_self_collisions=False, solver_position_iteration_count=16, solver_velocity_iteration_count=4)),
            init_state=ArticulationCfg.InitialStateCfg(pos=(0.0, 0.0, 0.625), joint_pos=self.initial_positions),
            actuators={
                "fixed_body": ImplicitActuatorCfg(
                    joint_names_expr=["(left|right)_(hip|knee|ankle|shoulder|elbow|wrist).*", "waist.*"],
                    stiffness=400.0, damping=25.0, effort_limit_sim=None),
                "hands": ImplicitActuatorCfg(joint_names_expr=[".*_hand_thumb_.*", ".*_hand_(index|middle|ring|little)_[012]_joint"],
                                             stiffness=10.0, damping=0.3, effort_limit_sim=None),
            },
        )
        self.robot = Articulation(robot_cfg)
        # The URDF importer instances collision subtrees even with a non-
        # instanceable top-level asset. De-instance those subtrees before
        # applying physics properties; otherwise Isaac only emits a warning
        # and silently leaves the imported contact settings unchanged.
        for prim in list(stage.Traverse()):
            if str(prim.GetPath()).startswith("/World/G1/") and prim.GetName() == "collisions" and prim.IsInstance():
                prim.SetInstanceable(False)
        sim_utils.modify_collision_properties("/World/G1", sim_utils.CollisionPropertiesCfg(
            contact_offset=self.config.body_contact_offset_m, rest_offset=0.0))
        for prim in stage.Traverse():
            for schema in prim.GetAppliedSchemas():
                if schema.startswith("PhysxMimicJointAPI:"):
                    prefix = "physxMimicJoint:" + schema.split(":", 1)[1]
                    prim.GetAttribute(prefix + ":naturalFrequency").Set(0.0)
                    prim.GetAttribute(prefix + ":dampingRatio").Set(0.0)
        self.mimic_audit = validate_dex5_mimic_stage(stage, urdf=self.urdf, require_hard_coupling=True)
        for side in ("left", "right"):
            for finger in FINGERS:
                name = f"{side}_hand_{finger}_3_link"
                bodies = [prim for prim in stage.Traverse() if prim.GetName() == name and prim.HasAPI(UsdPhysics.RigidBodyAPI)]
                if len(bodies) != 1:
                    raise RuntimeError(f"ambiguous fingertip body: {name}")
                body = bodies[0]
                collisions = body.GetChild("collisions")
                if not collisions.IsValid():
                    raise RuntimeError(f"missing fingertip collision subtree: {name}")
                collisions.SetActive(False)
                pad = sim_utils.SphereCfg(
                    radius=self.config.pad_radius_m,
                    collision_props=sim_utils.CollisionPropertiesCfg(contact_offset=0.0003, rest_offset=0.0),
                    visual_material=sim_utils.PreviewSurfaceCfg(diffuse_color=(0.1, 0.7, 0.9)),
                )
                pad.func(str(body.GetPath()) + "/LearningPad", pad,
                         translation=(0.055, 0.008, 0.0) if finger == "thumb" else (0.026, 0.006, 0.0))
                active = [str(prim.GetPath()) for prim in Usd.PrimRange(body, Usd.TraverseInstanceProxies())
                          if prim.HasAPI(UsdPhysics.CollisionAPI)
                          and UsdPhysics.CollisionAPI(prim).GetCollisionEnabledAttr().Get() is not False]
                if active != [str(body.GetPath()) + "/LearningPad/geometry/mesh"]:
                    raise RuntimeError(f"unexpected distal collision model: {active}")
        body_colliders = [prim for prim in stage.Traverse()
                          if str(prim.GetPath()).startswith("/World/G1/") and "/LearningPad/" not in str(prim.GetPath())
                          and prim.HasAPI(UsdPhysics.CollisionAPI)]
        if len(body_colliders) < 29 or any(
            not math.isclose(PhysxSchema.PhysxCollisionAPI(prim).GetContactOffsetAttr().Get(),
                             self.config.body_contact_offset_m, abs_tol=1e-9)
            or PhysxSchema.PhysxCollisionAPI(prim).GetRestOffsetAttr().Get() != 0.0 for prim in body_colliders
        ):
            raise RuntimeError("body collision configuration did not reach every active collider")
        self.body_collision_audit = {"active_collider_count": len(body_colliders),
                                      "contact_offset_m": self.config.body_contact_offset_m, "rest_offset_m": 0.0}
        # The authored fixed root is offset from /World/Keyboard. An all-zero
        # init_state would move the whole keyboard to the origin on reset.
        self.geometry["keyboard_cfg"].init_state.pos = self.geometry["keyboard_root_position"]
        self.keyboard = Articulation(self.geometry["keyboard_cfg"])
        self.contacts = ContactSensor(ContactSensorCfg(prim_path="/World/Keyboard/Keys/.*", update_period=0.0, history_length=1))
        self.sim.reset()
        expected = set(EXPECTED_BODY_JOINTS) | set(EXPECTED_DEX5_JOINTS)
        if set(self.robot.joint_names) != expected or len(self.robot.joint_names) != len(expected) or not self.robot.is_fixed_base:
            raise RuntimeError("fixed-pelvis runtime joint set differs from the pinned 69-coordinate asset")
        self.joint_audit = {"generalized_joint_count": len(expected), "fixed_pelvis": True,
                            "body_joint_count": 29, "dex5_coordinate_count": 40, "dex5_active_joint_count": 32}
        self.ids = self.robot.find_joints(list(self.control_spec.joint_names), preserve_order=True)[0]
        if tuple(self.robot.joint_names[index] for index in self.ids) != self.control_spec.joint_names:
            raise RuntimeError("JOINT_MAPPING: runtime order differs from the 23-dimensional contract")
        self.key_names = tuple(self.geometry["key_paths"])
        self.key_ids = [self.keyboard.find_joints([self.geometry["key_joint_names"][key]], preserve_order=True)[0][0]
                        for key in self.key_names]
        self.contact_ids = [self.contacts.body_names.index(self.geometry["key_paths"][key].rsplit("/", 1)[-1])
                            for key in self.key_names]
        self.key_body_ids = [self.keyboard.body_names.index(self.geometry["key_paths"][key].rsplit("/", 1)[-1])
                             for key in self.key_names]
        self.tip_id = self.robot.find_bodies(["left_hand_index_3_link"], preserve_order=True)[0][0]
        self.pelvis_id = self.robot.find_bodies(["pelvis"], preserve_order=True)[0][0]
        self.torso_id = self.robot.find_bodies(["torso_link"], preserve_order=True)[0][0]
        joints = {joint.get("name"): joint for joint in ET.parse(self.urdf).getroot().findall("joint")}
        self.coordinate_limits = tuple(CoordinateLimit(
            name, "m" if joints[name].get("type") == "prismatic" else "rad",
            float(joints[name].find("limit").get("lower")), float(joints[name].find("limit").get("upper")),
            min(float(joints[name].find("limit").get("velocity")), self.config.maximum_joint_speed_rad_s),
        ) for name in self.robot.joint_names)
        self.mimic_pairs = []
        for name, joint in joints.items():
            mimic = joint.find("mimic")
            if mimic is not None:
                self.mimic_pairs.append((self.robot.joint_names.index(mimic.get("joint")),
                                         self.robot.joint_names.index(name), float(mimic.get("multiplier", "1"))))
        self.safety = PhysicsSafetyMonitor(
            self.coordinate_limits, maximum_force_n=self.config.maximum_contact_force_n,
            support_origin_xyz_m=(0.0, 0.0, 0.625), support_tolerance_m=self.config.support_tolerance_m,
        )
        self.sim.set_camera_view((1.5, -1.3, 1.4), (0.35, 0.0, 0.65))
        stage.GetRootLayer().Export(str(self.output / "scene.usda"))

    def _stamp(self, stream: str, sequence: int) -> RecordStamp:
        now = self.tick * self.config.physics_dt_s
        return RecordStamp(f"{self.reset_spec.episode_id}:{stream}:{sequence}", self.env_id,
                           self.reset_spec.episode_id, sequence, "isaac_sim_seconds", now, now, "isaac." + stream)

    def _advance(self) -> None:
        self.robot.set_joint_position_target(self.targets)
        gravity = self.robot.root_physx_view.get_gravity_compensation_forces().clone()
        for parent, child, ratio in self.mimic_pairs:
            gravity[0, parent] += ratio * gravity[0, child]
            gravity[0, child] = 0.0
        self.robot.set_joint_effort_target(gravity)
        self.robot.write_data_to_sim()
        self.keyboard.set_joint_position_target(self.keyboard.data.default_joint_pos * 0.0)
        self.keyboard.write_data_to_sim()
        self.sim.step(render=False)
        self.robot.update(self.config.physics_dt_s)
        self.keyboard.update(self.config.physics_dt_s)
        self.contacts.update(self.config.physics_dt_s, force_recompute=True)

    def _sample(self) -> PhysicsSample:
        offset = self.quat_apply(self.robot.data.body_quat_w[0, self.tip_id],
                                 self.torch.tensor((0.026, 0.006, 0.0), device=self.robot.device))
        return PhysicsSample(
            self._stamp("physx", self.tick), tuple(self.robot.data.joint_pos[0].tolist()),
            tuple(self.robot.data.joint_vel[0].tolist()), tuple(self.targets[0].tolist()),
            tuple(self.keyboard.data.joint_pos[0, self.key_ids].tolist()),
            tuple(self.keyboard.data.joint_pos_target[0, self.key_ids].tolist()),
            tuple(self.contacts.data.net_forces_w[0, self.contact_ids].flatten().tolist()),
            tuple(self.keyboard.data.body_pos_w[0, self.key_body_ids].flatten().tolist()),
            tuple((self.robot.data.body_pos_w[0, self.tip_id] + offset).tolist()),
            tuple(self.robot.data.body_pos_w[0, self.pelvis_id].tolist()),
            tuple(self.robot.data.body_quat_w[0, self.torso_id].tolist()),
        )

    def reset(self, spec: ResetSpec) -> tuple[PolicyObservation, EpisodeMetadata]:
        if self.closed or self.running:
            raise RuntimeError("finish or terminate the current episode before reset; closed environments cannot reset")
        if spec.target_key != "a" or abs(spec.maximum_time_s / self.config.physics_dt_s - round(spec.maximum_time_s / self.config.physics_dt_s)) > 1e-6:
            raise ValueError("v1 supports one physical key 'a' and horizons divisible by the physics period")
        if self.reset_spec is not None and not self.sealed:
            self.save_episode()
        if self.reset_spec is not None:
            self._cleanup_bridge("episode_reset")
        self.episode_dir = self.output / spec.episode_id
        self.episode_dir.mkdir(exist_ok=False)
        self.reset_spec = spec
        self.owner_token = uuid4().hex
        self.torch.manual_seed(spec.seed)
        self.bridge = KeyboardEventBridge(self.bridge_config)
        self.tick = 0
        self.action_sequence = 0
        self.previous_delta = (0.0,) * len(self.ids)
        self.events, self.observations, self.raw_samples, self.actions = [], [], [], []
        self.text = ""
        self.texts = [TextObservation(self._stamp("text_oracle", 0), "", None, "snapshot")]
        self.failures = []
        self.termination = "finish_requested"
        self.sealed = False
        self.cleanup_record = None
        self.running = True
        self.task = TaskSpec(
            SCHEMA_VERSION, "vf01-physical-single-a", "a", "", (KeyBinding("a", "a"),), "backspace",
            self.key_names, self.control_spec.joint_names, "left", "fixed_pelvis_seated_pd",
            ("joint_state", "key_travel", "key_contact"), spec.maximum_time_s, 1, spec.maximum_time_s,
        )
        self.targets = self.robot.data.default_joint_pos.clone()
        for name, value in self.initial_positions.items():
            self.targets[0, self.robot.joint_names.index(name)] = value
        try:
            # State writes are confined to reset, before recorded control begins.
            for asset, q in ((self.robot, self.targets), (self.keyboard, self.keyboard.data.default_joint_pos * 0.0)):
                root = asset.data.default_root_state.clone()
                asset.write_root_pose_to_sim(root[:, :7])
                asset.write_root_velocity_to_sim(root[:, 7:])
                asset.write_joint_state_to_sim(q, self.torch.zeros_like(q))
                asset.reset()
            self.contacts.reset()
            for _ in range(self.config.settling_steps):
                self._advance()
                codes = self.safety.check(self._sample())
                if codes:
                    self.failures.extend(codes)
                    self._record()
                    raise RuntimeError(f"unsafe reset settling step: {codes}")
            self._record()
            if self.failures:
                raise RuntimeError(f"unsafe reset state: {self.failures}")
        except Exception as exc:
            self.failures.append("RESET_FAILED")
            self.running = False
            self.termination = "safety_stop"
            self.save_episode()
            write_json(self.episode_dir / "reset_failure.json", {
                "status": "reset_failed", "physical_primitive_passed": False,
                "error": str(exc), "failure_codes": self.failures,
                "raw_samples_available": len(self.raw_samples),
            })
            raise
        metadata = EpisodeMetadata(self.env_id, spec.episode_id, spec.seed, self.owner_token, self.control_spec,
                                   "isaac_physx_single_hand_v1", self.task.support_mode)
        return self._observation(), metadata

    def _record(self) -> None:
        raw = self._sample()
        self.raw_samples.append(raw)
        self.failures.extend(code for code in self.safety.check(raw) if code not in self.failures)
        keys = tuple(PhysicalKeyRecord(
            key, max(0.0, -raw.key_joint_positions_m[index]),
            math.sqrt(sum(v*v for v in raw.key_force_vectors_n[index*3:index*3+3])) > 0.01,
            abs(raw.key_force_vectors_n[index*3 + 2]),
        ) for index, key in enumerate(self.key_names))
        frame = ObservationFrame(raw.stamp, keys, tuple(JointRecord(
            name, raw.joint_positions[index], raw.joint_velocities[index], raw.joint_targets[index],
        ) for name, index in zip(self.control_spec.joint_names, self.ids, strict=True)), bool(self.failures))
        self.observations.append(frame)
        for key in keys:
            for event in self.bridge.process(KeycapSample(
                key.key, key.travel_m, key.in_contact, raw.stamp.capture_time_s,
                "isaac.physx", f"{raw.stamp.record_id}:{key.key}"), now=raw.stamp.capture_time_s,
            ):
                recorded = KeyEventRecord(self._stamp("keyboard_bridge", len(self.events)), event.key,
                                          event.event_type.value, event.reason, raw.stamp.record_id)
                self.events.append(recorded)
                if event.event_type.value == "keydown":
                    if event.key == "backspace":
                        self.text = self.text[:-1]
                    elif event.key == "space":
                        self.text += " "
                    elif len(event.key) == 1:
                        self.text += event.key
                    self.texts.append(TextObservation(self._stamp("text_oracle", len(self.texts)), self.text,
                                                      recorded.stamp.record_id, "applied"))
        # Keep a raw append-only checkpoint even if a later physics/API call fails.
        with (self.episode_dir / "physics.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(raw.to_dict(), allow_nan=False) + "\n")

    def _observation(self) -> PolicyObservation:
        frame = self.observations[-1]
        return PolicyObservation(self.env_id, self.reset_spec.episode_id, frame.stamp.capture_time_s,
                                 self.reset_spec.target_key, frame.joints, frame.keys, self.previous_delta)

    def step(self, action: JointTargetAction) -> StepResult:
        if not self.running or self.closed:
            raise RuntimeError("step requires an active episode")
        previous = tuple(self.targets[0, self.ids].tolist())
        before = len(self.observations)
        try:
            applied = self.guard.apply(previous, action, time_s=self.tick * self.config.physics_dt_s,
                                       owner_token=self.owner_token, expected_sequence=self.action_sequence)
        except ActionRejected as exc:
            self.actions.append(AppliedAction(self.tick * self.config.physics_dt_s, action, previous, previous, exc.code))
            self.failures.append(exc.code)
            self.running = False
            self.termination = "safety_stop"
            self.save_episode()
            return StepResult(self._observation(), (), True, False, tuple(self.failures), ())
        self.actions.append(AppliedAction(self.tick * self.config.physics_dt_s, action, previous, applied, None))
        self.targets[0, self.ids] = self.torch.tensor(applied, device=self.robot.device)
        self.previous_delta = tuple(after - old for after, old in zip(applied, previous, strict=True))
        self.action_sequence += 1
        for _ in range(self.config.decimation):
            try:
                self._advance()
                self.tick += 1
                self._record()
            except Exception:
                self.failures.append("PHYSICS_BACKEND_ERROR")
                self.running = False
                self.termination = "safety_stop"
                self.save_episode()
                raise
            if self.failures:
                self.running = False
                self.termination = "safety_stop"
                break
            if self.tick * self.config.physics_dt_s + 1e-9 >= self.reset_spec.maximum_time_s:
                self.running = False
                self.termination = "time_limit"
                break
        if not self.running:
            self.save_episode()
        return StepResult(self._observation(), (), self.termination == "safety_stop", self.termination == "time_limit",
                          tuple(self.failures), tuple(frame.stamp.record_id for frame in self.observations[before:]))

    def finish(self) -> None:
        if self.closed or self.reset_spec is None:
            raise RuntimeError("finish requires an initialized environment")
        if self.running and self.tick == 0:
            raise RuntimeError("an episode without a physical step cannot request successful completion")
        self.running = False
        self.save_episode()

    def save_episode(self) -> Path:
        if self.running:
            raise RuntimeError("terminal evidence must be saved after finish or termination")
        if self.sealed:
            return self.episode_dir
        if not self.observations:
            write_json(self.episode_dir / "reset_failure.json", {
                "status": "invalid_initial_physics", "physical_primitive_passed": False,
                "failure_codes": self.failures, "raw_samples_available": len(self.raw_samples),
            })
            self.sealed = True
            return self.episode_dir
        texts = (*self.texts, TextObservation(self._stamp("text_oracle", len(self.texts)), self.text, None, "snapshot"))
        sources = self.source_digests
        if sources != {name: file_sha256(ROOT / name) for name in COLLECTOR_FILES}:
            self.failures.append("COLLECTOR_SOURCE_CHANGED")
            self.termination = "safety_stop"
        collector_hash = hashlib.sha256(json.dumps(sources, sort_keys=True).encode()).hexdigest()
        episode = EpisodeRecord(
            SCHEMA_VERSION, self.env_id, self.reset_spec.episode_id, "isaac_sim_seconds", self.task.sha256(),
            RunManifest(self.reset_spec.episode_id, "isaac_sim_5_1", "isaac_physx_single_hand_v1", "working_tree",
                        collector_hash, self.reset_spec.seed, "hand_authored_direct_joint_v1; no IK or old posture data"),
            0.0, self.observations[-1].stamp.capture_time_s, self.termination, tuple(self.observations), tuple(self.events),
            texts, (), (),
        )
        trace = PhysicsTrace(
            "vf-physical/1", "isaac_physx_single_hand_v1", self.config.physics_dt_s, self.control_spec, self.owner_token,
            self.coordinate_limits, self.key_names, tuple(self.initial_positions[name] for name in self.robot.joint_names),
            (0.0, 0.0, 0.625), self.config.maximum_contact_force_n, self.config.support_tolerance_m,
            tuple(self.raw_samples), tuple(self.actions), tuple(self.failures), episode.sha256(),
        )
        self.task.write(self.episode_dir / "task.json")
        episode.write(self.episode_dir / "episode.json")
        trace.write(self.episode_dir / "trace.json")
        self.config.write(self.episode_dir / "config.json")
        self.reset_spec.write(self.episode_dir / "reset.json")
        files = {name: file_sha256(self.episode_dir / name) for name in RECORDING_FILES}
        write_json(self.episode_dir / "manifest.json", {
            "schema_version": "vf-physics-bundle/1", "runtime": self.runtime, "sources": sources,
            "urdf": {"path": str(self.urdf.relative_to(ROOT)), "sha256": file_sha256(self.urdf)},
            "usd_bundle": {str(path.relative_to(ROOT)): file_sha256(path) for path in sorted(self.usd_dir.rglob("*.usd"))},
            "geometry": self.geometry["config"], "joint_audit": self.joint_audit, "mimic_audit": self.mimic_audit,
            "body_collision_audit": self.body_collision_audit,
            "scene_sha256": file_sha256(self.output / "scene.usda"),
            "fixed_pelvis_xyz_m": [0.0, 0.0, 0.625],
            "initialization": {"source": "hand_authored_direct_joint_v1", "positions": self.initial_positions,
                               "settling_steps": self.config.settling_steps},
            "bridge_config": asdict(self.bridge_config), "files": files,
            "assumptions": ["fixed pelvis; frozen seated body PD", "single environment", "approximate mounts and distal coupling",
                            "6 mm distal collision pads", "joint PD plus gravity feedforward",
                            "PhysX contact and text-state oracle; no camera, desktop or learned policy"],
        })
        self.sealed = True
        return self.episode_dir

    def close(self) -> CleanupRecord:
        if self.closed and self.reset_spec is not None:
            return self.cleanup_record
        if self.reset_spec is None:
            self.closed = True
            return CleanupRecord(self.env_id, "uninitialized", ())
        if self.running:
            self.running = False
            self.termination = "cancelled"
        self.save_episode()
        cleanup = self._cleanup_bridge("worker_shutdown")
        self.closed = True
        return cleanup

    def _cleanup_bridge(self, reason: str) -> CleanupRecord:
        if self.cleanup_record is None:
            events = self.bridge.all_keys_up(monotonic_time=self.tick * self.config.physics_dt_s, reason=reason)
            self.cleanup_record = CleanupRecord(self.env_id, self.reset_spec.episode_id, tuple(event.event_id for event in events))
            write_json(self.episode_dir / "cleanup.json", {
                "cleanup": self.cleanup_record.to_dict(), "reason": reason,
                "events": [asdict(event) for event in events],
            })
        return self.cleanup_record
