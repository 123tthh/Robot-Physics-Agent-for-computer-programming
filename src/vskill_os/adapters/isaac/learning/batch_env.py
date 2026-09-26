"""Batch control, row-local bridges/feedback and subset reset orchestration."""

from dataclasses import replace
import time
from uuid import uuid4

from ....learning.batch import RowState, diagnostic_profile, row_seed
from ....learning.contracts import JointRecord, KeyEventRecord, PhysicalKeyRecord, RecordStamp
from ....learning.environment import EpisodeMetadata, JointTargetGuard, PolicyObservation, ResetSpec
from ....learning.feedback import ReadoutFault
from ....learning.feedback_session import FeedbackSpec
from ....learning.physical import AppliedAction, PhysicsSafetyMonitor, PhysicsSample
from ....learning.text_task import DigitalInputPlan, EpisodeScope
from ....simulation.keyboard_bridge import KeyboardEventBridge, KeycapSample
from ...text_feedback import session_from_spec
from .batch_recording import RawChunks, save_snapshot, sha, write_gzip
from .single_hand_env import write_json


def feedback_spec_for_row(index: int) -> FeedbackSpec:
    profile = diagnostic_profile(index)
    return FeedbackSpec(input_plan=DigitalInputPlan((0,)) if profile == "software_drop" else DigitalInputPlan(),
                        readout_faults=(ReadoutFault(1,missing=True),) if profile == "missing_readout" else ())


def sample_from_raw(raw, index, stamp) -> PhysicsSample:
    def values(key):
        return tuple(float(v) for v in raw[key][index].reshape(-1))
    return PhysicsSample(stamp,values("q"),values("v"),values("targets"),values("key_q"),values("key_targets"),
                         values("forces"),values("key_xyz"),values("tip"),values("pelvis"),values("torso"))


class BatchLane:
    def __init__(self, scene, index: int, generation: int, global_tick: int):
        self.index,self.generation,self.start_tick = index,generation,global_tick
        self.reset = ResetSpec(f"env{index}-generation{generation}",seed=row_seed(scene.batch.seed,index))
        self.state = RowState(index,self.reset)
        self.scope = EpisodeScope(f"isaac-batch-{index}",self.reset.episode_id,"isaac_sim_seconds")
        self.owner,self.tick,self.previous = uuid4().hex,0,(0.,)*len(scene.ids)
        self.events,self.actions = [],[]
        self.bridge = KeyboardEventBridge()
        self.spec = feedback_spec_for_row(index)
        self.feedback = session_from_spec(self.spec,scope=self.scope,source="isaac_physx_feedback")
        self.closed = False

    def stamp(self, stream: str, sequence: int, dt: float):
        now = self.tick*dt
        return RecordStamp(f"{self.scope.env_id}:{self.scope.episode_id}:{stream}:{sequence}",self.scope.env_id,
                           self.scope.episode_id,sequence,self.scope.clock_domain,now,now,"isaac.batch."+stream)

    def consume(self, raw, scene):
        start = len(self.events)
        for index,key in enumerate(scene.key_names):
            travel = max(0.,-float(raw["key_q"][self.index,index]))
            force = float(sum(v*v for v in raw["forces"][self.index,index])**.5)
            sample_id = self.stamp("physx",self.tick,scene.config.physics_dt_s).record_id
            for event in self.bridge.process(KeycapSample(key,travel,force>.01,self.tick*scene.config.physics_dt_s,
                        "isaac.physx_batch",f"{sample_id}:{key}"),now=self.tick*scene.config.physics_dt_s):
                self.events.append(KeyEventRecord(self.stamp("bridge",len(self.events),scene.config.physics_dt_s),key,
                                                 event.event_type.value,event.reason,sample_id))
        self.feedback.advance(self.tick*scene.config.physics_dt_s,tuple(self.events[start:]))

    def observation(self, raw, scene):
        joints = tuple(JointRecord(name,float(raw["q"][self.index,i]),float(raw["v"][self.index,i]),float(raw["targets"][self.index,i]))
                       for name,i in zip(scene.control.joint_names,scene.ids,strict=True))
        keys = tuple(PhysicalKeyRecord(key,max(0.,-float(raw["key_q"][self.index,i])),
            float(sum(v*v for v in raw["forces"][self.index,i])**.5)>.01,abs(float(raw["forces"][self.index,i,2])))
            for i,key in enumerate(scene.key_names))
        return self.feedback.observe(PolicyObservation(self.scope.env_id,self.scope.episode_id,self.tick*scene.config.physics_dt_s,
                                                       "a",joints,keys,self.previous))


