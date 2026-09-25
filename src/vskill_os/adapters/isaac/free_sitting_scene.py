"""Free-root G1 + Dex5 and passive P1 caster-chair sitting experiment.

Only joint actuation is applied after reset. No root trajectory, artificial
support wrench, pelvis-chair weld, powered caster or kinematic chair is used.
"""
from __future__ import annotations

import argparse
import copy
from dataclasses import asdict
import hashlib
import json
import math
from pathlib import Path
import subprocess
import traceback
import xml.etree.ElementTree as ET

from vskill_os.simulation.free_sitting import SittingSample, sitting_guard, verify_free_sitting, standing_precondition, SITTING_CONTRACT, SEAT_CONTACT_BODIES
from vskill_os.simulation.dex5_assets import compose_mode15_g1_with_dex5, validate_g1_dex5_urdf, audit_pinned_assets
from vskill_os.simulation.dex5_preflight import validate_dex5_mimic_stage
from .balance_policy import BalancePolicy, BODY_NAMES, LOWER_NAMES, DEFAULT_LOWER, KP, KD, POLICY_SHA256

ROOT=Path(__file__).resolve().parents[4]


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def import_mobile_chair(stage, *, source: Path, center_xy: tuple[float,float], seat_height=.4775):
    from pxr import Sdf, Usd, UsdGeom, UsdPhysics, Gf
    original=Usd.Stage.Open(str(source.resolve()))
    layer=original.GetRootLayer()
    for name in ('ChairUpholstery','ChairMetal','ChairWheelRubber'):
        Sdf.CopySpec(layer,f'/World/Looks/{name}',stage.GetRootLayer(),f'/World/Looks/{name}')
    Sdf.CopySpec(layer,'/World/MobileChair',stage.GetRootLayer(),'/World/MobileChair')
    frame=stage.GetPrimAtPath('/World/MobileChair/Bodies/Frame')
    # Shorten the gas-lift column: caster positions and joint frames stay intact.
    change=seat_height-.4775
    for name in ('Seat','Backrest'):
        attr=frame.GetChild(name).GetAttribute('xformOp:translate')
        p=attr.Get();attr.Set(Gf.Vec3d(p[0],p[1],p[2]+change))
    column=frame.GetChild('Column')
    column.GetAttribute('height').Set(column.GetAttribute('height').Get()+change)
    attr=column.GetAttribute('xformOp:translate');p=attr.Get()
    attr.Set(Gf.Vec3d(p[0],p[1],p[2]+change/2))
    start=frame.GetAttribute('xformOp:translate').Get()
    delta=(center_xy[0]-start[0],center_xy[1]-start[1],0.)
    for body in stage.GetPrimAtPath('/World/MobileChair/Bodies').GetChildren():
        if body.HasAPI(UsdPhysics.RigidBodyAPI):
            position=body.GetAttribute('xformOp:translate').Get()
            body.GetAttribute('xformOp:translate').Set(Gf.Vec3d(*(position[i]+delta[i] for i in range(3))))
    joints=list(stage.GetPrimAtPath('/World/MobileChair/Joints').GetChildren())
    if len(joints)!=10 or any('DriveAPI' in api for p in joints for api in p.GetAppliedSchemas()):
        raise RuntimeError('passive chair joint audit failed')
    if any(not p.GetRelationship('physics:body0').GetTargets() or not p.GetRelationship('physics:body1').GetTargets() for p in joints):
        raise RuntimeError('chair contains a world-fixed joint')
    return {'source':str(source),'sha256':sha(source),'body_count':11,'passive_joint_count':10,
            'mobility_model':'P1 sliding_dominant, 5 swivel + 5 rolling joints',
            'seat_top_m':seat_height,'seat_size_m':[.46,.48,.075],'center_xy':center_xy}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,default=ROOT/'artifacts/free-sit/run')
    parser.add_argument('--headless',action=argparse.BooleanOptionalAction,default=True)
    parser.add_argument('--video',action='store_true')
    parser.add_argument('--device',default='cuda:0')
    parser.add_argument('--seed',type=int,default=7)
    parser.add_argument('--duration',type=float,default=18.)
    parser.add_argument('--height',type=float,default=.54)
    parser.add_argument('--seat-height',type=float,default=.4775)
    parser.add_argument('--lower-duration',type=float,default=8.)
    parser.add_argument('--chair-x',type=float,default=-.03)
    parser.add_argument('--robot-x',type=float,default=.25)
    parser.add_argument('--pitch',type=float,default=.20)
    parser.add_argument('--settled-pitch',type=float,default=.05)
    parser.add_argument('--desk-offset-x',type=float,default=0.)
    parser.add_argument('--seat-stiffness',type=float,default=20000.)
    parser.add_argument('--seat-damping',type=float,default=600.)
    parser.add_argument('--hold-load-fraction',type=float,default=.45)
    parser.add_argument('--chair-contact-mode',choices=('p1_rigid','cushion'),default='cushion')
    parser.add_argument('--type-text',default='',help='continue seated balance into contact-driven typing')
    parser.add_argument('--virtual-computer',action='store_true',help='render a real PTY desktop on the monitor with robot RGB-D capture')
    parser.add_argument('--terminal-command',help='physically type this command followed by Enter')
    parser.add_argument('--room',action='store_true',help='author a room with an open camera-facing wall')
    parser.add_argument('--resume-diagnostic',type=Path,help='debug hands from a saved seated state; never accepted as continuous sitting')
    parser.add_argument('--typing-height-drop',type=float,default=0.,help='bounded policy height adjustment during arm positioning')
    args=parser.parse_args()
    if args.terminal_command is not None:
        if not args.virtual_computer:raise ValueError('terminal command requires virtual computer')
        args.type_text=args.terminal_command+'\n'
    if not .38<=args.seat_height<=.50:raise ValueError('seat height outside tested geometry range')
    if not 0<=args.typing_height_drop<=.06:raise ValueError('invalid typing height adjustment')
    if not (.40<=args.height<=.74 and 3.<=args.duration<=60 and 3.<=args.lower_duration<=20):
        raise ValueError('invalid bounded sitting trajectory request')
    if not (-.45<=args.pitch<=.45 and -1<=args.chair_x<=1 and -1<=args.robot_x<=1 and 0<=args.desk_offset_x<=1.5):
        raise ValueError('invalid bounded workstation geometry or torso request')
    if not (0<=args.seat_stiffness<=100000 and 0<=args.seat_damping<=3000):raise ValueError('invalid seat contact parameters')
    if not .15<=args.hold_load_fraction<=.70:raise ValueError('invalid contact handoff fraction')
    if not -.30<=args.settled_pitch<=.30:raise ValueError('invalid seated posture command')
    if (args.output_dir/'report.json').exists():raise FileExistsError('choose a fresh output directory to preserve previous evidence')
    args.output_dir.mkdir(parents=True,exist_ok=True)
    source=ROOT/'artifacts/simulation-assets/unitree_ros_7d6075f7/robots'
    source_audit=audit_pinned_assets(manifest=ROOT/'configs/simulation/g1_29dof_5010_dex5_assets.json',asset_root=source.parent)
    urdf=ROOT/'artifacts/free-sit/assets/g1_dex5_free.urdf'
    urdf.parent.mkdir(parents=True,exist_ok=True)
    (urdf.parent/'UNITREE_LICENSE.txt').write_bytes((source.parent/'LICENSE').read_bytes())
    compose_mode15_g1_with_dex5(g1_urdf=source/'g1_description/g1_29dof_mode_15.urdf',
        left_hand_urdf=source/'dexterous_hand_description/dex5_1/Dex5-URDF-L/Dex5-URDF-L.urdf',
        right_hand_urdf=source/'dexterous_hand_description/dex5_1/Dex5-URDF-R/Dex5-URDF-R.urdf',output=urdf)
    tree=ET.parse(urdf)
    pelvis=tree.find("link[@name='pelvis']")
    collision=ET.SubElement(pelvis,'collision')
    for name in ('origin','geometry'):
        collision.append(copy.deepcopy(pelvis.find('visual/'+name)))
    # The earlier Dex3-length mount overlaps the wider Dex5 palm/thumb with
    # G1 wrist housings when self-collision is enabled. Explicit standoff
    # variant: keep the archived typing asset untouched.
    for side,sign in [('left',1),('right',-1)]:
        tree.find(f"joint[@name='{side}_hand_palm_joint']/origin").set('xyz',f'0.10 {sign*.003} 0')
    tree.write(urdf,encoding='utf-8',xml_declaration=True)
    validate_g1_dex5_urdf(urdf)
    report={'status':'failed','seed':args.seed,'request':vars(args).copy(),'urdf_sha256':sha(urdf),'source_asset_audit':source_audit,
        'policy_sha256':POLICY_SHA256,'robot_fixed':False,'external_support':False,'root_state_writes_during_motion':0,
        'initial_root_state_writes':1,'skill_contract':SITTING_CONTRACT,
        'physics_dt_s':.005,'policy_dt_s':.02,'guard_dt_s':.02,
        'effort_evidence':'explicit lower-body command; upper/hand implicit-drive torque estimate, not measured total actuator torque',
        'real_robot_verified':False,'self_collisions_enabled':True,'dex5_mount_standoff_x_m':.10,
        'assumptions':['NVIDIA balance policy transferred to G1 mode15 + Dex5 without retraining',
                       '0.10 m wrist mount origin; uncalibrated real mounting adapter',
                       'pelvis and hand collision convex decomposition; ten coaxial knuckle/palm filtered pairs',
                       'P1 sliding-dominant caster approximation; not an identified real caster model',
                       'PhysX contact/state oracle, not onboard perception'],
        'implementation_sha256':{p.name:sha(p) for p in (Path(__file__),Path(__file__).with_name('balance_policy.py'),ROOT/'src/vskill_os/simulation/free_sitting.py')}}
    for p in (Path(__file__).with_name('dex5_scene_geometry.py'),Path(__file__).with_name('p1_scene.py'),
              ROOT/'src/vskill_os/simulation/dex5_assets.py',ROOT/'src/vskill_os/simulation/dex5_preflight.py',
              ROOT/'configs/simulation/free_sitting_policy.json',ROOT/'configs/simulation/g1_29dof_5010_dex5_assets.json'):
        report['implementation_sha256'][p.name]=sha(p)
    report['request']['output_dir']=str(args.output_dir)
    report['request']['resume_diagnostic']=str(args.resume_diagnostic) if args.resume_diagnostic else None
    report['diagnostic_seated_reset']=args.resume_diagnostic is not None
    from isaaclab.app import AppLauncher
    app=AppLauncher(headless=args.headless,enable_cameras=True).app
    samples=[];writer=None;trace=None;typing=None;virtual_screen=None;forwarded_events=0
    try:
        import numpy as np
        import torch
        from pxr import Usd,UsdPhysics,PhysxSchema,Sdf,UsdShade
        import isaaclab.sim as sim_utils
        from isaaclab.actuators import ImplicitActuatorCfg,IdealPDActuatorCfg
        from isaaclab.assets import Articulation,ArticulationCfg
        from isaaclab.sensors import Camera,CameraCfg,ContactSensor,ContactSensorCfg
        from isaaclab.sim.converters import UrdfConverter,UrdfConverterCfg
        from isaaclab.utils.math import quat_apply_inverse,quat_apply
        from .dex5_scene_geometry import author_dex5_workstation,Dex5SceneGeometryConfig
        import os
        version=(Path(os.environ['ISAAC_PATH'])/'VERSION').read_text().strip()
        if not version.startswith('5.1.0'):raise RuntimeError('requires Isaac Sim 5.1.0')
        torch.manual_seed(args.seed);np.random.seed(args.seed)
        report['runtime']={'isaac_sim':version,'gpu':torch.cuda.get_device_name(0),'torch':torch.__version__}
        policy=BalancePolicy(ROOT/'artifacts/free-sit/policy/GR00T-WholeBodyControl-Balance.onnx')
        conv=UrdfConverter(UrdfConverterCfg(asset_path=str(urdf),usd_dir=str(urdf.parent/'usd'),usd_file_name='free_g1.usd',
            fix_base=False,merge_fixed_joints=False,make_instanceable=False,convert_mimic_joints_to_normal_joints=True,
            joint_drive=UrdfConverterCfg.JointDriveCfg(gains=UrdfConverterCfg.JointDriveCfg.PDGainsCfg(stiffness=0.,damping=0.))))
        report['usd_bundle_sha256']={str(p.relative_to(urdf.parent/'usd')):sha(p) for p in sorted((urdf.parent/'usd').rglob('*')) if p.suffix in {'.usd','.usda','.usdc'}}
        dt=.005
        sim=sim_utils.SimulationContext(sim_utils.SimulationCfg(dt=dt,device=args.device,render_interval=10))
        stage=sim_utils.get_current_stage()
        geometry=author_dex5_workstation(stage=stage,sim_utils=sim_utils,config=Dex5SceneGeometryConfig(
            table_center_x_m=.620+args.desk_offset_x,keyboard_operator_row_x_m=.450+args.desk_offset_x,
            display_center_x_m=.830+args.desk_offset_x))
        stage.RemovePrim('/World/Chair');stage.RemovePrim('/World/Ground')
        floor=sim_utils.CuboidCfg(size=(8.,8.,.05),collision_props=sim_utils.CollisionPropertiesCfg(),
            rigid_props=sim_utils.RigidBodyPropertiesCfg(kinematic_enabled=True),
            physics_material=sim_utils.RigidBodyMaterialCfg(static_friction=.9,dynamic_friction=.8),
            visual_material=sim_utils.PreviewSurfaceCfg(diffuse_color=(.24,.31,.36)))
        floor.func('/World/Floor',floor,translation=(0.,0.,-.025))
        if args.room:
            for name,size,position in (
                ('Back',(6.,.10,2.8),(0.,3.,1.4)),
                ('Left',(.10,6.,2.8),(-3.,0.,1.4)),
                ('Right',(.10,6.,2.8),(3.,0.,1.4)),
            ):
                wall=sim_utils.CuboidCfg(size=size,collision_props=sim_utils.CollisionPropertiesCfg(),
                    visual_material=sim_utils.PreviewSurfaceCfg(diffuse_color=(.65,.71,.76)))
                wall.func('/World/Room/'+name,wall,translation=position)
            report['room']={'size_m':[6.,6.,2.8],'front_wall':'open_for_external_cameras','navigation_rollout':False}
        stage.DefinePrim('/World/Looks','Scope')
        report['chair']=import_mobile_chair(stage,source=ROOT/'configs/simulation/p1_passive_office_chair.usda',center_xy=(args.chair_x,0.),seat_height=args.seat_height)
        report['chair']['contact_mode']=args.chair_contact_mode
        if args.chair_contact_mode=='cushion':
            sim_utils.modify_articulation_root_properties('/World/MobileChair',sim_utils.ArticulationRootPropertiesCfg(
                solver_position_iteration_count=32,solver_velocity_iteration_count=8))
            sim_utils.modify_rigid_body_properties('/World/MobileChair',sim_utils.RigidBodyPropertiesCfg(max_depenetration_velocity=1.))
            seat_material=UsdShade.Material.Define(stage,'/World/Looks/SeatContactMaterial')
            physics_material=UsdPhysics.MaterialAPI.Apply(seat_material.GetPrim())
            physics_material.CreateStaticFrictionAttr().Set(.8);physics_material.CreateDynamicFrictionAttr().Set(.7)
            physics_material.CreateRestitutionAttr().Set(0.)
            compliant=PhysxSchema.PhysxMaterialAPI.Apply(seat_material.GetPrim())
            compliant.CreateCompliantContactStiffnessAttr().Set(args.seat_stiffness)
            compliant.CreateCompliantContactDampingAttr().Set(args.seat_damping)
            compliant.CreateCompliantContactAccelerationSpringAttr().Set(False)
            UsdShade.MaterialBindingAPI.Apply(stage.GetPrimAtPath('/World/MobileChair/Bodies/Frame/Seat')).Bind(
                seat_material,UsdShade.Tokens.strongerThanDescendants,'physics')
            report['chair']['contact_variant']={'seat_stiffness_npm':args.seat_stiffness,'seat_damping_ns_per_m':args.seat_damping,
                'force_based_spring':True,'seat_friction':[.8,.7],'position_iterations':32,'velocity_iterations':8,'max_depenetration_velocity_mps':1.,
                'qualification':'nominal cushion contact approximation; not measured upholstery parameters'}
        sim_utils.activate_contact_sensors('/World/MobileChair')
        chair=Articulation(ArticulationCfg(prim_path='/World/MobileChair',spawn=None,actuators={}))
        initial=dict(zip(LOWER_NAMES,DEFAULT_LOWER))
        for side,sign in [('left',1),('right',-1)]:
            initial.update({f'{side}_shoulder_pitch_joint':-.1,f'{side}_shoulder_roll_joint':.7*sign,f'{side}_elbow_joint':-1.0})
            initial[f'{side}_hand_thumb_1_joint']=-1.1*sign
        cfg=ArticulationCfg(prim_path='/World/G1',spawn=sim_utils.UsdFileCfg(usd_path=conv.usd_path,activate_contact_sensors=True,
            rigid_props=sim_utils.RigidBodyPropertiesCfg(disable_gravity=False,max_depenetration_velocity=1.),
            articulation_props=sim_utils.ArticulationRootPropertiesCfg(enabled_self_collisions=True,solver_position_iteration_count=32,solver_velocity_iteration_count=8)),
            init_state=ArticulationCfg.InitialStateCfg(pos=(args.robot_x,0.,.7862),joint_pos=initial),
            actuators={
                'lower':IdealPDActuatorCfg(joint_names_expr=list(LOWER_NAMES),stiffness=dict(zip(LOWER_NAMES,KP)),damping=dict(zip(LOWER_NAMES,KD)),armature=.01),
                'upper':ImplicitActuatorCfg(joint_names_expr=['(left|right)_(shoulder|elbow|wrist).*'],stiffness=80.,damping=4.),
                'hands':ImplicitActuatorCfg(joint_names_expr=['.*_hand_thumb_.*','.*_hand_(index|middle|ring|little)_[012]_joint'],stiffness=10.,damping=.3)})
        robot=Articulation(cfg)
        # The first two finger axes share the knuckle centre. Their housings
        # sit inside the palm: suppress only palm-to-second-axis pairs, keeping
        # finger/finger, distal/palm and hand/environment collision enabled.
        report['mechanism_collision_filters']=[]
        for side in ('left','right'):
            palm=stage.GetPrimAtPath(f'/World/G1/{side}_hand_palm_link')
            rel=UsdPhysics.FilteredPairsAPI.Apply(palm).CreateFilteredPairsRel()
            for finger in ('thumb','index','middle','ring','little'):
                path=f'/World/G1/{side}_hand_{finger}_1_link'
                rel.AddTarget(path)
                report['mechanism_collision_filters'].append([str(palm.GetPath()),path])
        for body in stage.GetPrimAtPath('/World/G1').GetChildren():
            if body.GetName()!='pelvis' and '_hand_' not in body.GetName():continue
            for p in list(Usd.PrimRange(body)):
                if p.IsInstance():p.SetInstanceable(False)
            for p in Usd.PrimRange(body,Usd.TraverseInstanceProxies()):
                if p.HasAPI(UsdPhysics.MeshCollisionAPI):
                    UsdPhysics.MeshCollisionAPI(p).GetApproximationAttr().Set('convexDecomposition')
        for p in stage.Traverse():
            for schema in p.GetAppliedSchemas():
                if schema.startswith('PhysxMimicJointAPI:'):
                    prefix='physxMimicJoint:'+schema.split(':',1)[1]
                    p.GetAttribute(prefix+':naturalFrequency').Set(0.)
                    p.GetAttribute(prefix+':dampingRatio').Set(0.)
        report['mimic_preflight']=validate_dex5_mimic_stage(stage,urdf=urdf,require_hard_coupling=True)
        keyboard=Articulation(geometry['keyboard_cfg'])
        if args.type_text:
            from .dex5_demo import EXECUTORS,TIP_OFFSETS,PAD_RADIUS
            from .free_typing import FreeTypingController
            typing_sensors={}
            for executor in EXECUTORS:
                body_path='/World/G1/'+executor.replace('.', '_hand_')+'_3_link'
                stage.GetPrimAtPath(body_path+'/collisions').SetActive(False)
                pad=sim_utils.SphereCfg(radius=PAD_RADIUS,collision_props=sim_utils.CollisionPropertiesCfg(contact_offset=.0003,rest_offset=0.),
                    visual_material=sim_utils.PreviewSurfaceCfg(diffuse_color=(.1,.7,.9)))
                pad.func(body_path+'/TypingPad',pad,translation=TIP_OFFSETS[executor.split('.')[1]])
                typing_sensors[executor]=ContactSensor(ContactSensorCfg(prim_path=body_path,filter_prim_paths_expr=list(geometry['key_paths'].values()),update_period=0.))
            key_net=ContactSensor(ContactSensorCfg(prim_path='/World/Keyboard/Keys/.*',update_period=0.))
            report['typing_contact_model']='6 mm distal pads replacing distal mesh collision, attached to actual Dex5 links'
            report['assumptions'].append(report['typing_contact_model'])
            for name in ('free_typing.py','dex5_kinematics.py','dex5_demo.py'):
                report['implementation_sha256'][name]=sha(Path(__file__).with_name(name))
            report['implementation_sha256']['dex5_postures.json']=sha(ROOT/'configs/simulation/dex5_postures.json')
        sensors={}
        for name in (*SEAT_CONTACT_BODIES,'left_ankle_roll_link','right_ankle_roll_link'):
            target='/World/Floor' if 'ankle' in name else '/World/MobileChair/Bodies/Frame'
            sensors[name]=ContactSensor(ContactSensorCfg(prim_path='/World/G1/'+name,filter_prim_paths_expr=[target],
                track_contact_points=True,max_contact_data_count_per_prim=32,update_period=0.))
        net=ContactSensor(ContactSensorCfg(prim_path='/World/G1/.*',update_period=0.,history_length=4))
        cameras=[Camera(CameraCfg(prim_path=f'/World/SitCamera{i}',height=480,width=640,data_types=['rgb'],
            spawn=sim_utils.PinholeCameraCfg(focal_length=24.,clipping_range=(.02,20.)))) for i in range(2)]
        if args.virtual_computer:
            import sys
            sys.path.insert(0,str(ROOT/'artifacts/virtual-computer/deps'))
            from .virtual_screen import IsaacVirtualScreen
            virtual_screen=IsaacVirtualScreen(stage,args.output_dir/'computer',display_x=.830+args.desk_offset_x)
            # Fixture file is invisible until the real shell lists its directory.
            (virtual_screen.computer.desktop.workspace/'KEYBOARD_OK').write_text('physical keyboard demo fixture\n')
            for path in (Path(__file__).with_name('virtual_screen.py'),Path(__file__).parent.parent/'virtual_computer.py'):
                report['implementation_sha256'][path.name]=sha(path)
        sim.reset()
        if robot.is_fixed_base or chair.is_fixed_base or any('fixture' in n for n in robot.joint_names):
            raise RuntimeError('FREE_ROOT_PREFLIGHT_FAILED')
        if robot.num_joints!=69 or chair.num_joints!=10:raise RuntimeError('joint count mismatch')
        report['floating_body_preflight']={}
        for owner in ('G1','MobileChair'):
            count=0
            for prim in Usd.PrimRange(stage.GetPrimAtPath('/World/'+owner)):
                if prim.HasAPI(UsdPhysics.RigidBodyAPI):
                    count+=1
                    if UsdPhysics.RigidBodyAPI(prim).GetKinematicEnabledAttr().Get():raise RuntimeError('KINEMATIC_SUPPORT')
                    if PhysxSchema.PhysxRigidBodyAPI(prim).GetDisableGravityAttr().Get():raise RuntimeError('GRAVITY_DISABLED')
            report['floating_body_preflight'][owner]={'dynamic_body_count':count,'kinematic_body_count':0,'gravity_disabled_body_count':0}
        for prim in stage.Traverse():
            if str(prim.GetPath()).startswith('/World/G1') and prim.IsA(UsdPhysics.Joint):
                if not prim.GetRelationship('physics:body0').GetTargets() or not prim.GetRelationship('physics:body1').GetTargets():
                    raise RuntimeError('robot has joint attached to world')
        report['joint_names']=robot.joint_names;report['robot_mass_kg']=float(robot.root_physx_view.get_masses().sum())
        report['body_motion']=[]
        # One initialization write before stepping; never modify root poses again.
        checkpoint=json.loads(args.resume_diagnostic.read_text()) if args.resume_diagnostic else None
        tensor=lambda x:torch.tensor(x,device=args.device,dtype=torch.float32)
        robot.write_root_state_to_sim(tensor(checkpoint['robot_root']) if checkpoint else robot.data.default_root_state.clone())
        robot.write_joint_state_to_sim(tensor(checkpoint['robot_q']) if checkpoint else robot.data.default_joint_pos.clone(),tensor(checkpoint['robot_dq']) if checkpoint else robot.data.default_joint_vel.clone())
        if checkpoint:
            chair.write_root_state_to_sim(tensor(checkpoint['chair_root']))
            chair.write_joint_state_to_sim(tensor(checkpoint['chair_q']),tensor(checkpoint['chair_dq']))
            policy.history.clear();policy.history.extend(np.asarray(x,dtype=np.float32) for x in checkpoint['policy_history'])
            policy.action=np.asarray(checkpoint['policy_action'],dtype=np.float32)
        target=tensor(checkpoint['target']) if checkpoint else robot.data.default_joint_pos.clone()
        robot.set_joint_position_target(target);robot.write_data_to_sim()
        if args.type_text:
            typing_output=args.output_dir/'typing';typing_output.mkdir(exist_ok=True)
            typing=FreeTypingController(urdf=urdf,text=args.type_text,robot=robot,keyboard=keyboard,sensors=typing_sensors,
                net_sensor=key_net,sim=sim,geometry=geometry,torch=torch,quat_apply=quat_apply,output=typing_output)
        body_ids=robot.find_joints(list(BODY_NAMES),preserve_order=True)[0]
        lower_ids=robot.find_joints(list(LOWER_NAMES),preserve_order=True)[0]
        pelvis_id=robot.find_bodies(['pelvis'])[0][0];torso_id=robot.find_bodies(['torso_link'])[0][0]
        foot_ids=robot.find_bodies(['left_ankle_roll_link','right_ankle_roll_link'],preserve_order=True)[0]
        typing_net_ids={} if typing is None else {e:net.body_names.index(e.replace('.', '_hand_')+'_3_link') for e in EXECUTORS}
        unexpected_ids=[i for i,name in enumerate(net.body_names) if name not in sensors and i not in typing_net_ids.values()]
        allowed_net_ids={name:net.body_names.index(name) for name in sensors}
        for cam,eye,look in zip(cameras,((1.90,-2.2,1.7),(.15,-2.7,1.05)),((.40,0.,.75),(.15,0.,.70))):
            cam.set_world_poses_from_view(torch.tensor([eye],device=args.device),torch.tensor([look],device=args.device))
        stage.GetRootLayer().Export(str((args.output_dir/'scene.usda').resolve()))
        report['scene_sha256']=sha(args.output_dir/'scene.usda')
        trace=(args.output_dir/'physics.jsonl').open('w')
        phase='standing';height=.74;seat_first=None
        hold_start=None;seat_confirm_time=0.;lost_seat_time=0.
        if checkpoint:height=checkpoint['height'];hold_start=checkpoint['hold_start']
        typing_base_height=None
        def capture(filename=None):
            nonlocal writer
            from PIL import Image,ImageDraw
            if virtual_screen is not None:
                virtual_screen.sync_mount(robot)
                virtual_screen.publish(frame*dt)
            sim.render();canvas=Image.new('RGB',(1280,540),(15,20,27))
            if virtual_screen is not None and (filename or frame%200==0):
                virtual_screen.capture(frame*dt,Path(filename).stem if filename else f'{frame:06d}')
            for i,cam in enumerate(cameras):
                cam.update(dt*10,force_recompute=True)
                canvas.paste(Image.fromarray(cam.data.output['rgb'][0,:,:,:3].cpu().numpy().astype('uint8')),(640*i,0))
            draw=ImageDraw.Draw(canvas)
            label=phase if typing is None or typing.started is None else f'{typing.state} | events: {typing.text!r}'
            draw.text((16,489),f'FREE ROOT G1 + DEX5 | PASSIVE P1 CASTER CHAIR | {label.upper()}',fill='white')
            draw.text((16,514),f'No pelvis support / no root trajectory | t={frame*dt:.2f}s | height command={height:.3f}m',fill=(120,225,245))
            if filename:canvas.save(args.output_dir/filename)
            if args.video:
                if writer is None:writer=subprocess.Popen(['ffmpeg','-y','-loglevel','error','-f','rawvideo','-pixel_format','rgb24','-video_size','1280x540','-framerate','20','-i','-','-an','-c:v','libx264','-preset','fast','-crf','22','-pix_fmt','yuv420p',str(args.output_dir/'demo.mp4')],stdin=subprocess.PIPE)
                writer.stdin.write(canvas.tobytes())
        for frame in range(3600 if checkpoint else 0,int(args.duration/dt)):
            t=frame*dt
            if frame%4==0:
                if frame==800 and not standing_precondition(samples):raise RuntimeError('STANDING_PRECONDITION')
                alpha=max(0.,min(1.,(t-4.)/args.lower_duration));alpha=alpha*alpha*(3-2*alpha)
                if hold_start is None:height=.74+(args.height-.74)*alpha
                if typing_base_height is not None:
                    a=min(1.,(t-18.)/3.);a=a*a*(3-2*a)
                    height=typing_base_height-args.typing_height_drop*a
                phase='standing' if t<4. else ('lowering' if t<4.+args.lower_duration else 'settling')
                q=robot.data.joint_pos[0,body_ids].cpu().numpy();dq=robot.data.joint_vel[0,body_ids].cpu().numpy()
                rquat=robot.data.root_quat_w
                omega=quat_apply_inverse(rquat,robot.data.root_ang_vel_w)[0].cpu().numpy()
                gravity=quat_apply_inverse(rquat,torch.tensor([[0.,0.,-1.]],device=args.device))[0].cpu().numpy()
                pitch_alpha=max(0.,min(1.,(t-4.)/2.));pitch_alpha=pitch_alpha*pitch_alpha*(3-2*pitch_alpha)
                pitch_command=args.pitch*pitch_alpha
                if hold_start is not None:
                    upright=min(1.,(t-hold_start)/2.);upright=upright*upright*(3-2*upright)
                    pitch_command=args.pitch*(1-upright)+args.settled_pitch*upright
                positions=policy.target(position=q,velocity=dq,angular_velocity_body=omega,gravity_body=gravity,height=height,pitch=pitch_command)
                limits=robot.data.joint_pos_limits[0,lower_ids]
                blend=max(0.,min(1.,(t-.08)/.24))
                command=robot.data.default_joint_pos[0,lower_ids]*(1-blend)+blend*torch.as_tensor(positions,device=args.device,dtype=torch.float32)
                if hold_start is not None:
                    phase='contact_hold'
                target[0,lower_ids]=torch.clamp(command,limits[:,0]+.001,limits[:,1]-.001)
            if typing is not None:
                if frame==3600:
                    if not checkpoint:
                        report['sitting_before_typing']=verify_free_sitting(samples,robot_mass_kg=report['robot_mass_kg'])
                        if not report['sitting_before_typing']['succeeded']:raise RuntimeError('SIT_BEFORE_TYPE: '+report['sitting_before_typing']['failure'])
                        saved={'robot_root':robot.data.root_state_w.tolist(),'robot_q':robot.data.joint_pos.tolist(),'robot_dq':robot.data.joint_vel.tolist(),
                            'chair_root':chair.data.root_state_w.tolist(),'chair_q':chair.data.joint_pos.tolist(),'chair_dq':chair.data.joint_vel.tolist(),
                            'policy_history':[x.tolist() for x in policy.history],'policy_action':policy.action.tolist(),
                            'target':target.tolist(),'height':height,'hold_start':hold_start,
                            'qualification':'diagnostic restart only; continuous acceptance requires a fresh stand-to-sit run'}
                        (args.output_dir/'seated-checkpoint.json').write_text(json.dumps(saved))
                    typing.start(t)
                    typing_base_height=height
                    cameras[1].set_world_poses_from_view(torch.tensor([[.83+args.desk_offset_x,-.60,1.16]],device=args.device),
                        torch.tensor([[.45+args.desk_offset_x,0.,.71]],device=args.device))
                    capture('04-start-hands.png')
                robot.set_joint_effort_target(typing.command(t,target))
            robot.set_joint_position_target(target);robot.write_data_to_sim()
            keyboard.set_joint_position_target(keyboard.data.default_joint_pos*0);keyboard.write_data_to_sim()
            sim.step(render=False);robot.update(dt);chair.update(dt);keyboard.update(dt)
            net.update(dt,force_recompute=True)
            if typing is not None and (typing.started is not None or frame%4==0):
                typing.update((frame+1)*dt,samples[-1].seat_force_n if samples else 0.)
                if virtual_screen is not None:
                    for event in typing.events[forwarded_events:]:virtual_screen.computer.handle_event(event)
                    forwarded_events=len(typing.events)
            if frame%4:
                if args.video and frame%10==0:capture()
                continue
            for sensor in sensors.values():sensor.update(dt*4,force_recompute=True)
            up=torch.tensor([0.,0.,1.],device=args.device)
            tilt=lambda quat:math.acos(max(-1.,min(1.,float(quat_apply(quat,up)[2]))))
            chair_xyz=chair.data.root_pos_w[0].tolist();chair_top=chair_xyz[2]+.4775
            seat_contacts=[];seat_force=0.
            for name in SEAT_CONTACT_BODIES:
                sensor=sensors[name];force=sensor.data.force_matrix_w[0,0,0];point=sensor.data.contact_pos_w[0,0,0]
                magnitude=float(torch.linalg.vector_norm(force))
                if magnitude>1.:
                    local=quat_apply_inverse(chair.data.root_quat_w[0],point-chair.data.root_pos_w[0])
                    valid=bool(torch.isfinite(point).all()) and abs(float(local[2])-args.seat_height)<.035 and abs(float(local[0]))<.24 and abs(float(local[1]))<.25
                    seat_contacts.append({'body':name,'force_n':force.tolist(),'position_m':point.tolist(),'on_seat':valid})
                    if valid:seat_force+=max(0.,float(force[2]))
            feet=[max(0.,float(sensors[name].data.force_matrix_w[0,0,0,2])) for name in ('left_ankle_roll_link','right_ankle_roll_link')]
            effort=robot.data.applied_torque[0];effort_limits=robot.data.joint_effort_limits[0]
            slip=max(float(torch.linalg.vector_norm(robot.data.body_link_lin_vel_w[0,index,:2])) if force>10 else 0. for index,force in zip(foot_ids,feet))
            sample=SittingSample((frame+1)*dt,phase,tuple(robot.data.body_pos_w[0,pelvis_id].tolist()),float(torch.linalg.vector_norm(robot.data.root_lin_vel_w[0])),
                tilt(robot.data.body_quat_w[0,torso_id]),tuple(chair_xyz),float(torch.linalg.vector_norm(chair.data.root_lin_vel_w[0])),tilt(chair.data.root_quat_w[0]),
                seat_force,*feet,float(torch.linalg.vector_norm(net.data.net_forces_w_history[0],dim=-1).max()),float((effort.abs()/torch.clamp(effort_limits,min=1e-6)).max()),slip,tuple(seat_contacts),
                chair_quat_wxyz=tuple(chair.data.root_quat_w[0].tolist()),seat_top_local_m=args.seat_height,
                unexpected_contact_force_n=max(float(torch.linalg.vector_norm(net.data.net_forces_w_history[0,:,unexpected_ids],dim=-1).max()),
                    max(float(torch.linalg.vector_norm(net.data.net_forces_w[0,index]-sensors[name].data.force_matrix_w[0,0,0])) for name,index in allowed_net_ids.items()),
                    max((float(torch.linalg.vector_norm(net.data.net_forces_w[0,index]-typing_sensors[e].data.force_matrix_w[0,0].sum(dim=0))) for e,index in typing_net_ids.items()),default=0.)))
            if frame%4==0:
                samples.append(sample);trace.write(json.dumps(asdict(sample))+'\n');trace.flush()
            if frame%20==0:
                report['body_motion'].append({'time_s':sample.time_s,'q29':robot.data.joint_pos[0,body_ids].tolist(),
                    'root_quaternion_wxyz':robot.data.root_quat_w[0].tolist(),
                    'feet_xyz':robot.data.body_pos_w[0,foot_ids].tolist(),
                    'policy_target15':target[0,lower_ids].tolist()})
            failure=sitting_guard(sample)
            if failure:raise RuntimeError(failure)
            if t>=4. and hold_start is None:
                seat_confirm_time=seat_confirm_time+dt*4 if seat_force>args.hold_load_fraction*report['robot_mass_kg']*9.81 and min(feet)>20 else 0.
                if seat_confirm_time>=.12:
                    hold_start=t
                    report['contact_hold_handoff_time_s']=t
                    report['contact_hold_mode']='continue_balance_policy_with_frozen_height_command'
                    capture('02-load-transfer.png')
            elif hold_start is not None:
                lost_seat_time=lost_seat_time+dt*4 if seat_force<20 else 0.
                if lost_seat_time>.5:raise RuntimeError('SEAT_CONTACT_LOST')
            if frame==0:capture('00-initial.png')
            if frame==796:capture('01-standing.png')
            if seat_first is None and seat_force>30.:
                seat_first=sample.time_s;capture('02-first-seat-contact.png')
            if args.video and frame%10==0:capture()
            if virtual_screen is not None and not args.video and frame%200==0:capture()
        report['verification']=verify_free_sitting(samples,robot_mass_kg=report['robot_mass_kg'])
        report['status']='passed' if report['verification']['succeeded'] else 'failed'
        if typing is not None and not typing.result()['verification']['succeeded']:report['status']='failed'
        if report['status']=='passed':phase='verified_seated'
        capture('03-final.png')
        report['first_seat_contact_time_s']=seat_first
    except Exception as exc:
        report['error']=f'{type(exc).__name__}: {exc}';traceback.print_exc()
        if 'net' in locals() and net.is_initialized:
            values=torch.linalg.vector_norm(net.data.net_forces_w[0],dim=-1).tolist()
            report['failure_body_forces_n']=sorted(zip(net.body_names,values),key=lambda x:-x[1])[:16]
        if 'capture' in locals():
            try:capture('failure.png')
            except Exception:pass
    finally:
        if virtual_screen is not None:
            report['virtual_computer']=virtual_screen.close()
        if typing is not None:
            report['typing']=typing.result()
            typing.close()
        if trace:trace.close()
        if writer:
            writer.stdin.close();writer.wait(timeout=30)
            if writer.returncode:report['status']='failed';report['video_error']='encoder failed'
        report['samples']=[asdict(s) for s in samples]
        (args.output_dir/'report.json').write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps({'status':report['status'],'error':report.get('error'),'verification':report.get('verification'),'output':str(args.output_dir)}),flush=True)
        app._app.post_quit(0 if report['status']=='passed' else 1)
        app.close(wait_for_replicator=False,skip_cleanup=True)
    return 0 if report['status']=='passed' else 1


if __name__=='__main__':raise SystemExit(main())
