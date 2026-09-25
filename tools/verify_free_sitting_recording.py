#!/usr/bin/env python3
"""Independently re-evaluate saved PhysX observations after the controller exits."""
import argparse
import hashlib
import json
from pathlib import Path

from vskill_os.adapters.isaac.balance_policy import POLICY_SHA256
from vskill_os.simulation.dex5_assets import EXPECTED_BODY_JOINTS, EXPECTED_DEX5_JOINTS
from vskill_os.simulation.free_sitting import SittingSample, verify_free_sitting


def verify(path: Path) -> dict:
    report=json.loads(path.read_text())
    result={'report':str(path),'report_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'succeeded':False,'free_balance_sitting_verified':False}
    if report.get('diagnostic_seated_reset'):
        return {**result,'failure':'DIAGNOSTIC_RESET_NOT_CONTINUOUS'}
    required={
        'robot_fixed':False, 'external_support':False, 'self_collisions_enabled':True,
        'root_state_writes_during_motion':0, 'initial_root_state_writes':1,
        'policy_sha256':POLICY_SHA256,
    }
    for key,value in required.items():
        if report.get(key)!=value:
            return {**result,'failure':'PREFLIGHT_MISMATCH','field':key}
    if (len(report.get('joint_names',()))!=69 or set(report.get('joint_names',()))!=set(EXPECTED_BODY_JOINTS+EXPECTED_DEX5_JOINTS)
            or report.get('chair',{}).get('passive_joint_count')!=10 or report.get('chair',{}).get('body_count')!=11
            or not report.get('runtime',{}).get('isaac_sim','').startswith('5.1.0')):
        return {**result,'failure':'ASSET_OR_RUNTIME_MISMATCH'}
    mimic=report.get('mimic_preflight',{})
    if mimic.get('mimic_joint_count')!=8 or mimic.get('hard_coupling_verified') is not True:
        return {**result,'failure':'MIMIC_PREFLIGHT_MISMATCH'}
    if any('fixture' in name for name in report['joint_names']):
        return {**result,'failure':'EXTERNAL_SUPPORT'}
    # Deserialized data is checked by the same independent verifier used online.
    samples=[SittingSample(**row) for row in report.get('samples',())]
    if any(abs(s.seat_top_local_m-report.get('chair',{}).get('seat_top_m',.4775))>1e-6 for s in samples):
        return {**result,'failure':'SEAT_GEOMETRY_MISMATCH'}
    verdict=verify_free_sitting(samples,robot_mass_kg=report['robot_mass_kg'])
    for field in ('error','video_error','recording_error'):
        if report.get(field):
            verdict.update(succeeded=False,free_balance_sitting_verified=False,failure=report[field])
    if verdict['succeeded'] and abs(samples[-1].time_s-report['request']['duration'])>.031:
        verdict.update(succeeded=False,free_balance_sitting_verified=False,failure='TRUNCATED_RUN')
    result.update(verdict)
    result['controller_status']=report.get('status')
    result['evidence_note']='Checks record consistency; does not authenticate fabricated sensor records.'
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('reports',nargs='+',type=Path)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    results=[]
    for path in args.reports:
        try:results.append(verify(path))
        except (KeyError,ValueError,TypeError,OSError) as exc:
            results.append({'report':str(path),'succeeded':False,'failure':f'INVALID_RECORD: {exc}'})
    summary={'succeeded':all(row['succeeded'] for row in results),'runs':results}
    content=json.dumps(summary,indent=2)+'\n'
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(content)
    print(content,end='')
    return 0 if summary['succeeded'] else 1


if __name__=='__main__':raise SystemExit(main())
