"""Finite-horizon Dex5 hand control while a separate policy balances the body.

Measured-base URDF IK supplies a posture seed, differential IK refines measured
tips, and only contact/travel-derived events advance press/release phases.
"""
from dataclasses import asdict, replace
import json
from pathlib import Path

from .dex5_demo import PhysicalTypingAdapter, EXECUTORS, FINGERS, TIP_OFFSETS, PAD_RADIUS, HOME_KEYS
from .dex5_kinematics import UrdfKinematics
from vskill_os.simulation.dex5_typing import Dex5Phase, Dex5TypingPlanner, Dex5TypingVerifier
from vskill_os.simulation.keyboard_bridge import KeycapSample


def joint_gravity_for_free_base(generalized_row, joint_count):
    """Project PhysX generalized gravity to actuated coordinates, without root wrench."""
    if len(generalized_row)!=joint_count+6:
        raise ValueError('FLOATING_GRAVITY_LAYOUT_MISMATCH')
    return generalized_row[6:]


class FreeTypingController(PhysicalTypingAdapter):
    def __init__(self, *, urdf, text, **kwargs):
        super().__init__(cameras=[], video=False, **kwargs)
        self.dt = .005
        self.urdf = urdf
        self.plan = replace(Dex5TypingPlanner().plan(text, support_mode='seat_contact', phase_timeout_s=12.),
                            maximum_setup_duration_s=60.)
        self.state = 'waiting_for_seated_verifier'
        self.started = None
        self.state_start = 0.
        self.stroke_index = 0
        self.seat_force = 0.
        self.done = False
        self.seeds = {}
        self.operating_limits = {}
        self.diagnostics = {}

    def set_home_goals(self,height=.065):
        # Dex5 phalanges are wider than 19 mm key pitch. Idle fingers spread
        # and stagger above the keyboard; only the selected finger approaches.
        for side in ('left','right'):
            center=sum(self.geometry['key_centers'][HOME_KEYS[side+'.'+f]][1] for f in FINGERS[1:])/4
            for i,finger in enumerate(FINGERS):
                executor=side+'.'+finger
                x,y,z=self.geometry['key_centers'][HOME_KEYS[executor]]
                if finger=='thumb':y+=.035 if side=='left' else -.035
                else:
                    y=center+(y-center)*1.65
                    x+=.015*(i%2)
                    z+=.015*(i%2)
                self.goals[executor]=self.torch.tensor([x,y,z+PAD_RADIUS+height],device=self.robot.device)

    def point_jacobian(self,executor,all_jacobians=None):
        torch=self.torch
        body=self.tip_ids[executor]
        point,_=self.point(executor)
        # PhysX dense link Jacobians are at the center of mass. Shift from
        # the measured COM to the pad, not from the URDF link origin.
        offset=point-self.robot.data.body_com_pos_w[0,body]
        full=(self.robot.root_physx_view.get_jacobians() if all_jacobians is None else all_jacobians)[0,body]
        x,y,z=offset.unbind();zero=torch.zeros((),device=self.robot.device)
        skew=torch.stack((torch.stack((zero,-z,y)),torch.stack((z,zero,-x)),torch.stack((-y,x,zero))))
        return full[:3]-skew@full[3:]

    def start(self, t):
        import numpy as np
        from scipy.spatial.transform import Rotation
        from scipy.optimize import least_squares
        model = UrdfKinematics(self.urdf, np, Rotation)
        self.kinematics=model
        root = self.robot.data.root_pos_w[0].cpu().numpy()
        quat = self.robot.data.root_quat_w[0].cpu().numpy()
        model.base_transform[:3, :3] = Rotation.from_quat(quat[[1,2,3,0]]).as_matrix()
        model.base_transform[:3, 3] = root
        model.fixed_positions = dict(zip(self.robot.joint_names, self.robot.data.joint_pos[0].tolist()))
        self.diagnostics['ik_base_transform']=model.base_transform.tolist()
        self.diagnostics['ik_fixed_positions']=model.fixed_positions
        (self.output/'ik-input.json').write_text(json.dumps(self.diagnostics,indent=2))
        previous = json.loads((Path(__file__).resolve().parents[4]/'configs/simulation/dex5_postures.json').read_text())
        self.set_home_goals(height=.15)
        for side in ('left','right'):
            names = model.joint_names(side)
            lo=np.array([model.limits[n][0]+.002 for n in names]);hi=np.array([model.limits[n][1]-.002 for n in names])
            lo[names.index(side+'_hand_thumb_0_joint')]=.1
            lo[names.index(side+'_hand_thumb_1_joint')]=-1.3 if side=='left' else .8
            hi[names.index(side+'_hand_thumb_1_joint')]=-.8 if side=='left' else 1.3
            hi[names.index(side+'_hand_thumb_2_joint')]=.9
            self.operating_limits[side]=(self.torch.tensor(lo,device=self.robot.device),self.torch.tensor(hi,device=self.robot.device))
            seed=np.clip([previous['home']['hands'][side]['joint_positions'][n] for n in names],lo,hi)
            wanted=np.array([self.goals[e].cpu().numpy() for e in EXECUTORS if e.startswith(side)])
            def error(q):
                tips,minima,_=model.measure(side,dict(zip(names,q)))
                # Seed only: live self/environment contact is independently guarded.
                floors=np.array([max(0.,.78-z) for z in minima.values()])
                return np.r_[(tips-wanted).ravel(),2*floors,.0003*(q-seed)]
            solution=least_squares(error,seed,bounds=(lo,hi),max_nfev=300,ftol=1e-8,xtol=1e-8,gtol=1e-8)
            tips,_,_=model.measure(side,dict(zip(names,solution.x)))
            residual=float(np.linalg.norm(tips-wanted,axis=1).max())
            if residual>.006:raise RuntimeError(f'PREPOSE_IK_UNREACHABLE {side}: {residual:.4f}')
            self.seeds.update(dict(zip(names,solution.x.tolist())))
            self.diagnostics[side+'_seed_max_tip_error_m']=residual
            # The thumb's proximal chain is already clear of the palm in the
            # verified approach posture. Preserve that local geometry during
            # typing; use arm/wrist motion and the distal thumb joint for Space.
            # Otherwise an idle-thumb clearance objective may fold its proximal
            # phalanx into the palm while another finger is pressing.
            for index in (0,1,2):
                i=names.index(f'{side}_hand_thumb_{index}_joint')
                lo[i]=max(lo[i],solution.x[i]-.002);hi[i]=min(hi[i],solution.x[i]+.002)
            i=names.index(f'{side}_hand_thumb_3_joint')
            lo[i]=max(lo[i],solution.x[i]-.35);hi[i]=min(hi[i],solution.x[i]+.35)
            self.operating_limits[side]=(self.torch.tensor(lo,device=self.robot.device),self.torch.tensor(hi,device=self.robot.device))
        self.seed_initial=self.robot.data.joint_pos.clone()
        self.seed_final=self.seed_initial.clone()
        for name,value in self.seeds.items():self.seed_final[0,self.robot.joint_names.index(name)]=value
        self.started=t;self.state_start=t;self.state='prepose'

    def audit_live_jacobian(self):
        import numpy as np
        from scipy.spatial.transform import Rotation
        model=self.kinematics
        quat=self.robot.data.root_quat_w[0].cpu().numpy()
        model.base_transform[:3,:3]=Rotation.from_quat(quat[[1,2,3,0]]).as_matrix()
        model.base_transform[:3,3]=self.robot.data.root_pos_w[0].cpu().numpy()
        model.fixed_positions=dict(zip(self.robot.joint_names,self.robot.data.joint_pos[0].tolist()))
        full=self.robot.root_physx_view.get_jacobians()
        for side in ('left','right'):
            names=model.joint_names(side);ids=self.arm_ids[side]
            tips,_,_=model.measure(side,{})
            actual=np.array([self.point(side+'.'+f)[0].cpu().numpy() for f in FINGERS])
            numeric=np.zeros((15,len(names)))
            for i,n in enumerate(names):
                shifted,_,_=model.measure(side,{n:model.fixed_positions[n]+1e-5})
                numeric[:,i]=((shifted-tips)/1e-5).ravel()
            matrices=[]
            for e in [side+'.'+f for f in FINGERS]:
                jac=self.point_jacobian(e,full);part=jac[:,[i+6 for i in ids]].clone()
                for parent,child in self.mimic_pairs[side]:part[:,ids.index(parent)]+=(1.3963/1.684242699)*jac[:,child+6]
                matrices.append(part.cpu().numpy())
            self.diagnostics[side+'_live_fk_error_m']=float(np.abs(actual-tips).max())
            self.diagnostics[side+'_jacobian_fk_max_error']=float(np.abs(np.vstack(matrices)-numeric).max())
            if self.diagnostics[side+'_jacobian_fk_max_error']>.001:
                raise RuntimeError('HAND_JACOBIAN_FK_MISMATCH '+side)

    def _refine(self, target, maximum_delta=.012):
        torch=self.torch
        import numpy as np
        from scipy.optimize import lsq_linear
        full=self.robot.root_physx_view.get_jacobians()
        largest=0.
        for side in ('left','right'):
            ids=self.arm_ids[side];errors=[];matrices=[]
            for executor in [e for e in EXECUTORS if e.startswith(side)]:
                point,_=self.point(executor);errors.append(self.goals[executor]-point)
                jac=self.point_jacobian(executor,full)
                reduced=jac[:,[i+6 for i in ids]].clone()
                for parent,child in self.mimic_pairs[side]:
                    reduced[:,ids.index(parent)]+=(1.3963/1.684242699)*jac[:,child+6]
                matrices.append(reduced)
            weights=torch.tensor([4.,4.,1.]*5,device=self.robot.device)
            if self.active_stroke is not None and self.active_stroke.hand.value==side:
                active=FINGERS.index(self.active_stroke.finger.value)
                # Idle fingertips may translate laterally with the hand. Their
                # vertical clearance matters more than exact rest XY targets.
                weights=torch.tensor([.3,.3,8.]*5,device=self.robot.device)
                weights[3*active:3*active+3]=torch.tensor([40.,40.,15.],device=self.robot.device)
                top=next(iter(self.geometry['key_centers'].values()))[2]+PAD_RADIUS
                for i,finger in enumerate(FINGERS):
                    if i!=active and float(self.point(side+'.'+finger)[0][2])<top+.035:
                        weights[3*i+2]=150.
                largest=max(largest,float(torch.linalg.vector_norm(errors[active])))
            elif self.active_stroke is None:
                largest=max(largest,max(float(torch.linalg.vector_norm(e)) for e in errors))
            err=torch.cat(errors)*weights;jac=torch.cat(matrices)*weights[:,None]
            lo,hi=self.operating_limits[side]
            measured=self.robot.data.joint_pos[0,ids]
            # Solve with the joint bounds inside IK. Clipping an unconstrained
            # answer afterward can repeatedly request motion through a limit,
            # starving the other joints of useful motion.
            lower=np.clip((lo-measured).cpu().numpy(),-maximum_delta,maximum_delta-1e-8)
            upper=np.clip((hi-measured).cpu().numpy(),-maximum_delta+1e-8,maximum_delta)
            matrix=np.vstack((jac.cpu().numpy(),np.eye(len(ids))*.025))
            rhs=np.r_[err.cpu().numpy()*.35,np.zeros(len(ids))]
            solved=lsq_linear(matrix,rhs,bounds=(lower,upper),method='bvls',max_iter=40,tol=1e-7)
            if not np.isfinite(solved.x).all():raise RuntimeError('NONFINITE_HAND_IK')
            delta=torch.as_tensor(solved.x,device=self.robot.device,dtype=target.dtype)
            target[0,ids]=measured+delta
        return largest

    def command(self, t, target):
        torch=self.torch
        torque=torch.zeros_like(target)
        if self.started is None:return torque
        age=t-self.state_start
        if self.state=='prepose':
            a=min(1.,age/5.);a=a*a*(3-2*a)
            for ids in self.arm_ids.values():target[0,ids]=self.seed_initial[0,ids]+a*(self.seed_final[0,ids]-self.seed_initial[0,ids])
            if age>=5.:
                self.audit_live_jacobian()
                self.state='lower_hands';self.state_start=t
        else:
            if self.state=='lower_hands':
                a=min(1.,age/3.);a=a*a*(3-2*a)
                self.set_home_goals(height=.15+(.065-.15)*a)
            error=self._refine(target,.004 if self.state=='press' else .012)
            self.diagnostics['latest_tip_error_m']=error
            if self.state=='lower_hands' and age>=3.:self.state='align';self.state_start=t
        generalized_gravity=self.robot.root_physx_view.get_gravity_compensation_forces()
        # PhysX prepends six unactuated root forces for a floating articulation.
        # They must neither be applied as a support wrench nor mistaken for
        # joint torques when reusing the old fixed-base hand controller.
        gravity=joint_gravity_for_free_base(generalized_gravity[0],self.robot.num_joints).unsqueeze(0).clone()
        for pairs in self.mimic_pairs.values():
            for parent,child in pairs:
                gravity[0,parent]+=(1.3963/1.684242699)*gravity[0,child];gravity[0,child]=0.
        for ids in self.arm_ids.values():torque[0,ids]=gravity[0,ids]
        if self.state=='press':
            self.press_feedforward_n=min(1.2,self.press_feedforward_n+2*self.dt)
            e=self.active_stroke.executor;point,_=self.point(e);jac=self.point_jacobian(e)[:,6:]
            velocity=self.robot.data.body_link_lin_vel_w[0,self.tip_ids[e]]
            force=torch.zeros(3,device=self.robot.device)
            force[:2]=torch.clamp(500*(self.goals[e][:2]-point[:2])-10*velocity[:2],-2,2)
            force[2]=-self.press_feedforward_n
            effort=jac.T@force
            ids=self.arm_ids[self.active_stroke.hand.value];torque[0,ids]+=effort[ids]
            top=self.geometry['key_centers'][self.active_stroke.key][2]
            self.goals[e][2]=max(top+PAD_RADIUS-.020,float(self.goals[e][2])-.000015)
        else:self.press_feedforward_n=0.
        return torque

    def observe(self,index,phase,consume_events=True):
        result=super().observe(index,phase,consume_events)
        return replace(result,seat_support_force_n=self.seat_force,base_constraint_active=False)

    def _begin_stroke(self,t):
        if self.stroke_index==len(self.plan.strokes):
            self.done=True;self.state='done';self.active_stroke=None;return
        stroke=self.plan.strokes[self.stroke_index];self.active_stroke=stroke
        self.set_home_goals()
        x,y,z=self.geometry['key_centers'][stroke.key]
        if stroke.key=='space':y+=.035 if stroke.hand.value=='left' else -.035
        self.goals[stroke.executor]=self.torch.tensor([x,y,z+PAD_RADIUS+.015],device=self.robot.device)
        self.state='hover';self.state_start=t;self.phase_context=(stroke.index,Dex5Phase.HOVER)

    def update(self,t,seat_force):
        self.frame=round(t/self.dt);self.seat_force=seat_force
        if self.frame%200==0:
            (self.output/'progress.json').write_text(json.dumps({'time_s':t,'state':self.state,'text':self.text,
                'tip_error_m':self.diagnostics.get('latest_tip_error_m'),'seat_force_n':seat_force}))
        for sensor in (*self.sensors.values(),self.net_sensor):sensor.update(self.dt,force_recompute=True)
        q=self.keyboard.data.joint_pos[0].tolist()
        self._travels={key:max(0.,-q[i]) for key,i in self.key_ids.items()}
        forces=self.torch.linalg.vector_norm(self.net_sensor.data.net_forces_w[0],dim=-1).tolist()
        self.maximum_force=max(self.maximum_force,max(forces))
        for key in self.key_names:
            for event in self.bridge.process(KeycapSample(key,self.travel(key),forces[self.net_ids[key]]>.01,t,source='isaac_physx_all_keycaps'),now=t):
                self.events.append(event)
                if event.event_type.value=='keydown':
                    self.text=self.text[:-1] if key=='backspace' else self.text+{'space':' ','enter':'\n'}.get(key,key if len(key)==1 else '')
        if max(forces)>12:raise RuntimeError('TYPING_EXCESS_KEY_FORCE')
        if self.started is None:
            if self.events:raise RuntimeError('KEY_EVENT_BEFORE_SEATED')
            return
        context=self.phase_context or (-1,Dex5Phase.SEATED)
        sample=self.observe(*context,consume_events=False)
        failure=self.guard.check(sample)
        if failure:raise RuntimeError('TYPING_'+failure.value)
        if self.active_stroke is not None:
            bad=[c for c in sample.contacts if c.key!=self.active_stroke.key or c.executor!=self.active_stroke.executor]
            stray=[k for k,v in sample.key_travel_m if k!=self.active_stroke.key and v>.0012]
            if bad or stray:raise RuntimeError(f'TYPING_UNPLANNED_CONTACT {bad} {stray}')
        elif sample.contacts:raise RuntimeError(f'TYPING_PREPOSE_CONTACT {sample.contacts}')
        if self.state not in ('prepose','lower_hands','align','done'):
            self.record(*context)
        if self.frame%4==0:self.trace.write(json.dumps(asdict(sample))+'\n')
        age=t-self.state_start
        if self.state in ('prepose','lower_hands'):return
        if self.state=='align':
            if self.diagnostics.get('latest_tip_error_m',1)<.006 and age>.3:
                self.record(-1,Dex5Phase.SEATED);self._begin_stroke(t)
            elif age>12:raise RuntimeError('TYPING_PREPOSE_TIMEOUT')
        elif self.state=='hover':
            if (self.diagnostics['latest_tip_error_m']<.003 and age>.1 and not sample.contacts
                    and not self.bridge.keys_down and all(c>=.004 for _,c in sample.fingertip_clearance_m)):
                self.state='press';self.state_start=t;self.phase_context=(self.stroke_index,Dex5Phase.PRESS)
                self.goals[self.active_stroke.executor][2]=self.geometry['key_centers'][self.active_stroke.key][2]+PAD_RADIUS-.0032
        elif self.state=='press' and self.active_stroke.key in self.bridge.keys_down:
            self.state='release';self.state_start=t;self.phase_context=(self.stroke_index,Dex5Phase.RELEASE)
            self.goals[self.active_stroke.executor][2]=self.geometry['key_centers'][self.active_stroke.key][2]+PAD_RADIUS+.045
        elif self.state=='release':
            if age>.15 and not self.bridge.keys_down and not sample.contacts and all(c>=.004 for _,c in sample.fingertip_clearance_m):
                if self.text!=self.active_stroke.expected_prefix:raise RuntimeError('TYPING_PREFIX_MISMATCH')
                self.stroke_index+=1;self._begin_stroke(t)
        if self.state in ('hover','press','release') and age>12:raise RuntimeError('TYPING_PHASE_TIMEOUT '+self.state)

    def result(self):
        verdict=Dex5TypingVerifier().verify(self.plan,self.observations,capability='isaac_sim_5_1')
        return {'plan':self.plan.to_dict(),'verification':verdict.to_dict(),'state':self.state,
                'observations':[asdict(o) for o in self.observations],
                'events':[asdict(e) for e in self.events],'text':self.text,
                'diagnostics':self.diagnostics,'max_keycap_force_n':self.maximum_force,
                'prepose_seed':self.seeds,'tip_positions':{e:self.point(e)[0].tolist() for e in EXECUTORS},
                'joint_positions':dict(zip(self.robot.joint_names,self.robot.data.joint_pos[0].tolist())),
                'root_pose':self.robot.data.root_state_w[0,:7].tolist(),
                'goals':{e:g.tolist() for e,g in self.goals.items()}}
