"""Independently check one uninterrupted free sitting + ten-finger recording."""
import argparse
from dataclasses import replace
import hashlib
import json
from pathlib import Path

from verify_free_sitting_recording import verify as verify_sitting
from vskill_os.simulation.dex5_typing import (
    DEFAULT_DEMO_TEXT, Dex5TypingPlanner, Dex5TypingVerifier,
    Dex5Observation, Dex5Contact, Dex5Phase,
)


def verify(path, *, require_all_ten=True):
    report=json.loads(path.read_text()); typing=report['typing']; raw=typing['plan']
    plan=replace(Dex5TypingPlanner().plan(raw['text'],support_mode='seat_contact',
        initial_text=raw['initial_text'],phase_timeout_s=raw['stages'][0]['maximum_duration_s']),
        maximum_setup_duration_s=raw['maximum_setup_duration_s'])
    if json.loads(json.dumps(plan.to_dict()))!=raw:raise ValueError('typing contract mismatch')
    observations=[]
    for row in typing['observations']:
        row=dict(row);row['phase']=Dex5Phase(row['phase'])
        row['contacts']=tuple(Dex5Contact(**c) for c in row['contacts'])
        for field in ('events','key_travel_m','fingertip_clearance_m'):
            row[field]=tuple(tuple(v) for v in row[field])
        row['keys_down']=tuple(row['keys_down'])
        observations.append(Dex5Observation(**row))
    verdict=Dex5TypingVerifier().verify(plan,observations,capability='isaac_sim_5_1')
    sitting=verify_sitting(path)
    event_stream=[(e['event_type'],e['key'],e['monotonic_time']) for e in typing['events']]
    events=[(kind,key,o.time_s) for o in observations for kind,key in o.events]
    gates={'free_sitting_and_final_stability':sitting['succeeded'],
           'typing':verdict.succeeded,
           'one_continuous_physics_timeline':bool(observations) and observations[0].time_s>18 and observations[-1].time_s<=report['samples'][-1]['time_s']+.021,
           'all_events_preserved':event_stream==events,'finished_without_error':report['status']=='passed' and not report.get('error'),
           'real_chair_support':all(o.seat_support_force_n>=5 and not o.base_constraint_active for o in observations)}
    if require_all_ten:
        gates.update(all_ten_fingers=len(verdict.executors_observed)==10,default_demo_text=plan.text==DEFAULT_DEMO_TEXT)
    return {'succeeded':all(gates.values()),'gates':gates,'sitting':sitting,'typing':verdict.to_dict(),
            'report':str(path),'report_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'camera_ocr_verified':False,'real_robot_verified':False}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('report',type=Path);p.add_argument('--output',type=Path)
    args=p.parse_args()
    try:result=verify(args.report)
    except (KeyError,ValueError,TypeError,OSError) as exc:result={'succeeded':False,'error':str(exc)}
    content=json.dumps(result,ensure_ascii=False,indent=2)+'\n'
    if args.output:args.output.write_text(content)
    print(content,end='');return 0 if result['succeeded'] else 1


if __name__=='__main__':raise SystemExit(main())
