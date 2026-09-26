"""One PhysX scene containing independently filtered G1/Dex5 workstations.

Start AppLauncher first. T02 source files and asset conversion are unchanged;
this version owns a separate USD conversion and records world-space raw arrays.
"""

from dataclasses import asdict
import math
import os
from pathlib import Path
import xml.etree.ElementTree as ET

from ....learning.batch import BatchConfig
from ....learning.physical import CoordinateLimit
from ....simulation.dex5_assets import EXPECTED_BODY_JOINTS, EXPECTED_DEX5_JOINTS
from ....simulation.dex5_preflight import validate_dex5_mimic_stage
from ..dex5_scene_geometry import author_dex5_workstation
from .config import FINGERS, SingleHandConfig, control_spec, direct_joint_initialization, prepare_fixed_pelvis_asset
from .single_hand_env import DEFAULT_URDF, ROOT


class BatchPhysicsScene:
    def __init__(self, directory: Path, batch: BatchConfig, physical: SingleHandConfig):
        import torch
        import isaaclab.sim as sim_utils
        from isaaclab.actuators import ImplicitActuatorCfg
        from isaaclab.assets import Articulation, ArticulationCfg
        from isaaclab.sensors import ContactSensor, ContactSensorCfg
        from isaaclab.sim.converters import UrdfConverter, UrdfConverterCfg
        from isaaclab.utils.math import quat_apply
        from isaacsim.core.cloner import GridCloner
        from isaacsim.core.utils.prims import move_prim
        from pxr import PhysxSchema, Usd, UsdPhysics

        self.torch, self.quat_apply, self.batch, self.config = torch, quat_apply, batch, physical
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        version = (Path(os.environ["ISAAC_PATH"]) / "VERSION").read_text().strip()
        if not version.startswith("5.1.0") or sim_utils.SimulationContext.instance() is not None:
            raise RuntimeError("batch adapter requires sole ownership of an Isaac 5.1 simulation context")
        self.urdf = prepare_fixed_pelvis_asset(DEFAULT_URDF)
        self.control = control_spec(self.urdf, physical)
        self.initial_positions = direct_joint_initialization(self.urdf)
        converter = UrdfConverter(UrdfConverterCfg(asset_path=str(self.urdf),
            usd_dir=str(ROOT / "artifacts/verification_feedback/assets/batch_usd"), usd_file_name="g1_dex5_batch.usd",
            fix_base=True, merge_fixed_joints=False, make_instanceable=False, convert_mimic_joints_to_normal_joints=True,
            joint_drive=UrdfConverterCfg.JointDriveCfg(gains=UrdfConverterCfg.JointDriveCfg.PDGainsCfg(stiffness=80., damping=4.))))
        buffers = {k: v for k, v in asdict(batch).items() if k.startswith("gpu_")}
        self.sim = sim_utils.SimulationContext(sim_utils.SimulationCfg(dt=physical.physics_dt_s, device="cuda:0",
            render_interval=16, physx=sim_utils.PhysxCfg(**buffers)))
        stage = sim_utils.get_current_stage()
        geometry = author_dex5_workstation(stage=stage, sim_utils=sim_utils, config=physical.geometry())
        cloner = GridCloner(spacing=batch.spacing_m, stage=stage)
        cloner.define_base_env("/World/envs")
        self.paths = cloner.generate_paths("/World/envs/env", batch.num_envs)
        stage.DefinePrim(self.paths[0], "Xform")
        for name in ("Workstation", "Chair", "Keyboard"):
            move_prim(f"/World/{name}", f"{self.paths[0]}/{name}")
        robot_path = f"{self.paths[0]}/G1"
        spawn = sim_utils.UsdFileCfg(usd_path=converter.usd_path, activate_contact_sensors=True,
            rigid_props=sim_utils.RigidBodyPropertiesCfg(disable_gravity=False),
            articulation_props=sim_utils.ArticulationRootPropertiesCfg(enabled_self_collisions=False,
                solver_position_iteration_count=16, solver_velocity_iteration_count=4))
        spawn.func(robot_path, spawn, translation=(0., 0., .625))
        for prim in list(stage.Traverse()):
            if str(prim.GetPath()).startswith(robot_path+"/") and prim.GetName() == "collisions" and prim.IsInstance():
                prim.SetInstanceable(False)
        sim_utils.modify_collision_properties(robot_path, sim_utils.CollisionPropertiesCfg(
            contact_offset=physical.body_contact_offset_m, rest_offset=0.))
        for prim in stage.Traverse():
            for schema in prim.GetAppliedSchemas():
                if schema.startswith("PhysxMimicJointAPI:"):
                    prefix = "physxMimicJoint:" + schema.split(":", 1)[1]
                    prim.GetAttribute(prefix+":naturalFrequency").Set(0.)
                    prim.GetAttribute(prefix+":dampingRatio").Set(0.)
        self.mimic_audit = validate_dex5_mimic_stage(stage, urdf=self.urdf, robot_path=robot_path, require_hard_coupling=True)
        for side in ("left", "right"):
            for finger in FINGERS:
                bodies = [prim for prim in stage.Traverse() if prim.GetName() == f"{side}_hand_{finger}_3_link"
                          and prim.HasAPI(UsdPhysics.RigidBodyAPI)]
                if len(bodies) != 1:
                    raise RuntimeError("ambiguous template fingertip")
                body = bodies[0]
                body.GetChild("collisions").SetActive(False)
                pad = sim_utils.SphereCfg(radius=physical.pad_radius_m,
                    collision_props=sim_utils.CollisionPropertiesCfg(contact_offset=.0003, rest_offset=0.))
                pad.func(str(body.GetPath())+"/LearningPad", pad,
                    translation=(.055, .008, 0.) if finger == "thumb" else (.026, .006, 0.))
                active = [str(p.GetPath()) for p in Usd.PrimRange(body, Usd.TraverseInstanceProxies())
                          if p.HasAPI(UsdPhysics.CollisionAPI) and UsdPhysics.CollisionAPI(p).GetCollisionEnabledAttr().Get() is not False]
                if active != [str(body.GetPath())+"/LearningPad/geometry/mesh"]:
                    raise RuntimeError("unexpected distal collision model")
        colliders = [p for p in stage.Traverse() if str(p.GetPath()).startswith(robot_path+"/")
                     and "/LearningPad/" not in str(p.GetPath()) and p.HasAPI(UsdPhysics.CollisionAPI)]
        if len(colliders) != 67 or any(not math.isclose(PhysxSchema.PhysxCollisionAPI(p).GetContactOffsetAttr().Get(),
                    physical.body_contact_offset_m, abs_tol=1e-9) for p in colliders):
            raise RuntimeError("body collider audit failed")
        origins = cloner.clone(source_prim_path=self.paths[0], prim_paths=self.paths,
                               replicate_physics=True, copy_from_source=False, enable_env_ids=True)
        self.origins = torch.tensor(origins, dtype=torch.float32, device="cuda:0")
        cloner.filter_collisions(self.sim.cfg.physics_prim_path, "/World/collisions", self.paths, global_paths=["/World/Ground"])
        self.collision_groups = []
        for index, path in enumerate(self.paths):
            prim = stage.GetPrimAtPath(f"/World/collisions/group{index}")
            included = [str(p) for p in prim.GetRelationship("collection:colliders:includes").GetTargets()]
            allowed = [str(p) for p in prim.GetRelationship("physics:filteredGroups").GetTargets()]
            if included != [path] or set(allowed) != {f"/World/collisions/group{index}", "/World/collisions/global_group"}:
                raise RuntimeError("cross-environment collision filter differs from requested partition")
            self.collision_groups.append(dict(included=included, allowed=allowed))
        self.robot = Articulation(ArticulationCfg(prim_path="/World/envs/env_.*/G1", spawn=None,
            init_state=ArticulationCfg.InitialStateCfg(pos=(0., 0., .625), joint_pos=self.initial_positions),
            actuators={"fixed_body": ImplicitActuatorCfg(joint_names_expr=["(left|right)_(hip|knee|ankle|shoulder|elbow|wrist).*", "waist.*"],
                           stiffness=400., damping=25., effort_limit_sim=None),
                       "hands": ImplicitActuatorCfg(joint_names_expr=[".*_hand_thumb_.*", ".*_hand_(index|middle|ring|little)_[012]_joint"],
                           stiffness=10., damping=.3, effort_limit_sim=None)}))
        geometry["keyboard_cfg"].prim_path = "/World/envs/env_.*/Keyboard"
        geometry["keyboard_cfg"].init_state.pos = geometry["keyboard_root_position"]
        self.keyboard = Articulation(geometry["keyboard_cfg"])
        self.contacts = ContactSensor(ContactSensorCfg(prim_path="/World/envs/env_.*/Keyboard/Keys/.*", update_period=0., history_length=1))
        self.sim.reset()
        expected = set(EXPECTED_BODY_JOINTS) | set(EXPECTED_DEX5_JOINTS)
        if (set(self.robot.joint_names) != expected or len(self.robot.joint_names) != 69
                or self.robot.num_instances != batch.num_envs or self.keyboard.num_instances != batch.num_envs
                or not self.robot.is_fixed_base):
            raise RuntimeError("batched articulation shape or coordinate map differs from pinned assets")
        self.ids = self.robot.find_joints(list(self.control.joint_names), preserve_order=True)[0]
        self.key_names = tuple(geometry["key_paths"])
        self.key_ids = [self.keyboard.find_joints([geometry["key_joint_names"][key]], preserve_order=True)[0][0] for key in self.key_names]
        leaf_names = [geometry["key_paths"][key].rsplit("/",1)[-1] for key in self.key_names]
        self.contact_ids = [self.contacts.body_names.index(name) for name in leaf_names]
        self.key_body_ids = [self.keyboard.body_names.index(name) for name in leaf_names]
        self.tip_id = self.robot.find_bodies(["left_hand_index_3_link"], preserve_order=True)[0][0]
        self.pelvis_id = self.robot.find_bodies(["pelvis"], preserve_order=True)[0][0]
        self.torso_id = self.robot.find_bodies(["torso_link"], preserve_order=True)[0][0]
        self.robot_paths = list(self.robot.root_physx_view.prim_paths)
        self.keyboard_paths = list(self.keyboard.root_physx_view.prim_paths)
        # Tensor row ordering is part of the contract, never inferred from a
        # lexical sort where env_10 would precede env_2.
        if any(not p.startswith(self.paths[i]+"/") for i,p in enumerate(self.robot_paths)) or any(
                not p.startswith(self.paths[i]+"/") for i,p in enumerate(self.keyboard_paths)):
            raise RuntimeError("tensor row order differs from environment namespace order")
        joints = {j.get("name"):j for j in ET.parse(self.urdf).getroot().findall("joint")}
        self.limits = tuple(CoordinateLimit(name, "rad", float(joints[name].find("limit").get("lower")),
            float(joints[name].find("limit").get("upper")), min(float(joints[name].find("limit").get("velocity")),physical.maximum_joint_speed_rad_s))
            for name in self.robot.joint_names)
        self.mimic_pairs = [(self.robot.joint_names.index(j.find("mimic").get("joint")), self.robot.joint_names.index(name),
                            float(j.find("mimic").get("multiplier","1"))) for name,j in joints.items() if j.find("mimic") is not None]
        self.targets = self.robot.data.default_joint_pos.clone()
        self.keyboard_targets = self.keyboard.data.default_joint_pos*0.
        all_ids = torch.arange(batch.num_envs, device="cuda:0")
        for asset, q in ((self.robot,self.targets),(self.keyboard,self.keyboard_targets)):
            root = asset.data.default_root_state.clone()
            root[:,:3] += self.origins
            asset.write_root_pose_to_sim(root[:,:7], env_ids=all_ids)
            asset.write_root_velocity_to_sim(root[:,7:], env_ids=all_ids)
            asset.write_joint_state_to_sim(q, torch.zeros_like(q), env_ids=all_ids)
            asset.reset(all_ids)
        self.contacts.reset(all_ids)
        self.runtime = dict(isaac_sim=version, torch=torch.__version__, gpu=torch.cuda.get_device_name(0), num_envs=batch.num_envs,
                            replicate_physics=True, collision_filter="inverted per-environment groups plus global ground", buffers=buffers)
        self.geometry = geometry
        stage.GetRootLayer().Export(str(directory / "scene.usda"))

    def advance(self):
        self.robot.set_joint_position_target(self.targets)
        gravity = self.robot.root_physx_view.get_gravity_compensation_forces().clone()
        for parent,child,ratio in self.mimic_pairs:
            gravity[:,parent] += ratio*gravity[:,child]
            gravity[:,child] = 0.
        self.robot.set_joint_effort_target(gravity)
        self.robot.write_data_to_sim()
        self.keyboard.set_joint_position_target(self.keyboard_targets)
        self.keyboard.write_data_to_sim()
        self.sim.step(render=False)
        self.robot.update(self.config.physics_dt_s)
        self.keyboard.update(self.config.physics_dt_s)
        self.contacts.update(self.config.physics_dt_s, force_recompute=True)

    def snapshot(self):
        offset = self.quat_apply(self.robot.data.body_quat_w[:,self.tip_id],
            self.torch.tensor((.026,.006,0.),device="cuda:0").expand(self.batch.num_envs,-1))
        tensors = dict(q=self.robot.data.joint_pos, v=self.robot.data.joint_vel, targets=self.targets,
            key_q=self.keyboard.data.joint_pos[:,self.key_ids], key_targets=self.keyboard.data.joint_pos_target[:,self.key_ids],
            forces=self.contacts.data.net_forces_w[:,self.contact_ids], key_xyz=self.keyboard.data.body_pos_w[:,self.key_body_ids],
            tip=self.robot.data.body_pos_w[:,self.tip_id]+offset, pelvis=self.robot.data.body_pos_w[:,self.pelvis_id],
            torso=self.robot.data.body_quat_w[:,self.torso_id])
        # Snapshot copies: tensors change in place on the next simulation step.
        return {key:value.detach().cpu().numpy().copy() for key,value in tensors.items()}

    def cache_reset_state(self):
        self.cached = [(asset.data.joint_pos.clone(),asset.data.joint_vel.clone(),asset.data.root_state_w.clone())
                       for asset in (self.robot,self.keyboard)]
        self.cached_targets = self.targets.clone()

    def reset_rows(self, rows):
        ids = self.torch.tensor(rows,device="cuda:0",dtype=self.torch.long)
        for asset,(q,v,root) in zip((self.robot,self.keyboard),self.cached):
            asset.write_root_pose_to_sim(root[ids,:7],env_ids=ids)
            asset.write_root_velocity_to_sim(root[ids,7:],env_ids=ids)
            asset.write_joint_state_to_sim(q[ids],v[ids],env_ids=ids)
            asset.reset(ids)
        self.targets[ids] = self.cached_targets[ids]
        self.robot.set_joint_position_target(self.targets[ids],env_ids=ids)
        self.keyboard.set_joint_position_target(self.keyboard_targets[ids],env_ids=ids)
        self.contacts.reset(ids)