class BatchKeyboardEnv:
    """Supervisor. Policies receive only immutable observations and metadata.

    A batch action is validated atomically before any target is written. A
    malformed row cannot partly advance neighbors. Safety is checked on every
    row at every substep, including initialization and idle rows.
    """
    def __init__(self, scene):
        self.scene,self.directory = scene,scene.directory
        self.guard = JointTargetGuard(scene.control)
        origins = scene.origins.cpu().tolist()
        self.monitors = [PhysicsSafetyMonitor(scene.limits,maximum_force_n=scene.config.maximum_contact_force_n,
                          support_origin_xyz_m=(o[0],o[1],.625+o[2]),support_tolerance_m=scene.config.support_tolerance_m) for o in origins]
        self.lanes,self.generations,self.global_tick = [None]*scene.batch.num_envs,[-1]*scene.batch.num_envs,0
        self.records,self.reset_records,self.rejections = [],[],[]
        self.recorder = RawChunks(self.directory,scene.batch.chunk_samples)
        self.timings = dict(physics_s=0.,transfer_s=0.,safety_event_recording_s=0.)
        self.raw = scene.snapshot()
        save_snapshot(self.directory/"initial.npz",self.raw)
        self._safety(self.raw)

    def _safety(self, raw):
        for index,monitor in enumerate(self.monitors):
            stamp = RecordStamp(f"safety:{self.global_tick}:{index}",f"isaac-batch-{index}","global-safety",self.global_tick,
                                "isaac_sim_seconds",self.global_tick*self.scene.config.physics_dt_s,
                                self.global_tick*self.scene.config.physics_dt_s,"isaac.batch.physx")
            failures = monitor.check(sample_from_raw(raw,index,stamp))
            if failures:
                import numpy as np
                save_snapshot(self.directory/"safety_failure.npz",dict(raw,global_tick=np.asarray(self.global_tick,dtype=np.int64)))
                write_json(self.directory/"safety_failure.json",dict(index=index,global_tick=self.global_tick,
                    physics_dt_s=self.scene.config.physics_dt_s,failure_codes=list(failures)))
                raise RuntimeError(f"independent substep safety rejected row {index}: {failures}")

    def _advance(self):
        start = time.perf_counter()
        self.scene.advance()
        self.timings["physics_s"] += time.perf_counter()-start
        start = time.perf_counter()
        self.raw = self.scene.snapshot()
        self.timings["transfer_s"] += time.perf_counter()-start
        self.global_tick += 1
        start = time.perf_counter()
        self._safety(self.raw)
        for lane in self.lanes:
            if lane is not None and not lane.closed:
                lane.tick += 1
                lane.consume(self.raw,self.scene)
        self.recorder.append(self.raw,self.global_tick,self.generations,[lane.tick if lane else -1 for lane in self.lanes])
        self.timings["safety_event_recording_s"] += time.perf_counter()-start

    def settle(self):
        for tick in range(self.scene.config.settling_steps):
            self._advance()
            if (tick+1) % 60 == 0:
                print(f"VF04_SETTLE envs={self.scene.batch.num_envs} physics_steps={tick+1}",flush=True)
        self.scene.cache_reset_state()
        save_snapshot(self.directory/"settled.npz",self.raw)

    def _seal(self, lane, reason):
        if lane.closed:
            return
        if lane.bridge.rejected_samples:
            raise RuntimeError(f"key sample loss in row {lane.index}: {lane.bridge.rejected_samples[:3]}")
        episode = lane.feedback.close(reason)
        cleanup = lane.bridge.all_keys_up(monotonic_time=lane.tick*self.scene.config.physics_dt_s,reason="batch_reset_or_close")
        name = f"episodes/{lane.reset.episode_id}.json.gz"
        write_gzip(self.directory/name,dict(schema_version="vf-batch-episode/1",index=lane.index,generation=lane.generation,
            start_global_tick=lane.start_tick,end_global_tick=self.global_tick,reset=lane.reset.to_dict(),owner_token=lane.owner,
            feedback_spec=lane.spec.to_dict(),feedback=episode.to_dict(),actions=[a.to_dict() for a in lane.actions],
            state=lane.state.fingerprint(),administrative_cleanup_event_count=len(cleanup),physical_release_claimed=False))
        self.records.append(dict(path=name,sha256=sha(self.directory/name)))
        lane.closed = True

    def state(self):
        return [None if lane is None else dict(**lane.state.fingerprint(),owner=lane.owner,tick=lane.tick,
                 generation=lane.generation,previous=lane.previous,events=len(lane.events),actions=len(lane.actions),
                 feedback=lane.feedback.queue.budget().to_dict()) for lane in self.lanes]

    def reset_rows(self, rows):
        if not rows or len(set(rows)) != len(rows) or any(type(i) is not int or not 0<=i<len(self.lanes) for i in rows):
            raise ValueError("reset needs unique valid row indices")
        before = self.scene.snapshot()
        state_before = self.state()
        for index in rows:
            if self.lanes[index] is not None:
                self._seal(self.lanes[index],"cancelled")
        self.scene.reset_rows(rows)
        self.raw = self.scene.snapshot()
        for index in rows:
            self.generations[index] += 1
            self.lanes[index] = BatchLane(self.scene,index,self.generations[index],self.global_tick)
        self._safety(self.raw)
        number = len(self.reset_records)
        before_path,after_path = f"reset-{number}-before.npz",f"reset-{number}-after.npz"
        save_snapshot(self.directory/before_path,before)
        save_snapshot(self.directory/after_path,self.raw)
        self.reset_records.append(dict(rows=list(rows),global_tick=self.global_tick,before=before_path,after=after_path,
            before_sha256=sha(self.directory/before_path),after_sha256=sha(self.directory/after_path),
            state_before=state_before,state_after=self.state()))
        return {index:(self.lanes[index].observation(self.raw,self.scene),EpisodeMetadata(self.lanes[index].scope.env_id,
            self.lanes[index].scope.episode_id,self.lanes[index].reset.seed,self.lanes[index].owner,self.scene.control,
            "isaac_physx_batch_v1","fixed_pelvis_seated_pd")) for index in rows}

    def step(self, actions):
        if len(actions) != len(self.lanes) or any(lane is None or lane.closed for lane in self.lanes):
            raise ValueError("step requires one action per active row")
        checked = []
        for index,(lane,action) in enumerate(zip(self.lanes,actions,strict=True)):
            previous = tuple(float(v) for v in self.raw["targets"][index,self.scene.ids])
            applied = self.guard.apply(previous,action,time_s=lane.tick*self.scene.config.physics_dt_s,
                owner_token=lane.owner,expected_sequence=len(lane.actions))
            checked.append((previous,applied))
        for index,(lane,action,(previous,applied)) in enumerate(zip(self.lanes,actions,checked,strict=True)):
            lane.actions.append(AppliedAction(lane.tick*self.scene.config.physics_dt_s,action,previous,applied,None))
            lane.previous = tuple(a-b for a,b in zip(applied,previous,strict=True))
            self.scene.targets[index,self.scene.ids] = self.scene.torch.tensor(applied,device="cuda:0")
        for _ in range(self.scene.config.decimation):
            self._advance()
        return tuple(lane.observation(self.raw,self.scene) for lane in self.lanes)

    def request(self,index,owner,purpose="intermediate"):
        if type(index) is not int or not 0 <= index < len(self.lanes):
            raise ValueError("feedback row index is outside the batch")
        lane = self.lanes[index]
        if lane.closed or lane.owner != owner:
            raise RuntimeError("stale feedback owner")
        return lane.feedback.request(purpose)

    def close(self,reason="cancelled"):
        try:
            for lane in self.lanes:
                if lane is not None and not lane.closed:
                    lane.observation(self.raw,self.scene)
                    self._seal(lane,reason)
        finally:
            self.recorder.flush()
