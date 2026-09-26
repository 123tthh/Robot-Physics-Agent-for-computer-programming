"""CPU replay of every row/substep, request stream and subset-reset boundary.

NumPy is needed only for the optional compressed raw-array adapter. No Isaac
application is started. The verifier takes frozen configs from the caller.
"""

import hashlib
import json
import math
from pathlib import Path
import random

from ....learning.batch import BatchConfig, diagnostic_profile, row_seed
from ....learning.contracts import EpisodeRecord, KeyBinding, ObservationFrame, PhysicalKeyRecord, RecordStamp, RunManifest, TaskSpec
from ....learning.environment import JointTargetGuard, ResetSpec
from ....learning.evaluation import EvaluationPolicy, check_key_transitions
from ....learning.feedback_evaluation import evaluate_feedback
from ....learning.feedback_session import FeedbackEpisode, FeedbackSpec
from ....learning.physical import AppliedAction
from ....learning.serialization import _unique_object
from ....learning.text_task import DigitalInputPlan, EpisodeScope
from ....learning.feedback import ReadoutFault
from ....simulation.dex5_assets import EXPECTED_BODY_JOINTS, EXPECTED_DEX5_JOINTS, audit_pinned_assets
from ..p1_scene import keyboard_key_center, standard_keyboard_layout
from .batch_recording import RAW_FIELDS, read_gzip, sha
from .batch_smoke import BATCH_SOURCES
from .config import SingleHandConfig, control_spec, direct_joint_initialization
from .single_hand_env import DEFAULT_URDF, ROOT


def check_reset_isolation(before: dict, after: dict, rows: list[int], state_before: list, state_after: list) -> list[str]:
    import numpy as np
    errors = []
    untouched = [i for i in range(len(state_before)) if i not in rows]
    if before.keys() != after.keys() or any(not np.array_equal(before[k][untouched],after[k][untouched]) for k in before):
        errors.append("RESET_CHANGED_NEIGHBOR_PHYSICS")
    if any(state_before[i] != state_after[i] for i in untouched):
        errors.append("RESET_CHANGED_NEIGHBOR_STATE")
    for i in rows:
        current,old = state_after[i],state_before[i]
        if (current is None or current["tick"] != 0 or current["actions"] or current["events"] or current["policy_state"]
                or any(current["previous"]) or current["feedback"]["accepted_requests"]
                or current["feedback"]["pending_requests"] or old is not None and current["owner"] == old["owner"]):
            errors.append("RESET_DID_NOT_CLEAR_SELECTED_STATE")
        expected_rng = hashlib.sha256(repr(random.Random(current["seed"]).getstate()).encode()).hexdigest()
        if current["rng_sha256"] != expected_rng:
            errors.append("RESET_RANDOM_STATE")
    return list(dict.fromkeys(errors))


