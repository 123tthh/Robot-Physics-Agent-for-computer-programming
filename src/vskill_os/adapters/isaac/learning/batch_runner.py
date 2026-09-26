"""Run each requested scale in a bounded worker and measure non-Torch resources."""

import argparse
from dataclasses import replace
from datetime import datetime,timezone
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

from ....learning.batch import BatchConfig
from .batch_verify import verify_batch
from .config import SingleHandConfig
from .single_hand_env import ROOT,write_json


def gpu_sample(pid):
    """Device/process allocation, including PhysX; unavailable is not zero."""
    result={"process_mib":None,"device_mib":None,"device_total_mib":None}
    try:
        apps=subprocess.run(["nvidia-smi","--query-compute-apps=pid,used_gpu_memory","--format=csv,noheader,nounits"],
                            capture_output=True,text=True,timeout=4)
        if apps.returncode==0:
            for line in apps.stdout.splitlines():
                parts=[p.strip() for p in line.split(",")]
                if len(parts)==2 and parts[0]==str(pid):
                    result["process_mib"]=float(parts[1])
        device=subprocess.run(["nvidia-smi","--query-gpu=memory.used,memory.total","--format=csv,noheader,nounits"],
                              capture_output=True,text=True,timeout=4)
        if device.returncode==0:
            used,total=device.stdout.splitlines()[0].split(",")
            result.update(device_mib=float(used),device_total_mib=float(total))
    except (OSError,ValueError,IndexError,subprocess.TimeoutExpired):
        pass
    return result


def run_worker(command,directory,timeout):
    log_path=directory.with_suffix(".log")
    started=time.perf_counter()
    record=dict(returncode=None,timed_out=False,gpu_samples=[],log=str(log_path.resolve()))
    with log_path.open("w") as stream:
        child=subprocess.Popen(command,stdout=stream,stderr=subprocess.STDOUT,start_new_session=True)
        try:
            while child.poll() is None:
                if time.perf_counter()-started>timeout:
                    record["timed_out"]=True
                    break
                record["gpu_samples"].append(dict(elapsed_s=time.perf_counter()-started,**gpu_sample(child.pid)))
                try:
                    child.wait(timeout=2.)
                except subprocess.TimeoutExpired:
                    pass
        except KeyboardInterrupt:
            record["interrupted"]=True
        finally:
            if child.poll() is None:
                os.killpg(child.pid,signal.SIGTERM)
                try:
                    child.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid,signal.SIGKILL)
                    child.wait(timeout=8)
            record["returncode"]=child.returncode
    record["wall_s"]=time.perf_counter()-started
    for field in ("process_mib","device_mib"):
        values=[s[field] for s in record["gpu_samples"] if s[field] is not None]
        record["peak_sampled_"+field]=max(values) if values else None
    log=log_path.read_text(errors="replace")
    pattern=re.compile(r"overflow|buffer[^\n]*(?:too small|exceeded)|increase[^\n]*(?:capacity|buffer)|out of memory|allocation failed|contact[^\n]*dropped",re.I)
    record["buffer_or_allocation_errors"]=[line for line in log.splitlines() if pattern.search(line)]
    write_json(directory/"process.json",record)
    return record


def evaluate_scale(directory,batch,physical,process):
    if process.get("returncode")!=0 or process.get("timed_out") is not False or process.get("buffer_or_allocation_errors"):
        return dict(num_envs=batch.num_envs,accepted=False,findings=["worker failure, timeout, or contact/allocation error"],process=process)
    result=verify_batch(directory,batch=batch,physical=physical)
    result["process"]=process
    if process.get("peak_sampled_process_mib") is None:
        result["accepted"]=False
        result["findings"].append("process GPU allocation measurement unavailable")
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path,default=ROOT/"artifacts/verification_feedback/vf01/batch")
    parser.add_argument("--scales",type=int,nargs="+",default=[1,16,64])
    parser.add_argument("--batch-config",type=Path,default=ROOT/"configs/learning/verification_feedback/batch_v1.json")
    parser.add_argument("--physics-config",type=Path,default=ROOT/"configs/learning/verification_feedback/single_hand_physics_v1.json")
    parser.add_argument("--timeout-s",type=float,default=900.)
    parser.add_argument("--verify-run",type=Path)
    parser.add_argument("--report",type=Path)
    args=parser.parse_args(argv)
    if (not math.isfinite(args.timeout_s) or args.timeout_s<=0 or sorted(set(args.scales))!=args.scales
            or not set(args.scales)<={1,16,64}):
        parser.error("scales must be unique increasing members of 1/16/64 and timeout positive")
    base,physical=BatchConfig.read(args.batch_config),SingleHandConfig.read(args.physics_config)
    directory=args.verify_run.resolve() if args.verify_run else (args.output/datetime.now(timezone.utc).strftime("run-%Y%m%dT%H%M%S-%f")).resolve()
    directory.mkdir(parents=True,exist_ok=args.verify_run is not None)
    report=dict(schema_version="vf-batch-suite/1",task="VF-01-T04",recording=str(directory),scales=args.scales,
                accepted=False,results=[],camera_verified=False,desktop_verified=False,learned_policy=False)
    print("VF04_RUN "+str(directory),flush=True)
    for count in args.scales:
        target=directory/f"n{count}"
        if args.verify_run:
            process=json.loads((target/"process.json").read_text())
        else:
            command=[sys.executable,"-m","vskill_os.adapters.isaac.learning.batch_smoke","--output",str(target),"--num-envs",str(count),
                     "--batch-config",str(args.batch_config.resolve()),"--physics-config",str(args.physics_config.resolve())]
            print(f"VF04_START num_envs={count}",flush=True)
            process=run_worker(command,target,args.timeout_s)
        result=evaluate_scale(target,replace(base,num_envs=count),physical,process)
        report["results"].append(result)
        write_json(target/"evaluation.json",result)
        write_json(directory/"suite.json",report)
        print("VF04_RESULT "+json.dumps(dict(num_envs=count,accepted=result["accepted"],findings=result["findings"],
                    transitions_per_s=result.get("benchmark",{}).get("transitions_per_s"),
                    peak_sampled_process_mib=process.get("peak_sampled_process_mib"))),flush=True)
        if process.get("interrupted"):
            break
    report["accepted"]=all(r["accepted"] for r in report["results"])
    report["largest_validated_num_envs"]=max((r["num_envs"] for r in report["results"] if r["accepted"]),default=None)
    report["all_required_scales_evaluated"]=[r["num_envs"] for r in report["results"]]==[1,16,64]
    if report["results"] and report["results"][0]["accepted"]:
        baseline=report["results"][0]["benchmark"]["transitions_per_s"]
        for result in report["results"]:
            if result["accepted"]:
                result["speedup_vs_first_scale"]=result["benchmark"]["transitions_per_s"]/baseline
    write_json(directory/"suite.json",report)
    if args.report:
        write_json(args.report,report)
    print(json.dumps(dict(accepted=report["accepted"],report=str(directory/"suite.json"))),flush=True)
    return 0 if report["accepted"] else 1


if __name__=="__main__":
    raise SystemExit(main())
