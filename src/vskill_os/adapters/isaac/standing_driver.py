"""Optional Isaac Sim 6.0.1 driver. Import only after SimulationApp starts.

Uses the compatibility core API shipped in 6.0.1, without Isaac Lab. This
implementation has a limited two-letter contact acceptance case; longer text
still requires per-run evidence. All joint commands stay in this adapter.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .p1_scene import standard_keyboard_layout, keyboard_key_center, _safe_name
from .p2_scene import IsaacDex3ActionAdapter


def _place_fixed_robot(stage, robot_path: str, translation: tuple[float, float, float]) -> str:
    """Place a referenced asset and move its world fixed-joint anchor with it.

    This USD-only helper is inspectable without starting Kit/PhysX. The copied
    asset already authors translate/orient/scale ops, so adding another default
    translate op would be invalid. A fixed joint with no body0 uses world-space
    localPos0/localRot0, which must follow the moved body1 frame.
    """
    from pxr import Gf, Usd, UsdGeom, UsdPhysics

    robot = stage.GetPrimAtPath(robot_path)
    xform = UsdGeom.Xformable(robot)
    attribute = robot.GetAttribute('xformOp:translate')
    if attribute:
        attribute.Set(Gf.Vec3d(*translation))
    else:
        xform.AddTranslateOp().Set(Gf.Vec3d(*translation))
    anchors = [UsdPhysics.FixedJoint(prim) for prim in Usd.PrimRange(robot)
               if prim.IsA(UsdPhysics.FixedJoint)
               and not UsdPhysics.FixedJoint(prim).GetBody0Rel().GetTargets()]
    if len(anchors) != 1:
        raise RuntimeError(f'Expected one world-fixed standing root joint, found {len(anchors)}')
    anchor = anchors[0]
    targets = anchor.GetBody1Rel().GetTargets()
    if len(targets) != 1:
        raise RuntimeError('Standing fixed joint must reference exactly one root rigid body')
    body = stage.GetPrimAtPath(targets[0])
    if not body.HasAPI(UsdPhysics.RigidBodyAPI):
        raise RuntimeError('Standing fixed joint body1 is not a rigid body')
    body_world = UsdGeom.Xformable(body).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
    local_pos = anchor.GetLocalPos1Attr().Get() or Gf.Vec3f(0)
    local_rot = anchor.GetLocalRot1Attr().Get() or Gf.Quatf(1)
    anchor.CreateLocalPos0Attr().Set(Gf.Vec3f(body_world.Transform(Gf.Vec3d(local_pos))))
    world_rotation = body_world.ExtractRotationQuat() * Gf.Quatd(local_rot)
    anchor.CreateLocalRot0Attr().Set(Gf.Quatf(world_rotation))
    return str(anchor.GetPath())


@dataclass(frozen=True)
class PhysicalFrame:
    tick: int
    time_s: float
    tips: dict[str, tuple[float, float, float]]
    travel: dict[str, float]
    forces: dict[str, float]
    index_forces: dict[str, dict[str, float]]
    source: str = 'isaac_physx'


class Isaac6StandingDriver:
    """One centralized physics clock and simultaneous two-arm DLS updates."""
    # Key centers are at .7602; cubes have 10 mm height. With distal local X
    # pointing down, +52 mm tip nearly coincides with the STL's distal support.
    tip_contact_offset_m = 0.005
    maximum_press_depth_m = 0.006  # 4 mm key stroke + 2 mm tracking allowance
    def __init__(self, robot_usd: Path, *, dt: float = 1 / 120, render: bool = False,
                 record_dir: Path | None = None, seed: int = 0):
        import numpy as np
        from pxr import Gf, UsdGeom, UsdPhysics, UsdLux, PhysxSchema
        from isaacsim.core.api import World
        from isaacsim.core.prims import Articulation, RigidPrim
        from isaacsim.core.utils.stage import get_current_stage

        self.np = np
        self.dt, self.render = dt, render
        self.tick = 0
        self.record_dir = record_dir
        self.world = World(physics_dt=dt, rendering_dt=dt, stage_units_in_meters=1.0,
                           backend='numpy', device='cpu')
        stage = get_current_stage()
        self.stage = stage
        UsdGeom.Xform.Define(stage, '/World')
        stage.SetDefaultPrim(stage.GetPrimAtPath('/World'))
        UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
        UsdGeom.SetStageMetersPerUnit(stage, 1.0)
        # The default ground fetches an NVIDIA network asset synchronously.
        # A local collision plane makes startup reproducible without Nucleus.
        self.world.scene.add_ground_plane(size=8.0, static_friction=0.8,
                                          dynamic_friction=0.6, restitution=0.0)
        print('[standing] local ground created', flush=True)
        light = UsdLux.DomeLight.Define(stage, '/World/Light')
        light.CreateIntensityAttr(1800)
        robot_prim = UsdGeom.Xform.Define(stage, '/World/G1')
        robot_prim.GetPrim().GetReferences().AddReference(str(robot_usd.resolve()))
        self.fixed_root_joint = _place_fixed_robot(stage, '/World/G1', (0.28, 0.0, 0.79))
        self.centers = {k.name: keyboard_key_center(k, operator_row_x=0.72,
                        left_origin_y=0.145, top_z=0.7602) for k in standard_keyboard_layout()}

        def cube(path, pos, size, color, dynamic=False, mass=0.006, collidable=True):
            # Joint anchors are expressed in the unscaled rigid-body frame.
            # Scaling the body itself also scales those anchors and can pull
            # all keys toward the tiny root body during the first reset.
            obj = UsdGeom.Xform.Define(stage, path)
            obj.AddTranslateOp().Set(Gf.Vec3d(*pos))
            shape = UsdGeom.Cube.Define(stage, path + '/Shape')
            shape.CreateSizeAttr(1.0)
            shape.AddScaleOp().Set(Gf.Vec3f(*size))
            shape.CreateDisplayColorAttr([Gf.Vec3f(*color)])
            if collidable:
                UsdPhysics.CollisionAPI.Apply(shape.GetPrim())
            if dynamic:
                UsdPhysics.RigidBodyAPI.Apply(obj.GetPrim())
                UsdPhysics.MassAPI.Apply(obj.GetPrim()).CreateMassAttr(mass)
                PhysxSchema.PhysxRigidBodyAPI.Apply(obj.GetPrim()).CreateDisableGravityAttr(True)
                PhysxSchema.PhysxContactReportAPI.Apply(obj.GetPrim()).CreateThresholdAttr(0.0)
                if collidable:
                    collision = PhysxSchema.PhysxCollisionAPI.Apply(shape.GetPrim())
                    collision.CreateContactOffsetAttr(0.0005)
                    collision.CreateRestOffsetAttr(0.0)
            return obj

        cube('/World/Desk', (0.83, 0, 0.71), (0.55, 0.85, 0.04), (0.25, 0.31, 0.37))
        for x in (0.60, 1.06):
            for y in (-0.36, 0.36):
                cube(f'/World/Leg_{int(x*100)}_{int((y+1)*100)}', (x,y,0.345),
                     (0.045,0.045,0.69), (0.1,0.12,0.14))
        UsdGeom.Xform.Define(stage, '/World/Keyboard')
        UsdGeom.Xform.Define(stage, '/World/Keyboard/Keys')
        UsdGeom.Xform.Define(stage, '/World/Keyboard/Joints')
        base = cube('/World/Keyboard/RootBody', (0.77,0,0.743), (0.001,0.001,0.001),
                    (0.05,0.05,0.05), True, 2.0, collidable=False)
        fixed = UsdPhysics.FixedJoint.Define(stage, '/World/Keyboard/Joints/fixed')
        fixed.CreateBody1Rel().SetTargets([base.GetPath()])
        fixed.CreateLocalPos0Attr().Set(Gf.Vec3f(0.77,0,0.743))
        cube('/World/KeyboardShell', (0.77,0,0.741), (0.15,0.32,0.018), (0.04,0.05,0.06))
        for key in standard_keyboard_layout():
            safe = _safe_name(key.name)
            path = '/World/Keyboard/Keys/' + safe
            color = (0.12,0.43,0.68) if self.centers[key.name][1] >= 0 else (0.68,0.34,0.1)
            body = cube(path, self.centers[key.name], (0.01705,0.01905*key.width_units-0.002,0.01), color, True)
            body.GetPrim().SetCustomDataByKey('vskill:key', key.name)
            joint = UsdPhysics.PrismaticJoint.Define(stage, f'/World/Keyboard/Joints/{safe}_joint')
            joint.CreateBody0Rel().SetTargets([base.GetPath()])
            joint.CreateBody1Rel().SetTargets([body.GetPath()])
            p = self.centers[key.name]
            joint.CreateLocalPos0Attr().Set(Gf.Vec3f(p[0]-0.77,p[1],p[2]-0.743))
            joint.CreateAxisAttr('Z')
            joint.CreateLowerLimitAttr(-0.004)
            joint.CreateUpperLimitAttr(0.0)
            drive = UsdPhysics.DriveAPI.Apply(joint.GetPrim(), 'linear')
            drive.CreateTypeAttr('force')
            drive.CreateTargetPositionAttr(0.0)
            drive.CreateStiffnessAttr(1000.0)
            drive.CreateDampingAttr(20.0)
            drive.CreateMaxForceAttr(20.0)
        UsdPhysics.ArticulationRootAPI.Apply(stage.GetPrimAtPath('/World/Keyboard'))
        self.robot = self.world.scene.add(Articulation('/World/G1', name='standing_g1', reset_xform_properties=False))
        self.keyboard = self.world.scene.add(Articulation('/World/Keyboard', name='keyboard', reset_xform_properties=False))
        # Resolve rigid link paths from the composed USD; never assume hierarchy.
        self.tip_paths = []
        for name in ('left_hand_index_1_link','right_hand_index_1_link'):
            matches = [str(p.GetPath()) for p in stage.Traverse() if p.GetName() == name and p.HasAPI(UsdPhysics.RigidBodyAPI)]
            if len(matches) != 1:
                raise RuntimeError(f'Expected exactly one rigid index link {name}: {matches}')
            self.tip_paths.extend(matches)
        self.tips_view = self.world.scene.add(RigidPrim(self.tip_paths, name='index_tips', reset_xform_properties=False))
        self.keys_view = self.world.scene.add(RigidPrim('/World/Keyboard/Keys/.*', name='all_key_contacts',
            reset_xform_properties=False, track_contact_forces=True,
            contact_filter_prim_paths_expr=self.tip_paths, max_contact_count=4096))
        self.camera = None
        if record_dir is not None or render:
            from isaacsim.sensors.camera import Camera
            if render:
                from isaacsim.core.utils.viewports import set_camera_view
                set_camera_view(eye=[-0.8,-1.3,1.55], target=[0.6,0,0.82])
            self.camera = Camera('/World/Camera', position=np.array([0.48,-0.64,1.35]), resolution=(960,720))
        print('[standing] scene built; initializing PhysX', flush=True)
        self.world.reset()
        print('[standing] PhysX initialized', flush=True)
        self.robot.set_gains(kps=np.full((1,self.robot.num_dof),1000.0), kds=np.full((1,self.robot.num_dof),100.0))
        self.q_target = self.robot.get_joint_positions().copy()
        self.limits = self.robot.get_dof_limits()[0]
        # Mirrored local-Z finger axes from the pinned Dex3 URDF. Retraction
        # keeps the unused middle fingertips away from the typing plane.
        for hand, sign in (('left', -1), ('right', 1)):
            for name, angle in zip(IsaacDex3ActionAdapter.FINGER_JOINTS[hand+'.middle'], (0.8, 1.0)):
                index = self.robot.get_dof_index(name)
                self.q_target[0, index] = np.clip(sign * angle, *self.limits[index])
        self.joint_ids = {h:[self.robot.get_dof_index(n) for n in (*IsaacDex3ActionAdapter.ARM_JOINTS[h],
                         *IsaacDex3ActionAdapter.FINGER_JOINTS[h+'.index'])] for h in ('left','right')}
        self.body_ids = {h:self.robot.get_body_index(IsaacDex3ActionAdapter.TIP_BODY_NAMES[h+'.index']) for h in ('left','right')}
        self.key_ids = {k:self.keyboard.get_dof_index(_safe_name(k)+'_joint') for k in self.centers}
        self.key_rows = {Path(p).name:i for i,p in enumerate(self.keys_view.prim_paths)}
        self.tip_rows = [self.tips_view.prim_paths.index(path) for path in self.tip_paths]
        if set(self.key_rows) != {_safe_name(k) for k in self.centers}:
            raise RuntimeError('Contact sensor does not cover all 74 keys')
        if self.robot.num_dof != 43 or self.keyboard.num_dof != 74:
            raise RuntimeError('Expected 43 G1/Dex3 joints and 74 physical keys')
        jacobian_shape = self.robot.get_jacobians().shape
        if jacobian_shape != (1, self.robot.num_bodies - 1, 6, self.robot.num_dof):
            raise RuntimeError(f'Expected fixed-base Jacobian shape, got {jacobian_shape}')
        if self.camera:
            # Camera poses can be inspected/changed in GUI; recording is evidence
            # only, never promoted automatically to vision verification.
            self.camera.initialize()
            from pxr import Gf
            cam = UsdGeom.Camera(stage.GetPrimAtPath('/World/Camera'))
            matrix = Gf.Matrix4d().SetLookAt(Gf.Vec3d(0.48,-0.64,1.35),Gf.Vec3d(0.77,0,0.76),Gf.Vec3d(0,0,1)).GetInverse()
            xform=UsdGeom.Xformable(cam)
            xform.ClearXformOpOrder()
            xform.AddTransformOp().Set(matrix)
        self.initial_q = self.q_target.copy()
        for _ in range(30):
            self.step({})

    def _points(self):
        np=self.np
        positions,quaternions=self.tips_view.get_world_poses()
        positions,quaternions=positions[self.tip_rows],quaternions[self.tip_rows]
        offsets=[]
        for quat in quaternions:
            # scalar-first quaternion rotation of distal-link offset.
            w=quat[0]; v=quat[1:]; p=np.array([0.052,0,0])
            offsets.append(p+2*np.cross(v,np.cross(v,p)+w*p))
        return positions+np.array(offsets),np.array(offsets)

    def step(self, targets: Mapping[str, tuple[float,float,float]]) -> PhysicalFrame:
        np=self.np
        points, offsets=self._points()
        jacobians=self.robot.get_jacobians()[0]
        current=self.robot.get_joint_positions()[0]
        for h_index,hand in enumerate(('left','right')):
            if hand not in targets:
                continue
            ids=self.joint_ids[hand]
            # Fixed base Jacobian omits root body.
            body=jacobians[self.body_ids[hand]-1][:,ids]
            x,y,z=offsets[h_index]
            skew=np.array([[0,-z,y],[z,0,-x],[-y,x,0]])
            jac=body[:3]-skew@body[3:]
            destination = np.array(targets[hand], dtype=float)
            # Keep the distal index axis pointing down. Position-only IK can
            # lower the palm/middle finger into adjacent keys while its index
            # point is still above the requested hover plane.
            axis = offsets[h_index] / 0.052
            downward = np.array([0.0, 0.0, -1.0])
            alignment = float(np.dot(axis, downward))
            angular_error = np.cross(axis, downward)
            if alignment < -0.95:
                # Resolve the 180-degree cross-product singularity explicitly.
                angular_error = np.cross(axis, np.array([0.0, 1.0, 0.0]))
            if alignment < 0.94:
                destination[2] = max(destination[2], 0.91)
            err = destination - points[h_index]
            pose_jac = np.concatenate((jac, body[3:] * 0.12), axis=0)
            pose_error = np.concatenate((err * 0.55, angular_error * 0.12 * 0.55))
            delta = pose_jac.T @ np.linalg.solve(pose_jac @ pose_jac.T + np.eye(6)*0.04**2, pose_error)
            delta*=min(1.0,0.006/max(float(np.max(np.abs(delta))),1e-12))
            target=np.clip(self.q_target[0,ids]+delta,current[ids]-0.08,current[ids]+0.08)
            self.q_target[0,ids]=np.clip(target,self.limits[ids,0]+0.002,self.limits[ids,1]-0.002)
        self.robot.set_joint_position_targets(self.q_target)
        # Keys remain passive spring drives. Never command a key down.
        self.world.step(render=self.render or (self.camera is not None and self.tick%12==0))
        self.tick+=1
        frame=self.observe()
        if self.record_dir is not None and self.camera is not None and self.tick%12==0:
            from PIL import Image
            rgba=self.camera.get_rgba()
            if rgba is not None and rgba.size:
                self.record_dir.mkdir(parents=True,exist_ok=True)
                Image.fromarray(rgba.astype(np.uint8)).save(self.record_dir/f'{self.tick:07d}.png')
        return frame

    def observe(self) -> PhysicalFrame:
        np=self.np
        points,_=self._points()
        positions=self.keyboard.get_joint_positions()[0]
        forces=self.keys_view.get_net_contact_forces(dt=self.dt)
        pairs=self.keys_view.get_contact_force_matrix(dt=self.dt)
        if pairs is None or pairs.shape[1] != 2:
            raise RuntimeError('Index-to-key pair contact evidence unavailable')
        return PhysicalFrame(self.tick,self.tick*self.dt,
            {h:tuple(float(v) for v in points[i]) for i,h in enumerate(('left','right'))},
            {k:max(0.0,-float(positions[i])) for k,i in self.key_ids.items()},
            {k:float(np.linalg.norm(forces[self.key_rows[_safe_name(k)]])) for k in self.centers},
            {k:{h:float(np.linalg.norm(pairs[self.key_rows[_safe_name(k)],i])) for i,h in enumerate(('left','right'))} for k in self.centers})

    def close(self):
        self.world.stop()