def verify_batch(directory: Path, *, batch: BatchConfig, physical: SingleHandConfig) -> dict:
    import numpy as np
    report = dict(schema_version="vf-batch-evaluation/1",num_envs=batch.num_envs,accepted=False,
                  findings=[],rows=[],camera_verified=False,desktop_verified=False,learned_policy=False,
                  recording=str(directory.resolve()))
    def require(condition, message):
        if not condition:
            raise ValueError(message)
    def load(path):
        with np.load(path,allow_pickle=False) as file:
            return {key:file[key] for key in file.files}
    try:
        require(not (directory/"failure.json").exists(),"worker preserved failure.json")
        m = json.loads((directory/"manifest.json").read_text(),object_pairs_hook=_unique_object)
        require(m["schema_version"] == "vf-batch-bundle/1","invalid bundle schema")
        require(m["sources"] == {name:sha(ROOT/name) for name in BATCH_SOURCES},"SOURCE_HASH: collector revision differs")
        require(BatchConfig.read(directory/"batch_config.json") == batch
                and SingleHandConfig.read(directory/"physics_config.json") == physical,"FROZEN_CONFIG_MISMATCH")
        expected_assets = {str(DEFAULT_URDF.relative_to(ROOT)):sha(DEFAULT_URDF),**{
            str(path.relative_to(ROOT)):sha(path) for path in sorted((ROOT/"artifacts/verification_feedback/assets/batch_usd").rglob("*.usd"))}}
        require(m["assets"] == expected_assets and len(expected_assets)>1,"ASSET_HASH")
        audit_pinned_assets()
        for entry in (*m["raw_chunks"],*m["episodes"]):
            path = Path(entry["path"])
            require(not path.is_absolute() and ".." not in path.parts,"unsafe artifact path")
            require(sha(directory/path)==entry["sha256"],"ARTIFACT_HASH: "+str(path))
        fixed_files = {"scene.usda","initial.npz","settled.npz","batch_config.json","physics_config.json",
                       "reset-0-before.npz","reset-0-after.npz","reset-1-before.npz","reset-1-after.npz"}
        require(set(m["files"]) == fixed_files,"unexpected batch sidecar file set")
        require(all(sha(directory/name)==digest for name,digest in m["files"].items()),"ARTIFACT_HASH: sidecar")
        count = batch.num_envs
        require(m["runtime"]["num_envs"]==count and m["runtime"]["isaac_sim"].startswith("5.1.0")
                and m["runtime"]["replicate_physics"] is True,"wrong physical backend")
        require(m["runtime"]["buffers"] == {k:v for k,v in batch.to_dict().items() if k.startswith("gpu_")},"CONTACT_BUFFER_CONFIG")
        names,keys = m["joint_names"],m["key_names"]
        require(len(names)==69 and set(names)==set(EXPECTED_BODY_JOINTS)|set(EXPECTED_DEX5_JOINTS),"JOINT_MAPPING")
        require(keys==[spec.name for spec in standard_keyboard_layout()],"KEY_MAPPING")
        control = control_spec(DEFAULT_URDF,physical)
        require(m["control"]==json.loads(control.to_json()) and m["joint_indices"]==[names.index(name) for name in control.joint_names],"ACTION_MAPPING")
        require(m["initialization"]==direct_joint_initialization(DEFAULT_URDF),"INITIALIZATION_SOURCE")
        for i in range(count):
            prefix=f"/World/envs/env_{i}"
            require(m["robot_paths"][i].startswith(prefix+"/") and m["keyboard_paths"][i].startswith(prefix+"/"),"TENSOR_ROW_MAPPING")
            require(m["collision_groups"][i]==dict(included=[prefix],allowed=[f"/World/collisions/group{i}","/World/collisions/global_group"]),"COLLISION_PARTITION")
        origins=np.asarray(m["origins"])
        require(origins.shape==(count,3) and np.isfinite(origins).all() and (origins[:,2]==0).all(),"INVALID_ORIGINS")
        require(all(np.linalg.norm(origins[i]-origins[j])>=batch.spacing_m-1e-6 for i in range(count) for j in range(i)),"OVERLAPPING_ENVIRONMENTS")
        chunks=[load(directory/entry["path"]) for entry in m["raw_chunks"]]
        require([len(c["global_tick"]) for c in chunks]==[e["samples"] for e in m["raw_chunks"]],"RAW_CHUNK_COUNT")
        require(all(set(c)==set(RAW_FIELDS)|{"global_tick","generations","local_ticks"} for c in chunks),"RAW_FIELDS")
        raw={key:np.concatenate([c[key] for c in chunks]) for key in chunks[0]}
        expected_count=physical.settling_steps+(batch.control_steps+1)*physical.decimation
        require(m["raw_sample_count"]==expected_count and np.array_equal(raw["global_tick"],np.arange(1,expected_count+1)),"SAMPLE_GAP")
        shapes=dict(q=(69,),v=(69,),targets=(69,),key_q=(74,),key_targets=(74,),forces=(74,3),key_xyz=(74,3),tip=(3,),pelvis=(3,),torso=(4,))
        for key,shape in shapes.items():
            require(raw[key].shape==(expected_count,count,*shape) and np.isfinite(raw[key]).all(),"INVALID_RAW_ARRAY: "+key)
        require(raw["generations"].shape==(expected_count,count) and raw["local_ticks"].shape==(expected_count,count),"ROW_CLOCK_SHAPE")
        initial_targets=np.asarray([direct_joint_initialization(DEFAULT_URDF)[name] for name in names])
        frozen_indices=[i for i,name in enumerate(names) if name not in physical.enabled_joints]
        require((np.abs(raw["targets"][:physical.settling_steps]-initial_targets)<1e-6).all()
                and (np.abs(raw["targets"][:,:,frozen_indices]-initial_targets[frozen_indices])<1e-6).all(),"INITIALIZATION_OR_FROZEN_JOINT_COMMAND")
        require((raw["generations"][:physical.settling_steps]==-1).all()
                and (raw["local_ticks"][:physical.settling_steps]==-1).all(),"WARMUP_CLOCKS")
        require((raw["key_targets"]==0).all(),"COMMANDED_KEYCAP")
        require(len(m["resets"])==2 and m["resets"][0]["rows"]==list(range(count)) and m["resets"][1]["rows"]==[0],"RESET_SCHEDULE")
        require([r["global_tick"] for r in m["resets"]]==[physical.settling_steps,physical.settling_steps+physical.decimation],"RESET_CLOCK_ADVANCED")
        settled=load(directory/"settled.npz")
        for reset in m["resets"]:
            before,after=load(directory/reset["before"]),load(directory/reset["after"])
            errors=check_reset_isolation(before,after,reset["rows"],reset["state_before"],reset["state_after"])
            require(not errors,",".join(errors))
            for index in reset["rows"]:
                require(np.array_equal(after["q"][index],settled["q"][index]) and np.array_equal(after["v"][index],settled["v"][index])
                        and np.array_equal(after["targets"][index],settled["targets"][index]),"RESET_CACHE_MISMATCH")
            # The before snapshot must match the immediately preceding physics
            # sample, not just another arbitrary unchanged array.
            preceding=reset["global_tick"]-1
            require(all(np.array_equal(before[key],raw[key][preceding]) for key in RAW_FIELDS),"RESET_BEFORE_UNBOUND")
        require(len(m["rejections"])==1 and m["rejections"][0]["code"]=="CONTROL_OWNERSHIP"
                and m["rejections"][0]["state_before"]==m["rejections"][0]["state_after"],"STALE_ACTION_MUTATED_BATCH")
        from ....learning.physical import CoordinateLimit
        import xml.etree.ElementTree as ET
        joints={j.get("name"):j for j in ET.parse(DEFAULT_URDF).getroot().findall("joint")}
        limits=tuple(CoordinateLimit(name,"rad",float(joints[name].find("limit").get("lower")),float(joints[name].find("limit").get("upper")),
                                    min(float(joints[name].find("limit").get("velocity")),physical.maximum_joint_speed_rad_s)) for name in names)
        require(m["coordinate_limits"]==[json.loads(limit.to_json()) for limit in limits],"SAFETY_LIMITS_CHANGED")
        # Vector checks are independent of the live row-wise safety monitor.
        lower=np.asarray([l.lower for l in limits]); upper=np.asarray([l.upper for l in limits]); speed=np.asarray([l.maximum_speed for l in limits])
        require(((raw["q"]>=lower-.03)&(raw["q"]<=upper+.03)).all() and (np.abs(raw["v"])<=speed+.05).all(),"JOINT_SAFETY")
        require((np.linalg.norm(raw["forces"],axis=-1)<=physical.maximum_contact_force_n).all(),"CONTACT_SAFETY")
        require(((raw["key_q"]>=-.0045)&(raw["key_q"]<=.0005)).all(),"KEY_TRAVEL_LIMIT")
        require((np.linalg.norm(raw["pelvis"]-(origins+np.asarray((0,0,.625))),axis=-1)<=physical.support_tolerance_m).all(),"SUPPORT_SAFETY")
        quat=raw["torso"]
        require((np.abs(np.sum(quat*quat,axis=-1)-1)<=.02).all() and (1-2*(quat[:,:,1]**2+quat[:,:,2]**2)>=math.cos(.25)).all(),"POSTURE_SAFETY")
        centers=np.asarray([keyboard_key_center(spec,operator_row_x=physical.keyboard_operator_row_x_m,
            left_origin_y=physical.keyboard_left_origin_y_m,top_z=physical.key_center_z_m) for spec in standard_keyboard_layout()])
        expected_xyz=centers[None,None,:,:]+origins[None,:,None,:]
        difference=raw["key_xyz"]-expected_xyz
        difference[:,:,:,2]-=raw["key_q"]
        require((np.linalg.norm(difference,axis=-1)<1e-5).all(),"KEYBOARD_POSE_OR_ROW_MISMATCH")
        episodes=[read_gzip(directory/e["path"]) for e in m["episodes"]]
        require(len(episodes)==count+1 and len({(e["index"],e["generation"]) for e in episodes})==count+1,"EPISODE_SET")
        require({(e["index"],e["generation"]) for e in episodes}=={(i,0) for i in range(count)}|{(0,1)},"EPISODE_IDENTITIES")
        require(len({e["owner_token"] for e in episodes})==count+1,"SHARED_OWNERSHIP")
        for item in episodes:
            index,generation=item["index"],item["generation"]
            feedback=FeedbackEpisode.from_dict(item["feedback"])
            reset_spec=ResetSpec.from_dict(item["reset"])
            scope=EpisodeScope(f"isaac-batch-{index}",f"env{index}-generation{generation}","isaac_sim_seconds")
            require(reset_spec.episode_id==scope.episode_id and reset_spec.seed==row_seed(batch.seed,index),"ROW_SEED_OR_SCOPE")
            profile=diagnostic_profile(index)
            expected_spec=FeedbackSpec(input_plan=DigitalInputPlan((0,)) if profile=="software_drop" else DigitalInputPlan(),
                readout_faults=(ReadoutFault(1,missing=True),) if profile=="missing_readout" else ())
            require(FeedbackSpec.from_dict(item["feedback_spec"])==expected_spec,"ROW_FEEDBACK_CONFIG")
            evaluation=evaluate_feedback(feedback,expected=expected_spec,scope=scope)
            require(evaluation["isolation_passed"],f"row {index} feedback: {evaluation['findings']}")
            require(feedback.source=="isaac_physx_feedback" and feedback.physical_episode_sha256 is None,"FEEDBACK_SOURCE")
            selected=np.flatnonzero(raw["generations"][:,index]==generation)
            start=item["start_global_tick"]; end=item["end_global_tick"]
            require(np.array_equal(raw["global_tick"][selected],np.arange(start+1,end+1))
                    and np.array_equal(raw["local_ticks"][selected,index],np.arange(1,end-start+1)),"ROW_SAMPLE_GAP")
            initial=load(directory/m["resets"][generation if index==0 else 0]["after"])
            arrays={key:np.concatenate((initial[key][None,index],raw[key][selected,index])) for key in RAW_FIELDS}
            require(abs(feedback.end_time_s-(end-start)*physical.physics_dt_s)<1e-9,"ROW_END_TIME")
            actions=tuple(AppliedAction.from_dict(a) for a in item["actions"])
            guard=JointTargetGuard(control)
            for seq,action in enumerate(actions):
                t=seq*control.control_dt_s
                tick=seq*physical.decimation
                previous=tuple(float(v) for v in arrays["targets"][tick,m["joint_indices"]])
                require(abs(action.time_s-t)<1e-9 and action.previous_targets_rad==previous,"ACTION_SEQUENCE")
                wanted=guard.apply(previous,action.action,time_s=t,owner_token=item["owner_token"],expected_sequence=seq)
                require(action.applied_targets_rad==wanted and action.failure_code is None,"ACTION_GUARD_BYPASS")
                targets=arrays["targets"][tick].copy(); targets[m["joint_indices"]]=wanted
                require(np.all(np.abs(arrays["targets"][tick+1:tick+physical.decimation+1]-targets)<1e-6),"UNLOGGED_COMMAND")
            require(len(actions)*physical.decimation==end-start,"ACTION_COVERAGE")
            frames=[]
            for tick in range(len(arrays["q"])):
                now=tick*physical.physics_dt_s
                stamp=RecordStamp(f"{scope.env_id}:{scope.episode_id}:physx:{tick}",scope.env_id,scope.episode_id,tick,
                                  scope.clock_domain,now,now,"isaac.batch.physx")
                states=tuple(PhysicalKeyRecord(key,max(0.,-float(arrays["key_q"][tick,j])),
                    math.sqrt(sum(float(v)**2 for v in arrays["forces"][tick,j]))>.01,abs(float(arrays["forces"][tick,j,2]))) for j,key in enumerate(keys))
                frames.append(ObservationFrame(stamp,states,(),False))
            task=TaskSpec("vf-evidence/1","vf04-single-a","a","",(KeyBinding("a","a"),),"backspace",tuple(keys),
                          control.joint_names,"left","fixed_pelvis_seated_pd",("joint_state","key_travel","key_contact"),3.,3,.9)
            physical_episode=EpisodeRecord("vf-evidence/1",scope.env_id,scope.episode_id,scope.clock_domain,task.sha256(),
                RunManifest("vf04","isaac_sim_5_1","isaac_physx_batch_v1","source-manifest",sha(directory/"manifest.json"),reset_spec.seed,
                            "cached audited direct-joint settling"),0.,feedback.end_time_s,feedback.termination,tuple(frames),feedback.events,(),(),())
            cancelled=index==0 and generation==0
            transition_findings=[]
            if cancelled:
                # This generation ends before the release dwell period. It
                # makes no completed-primitive claim; still reject any press.
                require(not feedback.events and (-arrays["key_q"] < EvaluationPolicy().press_threshold_m).all(),"CANCELLED_GENERATION_PRESSED_KEY")
                cycles=0
            else:
                cycles=check_key_transitions(task,physical_episode,EvaluationPolicy(maximum_sample_gap_s=physical.physics_dt_s*1.01,
                    maximum_contact_force_n=physical.maximum_contact_force_n),lambda code,detail,ref="episode":transition_findings.append(code))
            expected_cycles=0 if cancelled or profile=="idle" else 1
            require(not transition_findings and cycles==expected_cycles,f"row {index}/{generation} physical transitions: {transition_findings}, cycles={cycles}")
            require(len([e for e in feedback.events if e.event_type=="keydown"])==expected_cycles,"EXTRA_OR_CROSS_ROW_EVENTS")
            for publication in feedback.publications:
                base=publication.base
                tick=round(base.time_s/physical.physics_dt_s)
                require(abs(tick*physical.physics_dt_s-base.time_s)<1e-9 and 0<=tick<len(frames),"PUBLICATION_TIME")
                require(tuple(base.keys)==frames[tick].keys and [j.name for j in base.joints]==list(control.joint_names),"OBSERVATION_BINDING")
                prior=[a for a in actions if a.time_s<base.time_s-1e-9]
                previous=tuple(a-b for a,b in zip(prior[-1].applied_targets_rad,prior[-1].previous_targets_rad,strict=True)) if prior else (0.,)*len(control.joint_names)
                require(base.target_key=="a" and base.previous_delta_rad==previous,"POLICY_ACTION_OR_GOAL_BINDING")
                for joint,j in zip(base.joints,m["joint_indices"],strict=True):
                    require((joint.position_rad,joint.velocity_rad_s,joint.commanded_target_rad)==
                            tuple(float(arrays[k][tick,j]) for k in ("q","v","targets")),"JOINT_OBSERVATION_BINDING")
            expected_success=not cancelled and profile=="press"
            require(evaluation["text_task_passed"]==expected_success,"UNEXPECTED_TEXT_RESULT")
            require(item["physical_release_claimed"] is False,"ADMINISTRATIVE_RELEASE_CLAIM")
            if cancelled:
                require(feedback.termination=="cancelled" and len(feedback.cancelled_request_ids)==1 and not feedback.deliveries,"RESET_LEAKED_PENDING_FEEDBACK")
            else:
                require(feedback.termination=="finish_requested","UNEXPECTED_TERMINATION")
            report["rows"].append(dict(index=index,generation=generation,profile=profile,primitive_cycles=cycles,
                text_task_passed=evaluation["text_task_passed"],isolation_passed=True,task_failures=evaluation["task_failures"],
                maximum_force_n=float(np.linalg.norm(arrays["forces"],axis=-1).max()),
                maximum_key_travel_m=float(-arrays["key_q"].min()),events=len(feedback.events)))
        benchmark=m["benchmark"]
        require(benchmark["measured_control_steps"]==batch.control_steps and benchmark["cumulative_environment_transitions"]==batch.control_steps*count
                and benchmark["measured_loop_s"]>0 and benchmark["final_chunk_flush_s"]>=0,"INVALID_BENCHMARK")
        expected_rate=batch.control_steps*count/(benchmark["measured_loop_s"]+benchmark["final_chunk_flush_s"])
        require(abs(benchmark["transitions_per_s"]-expected_rate)<1e-9,"THROUGHPUT_DENOMINATOR")
        report.update(accepted=True,benchmark=benchmark,raw_sample_count=expected_count,reset_isolation_passed=True,
                      raw_environment_samples=expected_count*count,source_sha256=hashlib.sha256(json.dumps(m["sources"],sort_keys=True).encode()).hexdigest(),
                      manifest_sha256=sha(directory/"manifest.json"),recording_bytes=sum(p.stat().st_size for p in directory.rglob("*") if p.is_file()),
                      verifier_sha256=sha(Path(__file__)))
    except (OSError,ValueError,KeyError,TypeError,IndexError) as exc:
        report["findings"].append(str(exc))
    return report
