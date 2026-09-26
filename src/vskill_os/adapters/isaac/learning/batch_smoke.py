"""Mixed press/idle batches with a pending-feedback subset reset and benchmark."""

import argparse
from dataclasses import asdict, replace
import json
from pathlib import Path
import resource
import time
import traceback

from ....learning.batch import BatchConfig, diagnostic_profile
from ....learning.environment import ActionRejected, JointTargetAction
from .batch_recording import sha
from .config import SingleHandConfig
from .feedback_env import FEEDBACK_SOURCES
from .provenance import COLLECTOR_FILES
from .single_hand_env import ROOT, write_json

BATCH_SOURCES = tuple(dict.fromkeys((*COLLECTOR_FILES,*FEEDBACK_SOURCES,
    "src/vskill_os/learning/batch.py",
    "src/vskill_os/adapters/isaac/learning/batch_scene.py",
    "src/vskill_os/adapters/isaac/learning/batch_env.py",
    "src/vskill_os/adapters/isaac/learning/batch_recording.py",
    "src/vskill_os/adapters/isaac/learning/batch_smoke.py")))


def run(scene,env):
    env.settle()
    initial = env.reset_rows(list(range(scene.batch.num_envs)))
    observations = [initial[i][0] for i in range(scene.batch.num_envs)]
    metadata = [initial[i][1] for i in range(scene.batch.num_envs)]
    sequences = [0]*scene.batch.num_envs
    dt = scene.control.control_dt_s
    finger = scene.control.joint_names.index("left_hand_index_1_joint")
    for i in range(scene.batch.num_envs):
        env.request(i,metadata[i].owner_token)
        env.lanes[i].state.policy_state = (float(i),env.lanes[i].state.rng.random())

    def actions(idle=False):
        result = []
        for i,observation in enumerate(observations):
            target = .26 if not idle and diagnostic_profile(i) != "idle" and observation.time_s < 1.05-1e-9 else 0.
            delta = [0.]*len(scene.ids)
            delta[finger] = target-observation.joints[finger].commanded_target_rad
            result.append(JointTargetAction(scene.control.joint_names,tuple(delta),dt,observation.time_s+dt,
                                             metadata[i].owner_token,sequences[i]))
        return result

    # Advance one control step with a pending request, then reset only row 0.
    observations = list(env.step(actions(idle=True)))
    sequences = [1]*scene.batch.num_envs
    old_owner = metadata[0].owner_token
    reset = env.reset_rows([0])
    observations[0],metadata[0] = reset[0]
    sequences[0] = 0
    before = env.state()
    bad = actions(idle=True)
    bad[0] = replace(bad[0],owner_token=old_owner)
    try:
        env.step(bad)
    except ActionRejected as exc:
        if exc.code != "CONTROL_OWNERSHIP":
            raise
        env.rejections.append(dict(code=exc.code,global_tick=env.global_tick,state_before=before,state_after=env.state()))
    else:
        raise RuntimeError("stale action owner was accepted after subset reset")
    try:
        env.request(0,old_owner)
    except RuntimeError:
        pass
    else:
        raise RuntimeError("stale feedback owner was accepted")
    env.request(0,metadata[0].owner_token)
    if observations[0].feedback or observations[0].budget.accepted_requests:
        raise RuntimeError("reset published previous-generation feedback or budget")
    final = [False]*scene.batch.num_envs
    scene.torch.cuda.synchronize()
    started = time.perf_counter()
    timing_before = dict(env.timings)
    latencies = []
    for step in range(scene.batch.control_steps):
        began = time.perf_counter()
        observations = list(env.step(actions()))
        sequences = [sequence+1 for sequence in sequences]
        for index,observation in enumerate(observations):
            if observation.time_s >= 2.1-1e-9 and not final[index]:
                receipt = env.request(index,metadata[index].owner_token,"final")
                if not receipt.accepted:
                    raise RuntimeError("frozen diagnostic exceeded verification budget")
                final[index] = True
        latencies.append(time.perf_counter()-began)
        if step in (0,35,71,107,143):
            print(f"VF04_PROGRESS envs={scene.batch.num_envs} control_steps={step+1}",flush=True)
    scene.torch.cuda.synchronize()
    elapsed = time.perf_counter()-started
    flush_start = time.perf_counter()
    env.recorder.flush()
    flush_s = time.perf_counter()-flush_start
    env.close("finish_requested")
    ordered = sorted(latencies)
    return dict(measured_control_steps=scene.batch.control_steps,
                cumulative_environment_transitions=scene.batch.control_steps*scene.batch.num_envs,
                physics_substeps=scene.batch.control_steps*scene.config.decimation,
                measured_loop_s=elapsed,final_chunk_flush_s=flush_s,
                transitions_per_s=scene.batch.control_steps*scene.batch.num_envs/(elapsed+flush_s),
                batch_steps_per_s=scene.batch.control_steps/(elapsed+flush_s),
                latency_p50_s=ordered[len(ordered)//2],latency_p95_s=ordered[min(len(ordered)-1,int(len(ordered)*.95))],
                simulated_seconds_per_env=scene.batch.control_steps*dt,
                stage_seconds={key:env.timings[key]-timing_before[key] for key in timing_before},
                evidence_mode="all physics substeps, all rows; safety and feedback enabled; compressed raw chunks",
                episode_serialization_in_throughput=False,
                torch_peak_allocated_bytes=scene.torch.cuda.max_memory_allocated(),
                torch_peak_reserved_bytes=scene.torch.cuda.max_memory_reserved(),
                process_peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--num-envs",type=int,choices=(1,16,64),required=True)
    parser.add_argument("--batch-config",type=Path,required=True)
    parser.add_argument("--physics-config",type=Path,required=True)
    args = parser.parse_args(argv)
    if args.output.exists():
        raise ValueError("never overwrite a batch recording")
    sources = {name:sha(ROOT/name) for name in BATCH_SOURCES}
    batch = replace(BatchConfig.read(args.batch_config),num_envs=args.num_envs)
    physical = SingleHandConfig.read(args.physics_config)
    from isaaclab.app import AppLauncher
    app = AppLauncher(headless=True,enable_cameras=False,device="cuda:0").app
    scene = env = None
    started = time.perf_counter()
    try:
        from .batch_scene import BatchPhysicsScene
        from .batch_env import BatchKeyboardEnv
        scene = BatchPhysicsScene(args.output,batch,physical)
        env = BatchKeyboardEnv(scene)
        benchmark = run(scene,env)
        if sources != {name:sha(ROOT/name) for name in BATCH_SOURCES}:
            raise RuntimeError("batch collector source changed during capture")
        batch.write(args.output/"batch_config.json")
        physical.write(args.output/"physics_config.json")
        manifest = dict(schema_version="vf-batch-bundle/1",sources=sources,runtime=scene.runtime,
            control=scene.control.to_dict(),coordinate_limits=[item.to_dict() for item in scene.limits],
            joint_names=scene.robot.joint_names,key_names=scene.key_names,joint_indices=scene.ids,
            origins=scene.origins.cpu().tolist(),robot_paths=scene.robot_paths,keyboard_paths=scene.keyboard_paths,
            collision_groups=scene.collision_groups,initialization=scene.initial_positions,
            reset_method="cached per-row state after independent-safe settling; no physics advancement on subset reset",
            raw_chunks=env.recorder.files,raw_sample_count=env.recorder.count,
            episodes=env.records,resets=env.reset_records,rejections=env.rejections,benchmark=benchmark,
            worker_collection_s=time.perf_counter()-started,
            assets={str(scene.urdf.relative_to(ROOT)):sha(scene.urdf),**{
                str(path.relative_to(ROOT)):sha(path) for path in sorted((ROOT/"artifacts/verification_feedback/assets/batch_usd").rglob("*.usd"))}},
            files={str(path.relative_to(args.output)):sha(path) for path in sorted(args.output.iterdir()) if path.is_file()},
            learned_policy=False,camera_verified=False,desktop_verified=False)
        write_json(args.output/"manifest.json",manifest)
        print("VF04_COLLECTED "+json.dumps(dict(num_envs=batch.num_envs,benchmark=benchmark)),flush=True)
    except Exception as exc:
        write_json(args.output/"failure.json",dict(error=str(exc),traceback=traceback.format_exc()))
        traceback.print_exc()
        if env is not None:
            env.close()
    finally:
        app.close(wait_for_replicator=False,skip_cleanup=True)


if __name__ == "__main__":
    main()
