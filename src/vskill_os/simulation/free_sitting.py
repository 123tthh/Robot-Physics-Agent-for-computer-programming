"""Independent raw-evidence checks for unsupported stand-to-sit transitions."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math

SEAT_CONTACT_BODIES = ('pelvis', *(f'{side}_hip_{axis}_link'
                       for side in ('left','right') for axis in ('pitch','roll','yaw')))

SITTING_CONTRACT = {
    'name': 'free_balance_sit', 'version': '0.1.0', 'level': 'L2',
    'preconditions': ['audited_free_robot_and_passive_chair', 'standing_on_both_feet', 'initial_collision_clearance'],
    'postconditions': ['seat_bears_weight', 'both_feet_supported', 'two_seconds_settled', 'no_external_support'],
    'resources': ['legs', 'waist', 'both_arms', 'passive_chair'],
    'maximum_duration_s': 60.,
    'failure_codes': ['INVALID_EVIDENCE', 'EXTERNAL_SUPPORT', 'UNSAFE_TILT', 'FALL',
                      'EXCESS_CONTACT_FORCE', 'EFFORT_LIMIT', 'UNPLANNED_CONTACT',
                      'STANDING_PRECONDITION', 'INSUFFICIENT_SEAT_SUPPORT',
                      'FOOT_CONTACT_LOST', 'NOT_SETTLED', 'SEAT_EDGE', 'TIMEOUT',
                      'CONTACT_EVIDENCE_MISMATCH', 'MISSING_INITIAL_STATE', 'MISSING_EVIDENCE',
                      'SAMPLE_GAP', 'UNSUPPORTED_SOURCE', 'NO_SIT_TRANSITION',
                      'STABLE_WINDOW_MISSING', 'SEATED_TILT', 'SEAT_CONTACT_LOST'],
}


@dataclass(frozen=True)
class SittingSample:
    time_s: float
    phase: str
    pelvis_xyz_m: tuple[float,float,float]
    pelvis_speed_mps: float
    torso_tilt_rad: float
    chair_xyz_m: tuple[float,float,float]
    chair_speed_mps: float
    chair_tilt_rad: float
    seat_force_n: float
    left_foot_force_n: float
    right_foot_force_n: float
    maximum_contact_force_n: float
    maximum_joint_effort_ratio: float
    foot_slip_mps: float
    seat_contacts: tuple[dict,...]
    robot_fixed: bool = False
    chair_fixed: bool = False
    external_support: bool = False
    source: str = "isaac_physx_free_sitting"
    chair_quat_wxyz: tuple[float,float,float,float] = (1.,0.,0.,0.)
    unexpected_contact_force_n: float = 0.
    seat_top_local_m: float = .4775


def _finite_vector(value, size):
    return (isinstance(value,(tuple,list)) and len(value)==size
            and all(isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x) for x in value))


def _chair_local(sample: SittingSample, world):
    """Rotate a world point into the measured chair frame (unit wxyz)."""
    w,x,y,z=sample.chair_quat_wxyz
    px,py,pz=(world[i]-sample.chair_xyz_m[i] for i in range(3))
    return ((1-2*y*y-2*z*z)*px+(2*x*y+2*w*z)*py+(2*x*z-2*w*y)*pz,
            (2*x*y-2*w*z)*px+(1-2*x*x-2*z*z)*py+(2*y*z+2*w*x)*pz,
            (2*x*z+2*w*y)*px+(2*y*z-2*w*x)*py+(1-2*x*x-2*y*y)*pz)


def measured_seat_support(sample: SittingSample) -> float:
    """Recompute support from measured contact points, ignoring on_seat claims."""
    total=0.
    for contact in sample.seat_contacts:
        local=_chair_local(sample,contact['position_m'])
        if abs(local[2]-sample.seat_top_local_m)<.035 and abs(local[0])<.24 and abs(local[1])<.25:
            total+=max(0.,contact['force_n'][2])
    return total


def standing_precondition(samples: list[SittingSample]) -> bool:
    """One complete second of double support after startup, before descent."""
    window=[s for s in samples if 2.5<=s.time_s<=3.5]
    return len(window)>=30 and all(s.seat_force_n<=10 and min(s.left_foot_force_n,s.right_foot_force_n)>=20
                                 and s.pelvis_xyz_m[2]>=.68 for s in window)


def sitting_guard(sample: SittingSample) -> str | None:
    values = [v for v in asdict(sample).values() if isinstance(v,(int,float))]
    if not all(math.isfinite(v) for v in values):return "INVALID_EVIDENCE"
    if (isinstance(sample.seat_top_local_m,bool) or not isinstance(sample.seat_top_local_m,(int,float))
            or not .35<=sample.seat_top_local_m<=.55):return 'INVALID_EVIDENCE'
    if not (_finite_vector(sample.pelvis_xyz_m,3) and _finite_vector(sample.chair_xyz_m,3)
            and _finite_vector(sample.chair_quat_wxyz,4)):
        return 'INVALID_EVIDENCE'
    if abs(sum(v*v for v in sample.chair_quat_wxyz)-1)>1e-3:return 'INVALID_EVIDENCE'
    for name in ('time_s','pelvis_speed_mps','torso_tilt_rad','chair_speed_mps','chair_tilt_rad',
                 'seat_force_n','left_foot_force_n','right_foot_force_n','maximum_contact_force_n',
                 'maximum_joint_effort_ratio','foot_slip_mps','unexpected_contact_force_n'):
        value=getattr(sample,name)
        if isinstance(value,bool) or not isinstance(value,(int,float)) or value<0:return 'INVALID_EVIDENCE'
    if not all(type(getattr(sample,name)) is bool for name in ('robot_fixed','chair_fixed','external_support')):
        return 'INVALID_EVIDENCE'
    seen=set()
    for contact in sample.seat_contacts:
        if (not isinstance(contact,dict) or contact.get('body') not in SEAT_CONTACT_BODIES
                or contact['body'] in seen or not _finite_vector(contact.get('force_n'),3)
                or not _finite_vector(contact.get('position_m'),3)):
            return 'INVALID_EVIDENCE'
        seen.add(contact['body'])
    if abs(sample.seat_force_n-measured_seat_support(sample))>.01:return 'CONTACT_EVIDENCE_MISMATCH'
    if sample.robot_fixed or sample.chair_fixed or sample.external_support:return "EXTERNAL_SUPPORT"
    if sample.torso_tilt_rad > .60 or sample.chair_tilt_rad > .30:return "UNSAFE_TILT"
    if sample.pelvis_xyz_m[2] < .35:return "FALL"
    if sample.maximum_contact_force_n > 1500:return "EXCESS_CONTACT_FORCE"
    if sample.maximum_joint_effort_ratio > 1.001:return "EFFORT_LIMIT"
    if sample.unexpected_contact_force_n>20:return 'UNPLANNED_CONTACT'
    return None


def verify_free_sitting(samples: list[SittingSample], *, robot_mass_kg: float,
                        stable_window_s: float = 2., seat_half_size=(.23,.24)) -> dict:
    """Reject hover, external support, initial seated resets, slip and drift."""
    result = {"succeeded":False,"free_balance_sitting_verified":False,"failure":None,
              "source":"isaac_physx_free_sitting","sample_count":len(samples)}
    def fail(reason):result["failure"]=reason;return result
    if (not math.isfinite(robot_mass_kg) or robot_mass_kg<=0 or not math.isfinite(stable_window_s)
            or stable_window_s<2 or not _finite_vector(seat_half_size,2) or min(seat_half_size)<=.025):
        return fail('INVALID_EVIDENCE')
    if not samples:return fail('MISSING_EVIDENCE')
    if not 0<samples[0].time_s<=.031:return fail('MISSING_INITIAL_STATE')
    if samples[-1].time_s>60.01:return fail('TIMEOUT')
    for i,s in enumerate(samples):
        if s.source != result["source"]:return fail("UNSUPPORTED_SOURCE")
        if i and not 0 < s.time_s-samples[i-1].time_s <= .031:return fail("SAMPLE_GAP")
        error=sitting_guard(s)
        if error:return fail(error)
    standing=[s for s in samples if 2.5 <= s.time_s <= 3.5]
    if not standing_precondition(samples):
        return fail("STANDING_PRECONDITION")
    window=[s for s in samples if s.time_s>=samples[-1].time_s-stable_window_s-1e-8]
    if window[-1].time_s-window[0].time_s < stable_window_s-.021:return fail("STABLE_WINDOW_MISSING")
    if min(s.pelvis_xyz_m[2] for s in standing)-max(s.pelvis_xyz_m[2] for s in window)<.10:return fail("NO_SIT_TRANSITION")
    for s in window:
        if s.seat_force_n < .15*robot_mass_kg*9.81:return fail("INSUFFICIENT_SEAT_SUPPORT")
        if min(s.left_foot_force_n,s.right_foot_force_n)<10:return fail("FOOT_CONTACT_LOST")
        if s.pelvis_speed_mps>.06 or s.chair_speed_mps>.04 or s.foot_slip_mps>.06:return fail("NOT_SETTLED")
        if s.torso_tilt_rad>.30 or s.chair_tilt_rad>.12:return fail("SEATED_TILT")
        if s.unexpected_contact_force_n>5:return fail('UNPLANNED_CONTACT')
        local=_chair_local(s,s.pelvis_xyz_m)
        if any(abs(local[i])>seat_half_size[i]-.025 for i in (0,1)):
            return fail("SEAT_EDGE")
    result.update(succeeded=True,free_balance_sitting_verified=True,
        standing_height_m=sum(s.pelvis_xyz_m[2] for s in standing)/len(standing),
        final_height_m=window[-1].pelvis_xyz_m[2],
        minimum_seat_force_n=min(s.seat_force_n for s in window),
        maximum_chair_speed_mps=max(s.chair_speed_mps for s in window),
        stable_window_s=window[-1].time_s-window[0].time_s)
    return result
