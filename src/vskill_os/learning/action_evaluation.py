"""Independent arithmetic/lifecycle replay bound to raw physical action records.

This evaluator does not call the mapper or JointTargetGuard. Physical safety,
source/asset identity and actual Down/Up success are verified separately by the
VF-01 physical bundle verifier. Neither a mapper receipt nor this replay alone
is evidence of a successful physical key press.
"""

from .contracts import JointRecord
from .physical import PhysicsTrace
from .policy_manifest import ActionSchema, MappingTrace


def _near(actual, expected, tolerance=1e-6):
    return len(actual) == len(expected) and all(abs(a-b) <= tolerance for a, b in zip(actual, expected))


def check_mapping_trace(mapping: MappingTrace, physical: PhysicsTrace, expected_schema: ActionSchema) -> dict:
    report = dict(mapping_passed=False, findings=[], policy_steps=0, hold_steps=0, rejected_inputs=0,
                  masked_nonzero_values=0, rate_limited_values=0, position_limited_values=0,
                  maximum_feedback_rounding_rad=0.0)
    try:
        schema = mapping.action_schema
        if schema != expected_schema or schema.control != physical.control_spec:
            raise ValueError("ACTION_SCHEMA: frozen schema mismatch")
        if mapping.control_claims != schema.claims():
            raise ValueError("CONTROL_OWNERSHIP: overlapping or missing target owner")
        if (mapping.physical_episode_sha256 != physical.episode_sha256 or not physical.samples
                or not mapping.events or mapping.events[0].kind != "reset" or mapping.events[-1].kind != "finish"):
            raise ValueError("MAPPING_BINDING: incomplete trace or wrong physical episode")
        if any((s.stamp.env_id, s.stamp.episode_id) != (mapping.env_id, mapping.episode_id) for s in physical.samples):
            raise ValueError("MAPPING_BINDING: environment/episode mismatch")
        spec = schema.control
        all_names = tuple(limit.name for limit in physical.coordinate_limits)
        ids = tuple(all_names.index(name) for name in spec.joint_names)
        samples = {round(s.stamp.capture_time_s, 12): s for s in physical.samples}
        if len(samples) != len(physical.samples):
            raise ValueError("MAPPING_BINDING: duplicate physical timestamps")

        def joints_at(t):
            raw = samples[round(t, 12)]
            return tuple(JointRecord(name, raw.joint_positions[i], raw.joint_velocities[i], raw.joint_targets[i])
                         for name, i in zip(spec.joint_names, ids, strict=True))

        time_s, sequence, action_index = 0.0, 0, 0
        joints = joints_at(0.0)
        state, token, minted, paused_at = "new", "", set(), None
        for event_index, e in enumerate(mapping.events):
            if abs(e.before_time_s-time_s) > 1e-9 or e.joints != joints_at(e.after_time_s):
                raise ValueError(f"MAPPING_FEEDBACK: clock or raw joint mismatch at event {event_index}")
            targets = tuple(j.commanded_target_rad for j in joints)
            if e.kind in ("reset", "resume"):
                if e.kind == "reset":
                    if event_index != 0 or time_s != 0:
                        raise ValueError("MAPPING_LIFECYCLE: unexpected reset")
                    if any(abs(j.position_rad-j.commanded_target_rad) > schema.scale.initial_tracking_tolerance_rad for j in joints):
                        raise ValueError("MAPPING_INITIALIZATION: unaudited tracking error")
                elif state != "paused" or paused_at is None or time_s <= paused_at:
                    raise ValueError("MAPPING_LIFECYCLE: resume without fresh guarded hold")
                if not e.lease_token or e.lease_token in minted:
                    raise ValueError("MAPPING_OWNERSHIP: reused or missing lease")
                minted.add(e.lease_token)
                token, state, sequence = e.lease_token, "active", 0
            elif e.kind == "stop":
                if state != "active":
                    raise ValueError("MAPPING_LIFECYCLE: stop outside active state")
                token, state, paused_at = "", "paused", time_s
            elif e.kind == "finish":
                if state not in ("active", "paused") or event_index != len(mapping.events)-1:
                    raise ValueError("MAPPING_LIFECYCLE: invalid completion")
                token, state = "", "finished"
            elif e.kind in ("policy", "rejected"):
                a = e.action
                if a is None:
                    raise ValueError("MAPPING_INPUT: missing normalized action")
                code = None
                if state in ("latched", "finished"):
                    code = "RESET_REQUIRED"
                elif state == "paused":
                    code = "CONTROL_STOPPED"
                elif e.port_token != token or a.owner_token != token:
                    code = "CONTROL_OWNERSHIP"
                elif a.joint_names != spec.joint_names:
                    code = "JOINT_MAPPING"
                elif a.sequence != sequence:
                    code = "ACTION_SEQUENCE"
                elif any(abs(v) > 1 for v in a.values):
                    code = "ACTION_RANGE"
                elif abs(a.duration_s-spec.control_dt_s) > 1e-9 or a.valid_until_s+1e-9 < time_s+spec.control_dt_s:
                    code = "ACTION_EXPIRED"
                if e.kind == "rejected":
                    if code is None or e.failure_codes != (code,):
                        raise ValueError("MAPPING_REJECTION: rejection not supported by recorded input")
                    report['rejected_inputs'] += 1
                elif code is not None:
                    raise ValueError(f"MAPPING_INPUT: accepted invalid input {code}")
            elif e.kind != "hold":
                raise ValueError("MAPPING_LIFECYCLE: incomplete or faulted mapping capture")

            if e.kind in ("policy", "hold"):
                if state != ("paused" if e.kind == "hold" else "active") or e.backend_action_index != action_index:
                    raise ValueError("MAPPING_LIFECYCLE: wrong executor state or raw action index")
                a = e.action
                delta = tuple(v*scale*spec.control_dt_s/schema.scale.reference_dt_s
                              for v, scale in zip(a.values, schema.scale.delta_rad, strict=True)) if a else (0.0,)*23
                proposed = tuple(q+d for q, d in zip(targets, delta, strict=True))
                masked = tuple(d if n in schema.mask.active else 0.0 for n, d in zip(spec.joint_names, delta, strict=True))
                limited = tuple(max(-v*spec.control_dt_s, min(v*spec.control_dt_s, d))
                                for v, d in zip(spec.maximum_speed_rad_s, masked, strict=True))
                filtered = tuple(max(low, min(high, q+d)) for low, high, q, d in
                                 zip(spec.lower_rad, spec.upper_rad, targets, limited, strict=True))
                raw_action = physical.actions[action_index]
                backend_action = raw_action.action
                if (raw_action.failure_code is not None or abs(raw_action.time_s-time_s) > 1e-9
                        or backend_action.joint_names != spec.joint_names or backend_action.sequence != action_index
                        or backend_action.owner_token != physical.owner_token
                        or backend_action.duration_s != spec.control_dt_s
                        or backend_action.valid_until_s != (a.valid_until_s if a else time_s+spec.control_dt_s)
                        or not _near(backend_action.delta_rad, masked, 1e-12)
                        or not _near(raw_action.previous_targets_rad, targets)
                        or not _near(raw_action.applied_targets_rad, filtered)
                        or not _near(e.proposed_targets_rad, proposed, 1e-12)
                        or not _near(e.filtered_targets_rad, filtered, 1e-12)):
                    raise ValueError("MAPPING_TARGET: raw action or proposal/filter arithmetic differs")
                executed = tuple(j.commanded_target_rad for j in e.joints)
                if not _near(executed, filtered) or abs(e.after_time_s-time_s-spec.control_dt_s) > 1e-9 or e.failure_codes:
                    raise ValueError("MAPPING_FEEDBACK: executed target, duration or safety failure")
                report['maximum_feedback_rounding_rad'] = max(report['maximum_feedback_rounding_rad'],
                    max(abs(a-b) for a, b in zip(executed, filtered, strict=True)))
                report['masked_nonzero_values'] += sum(d != m for d, m in zip(delta, masked, strict=True))
                report['rate_limited_values'] += sum(abs(d-l) > 1e-12 for d, l in zip(masked, limited, strict=True))
                report['position_limited_values'] += sum(abs(q+d-f) > 1e-12 for q, d, f in zip(targets, limited, filtered, strict=True))
                if e.kind == "policy":
                    sequence += 1
                elif a is not None or e.port_token:
                    raise ValueError("MAPPING_LIFECYCLE: hold must be supervisor-owned")
                report[e.kind+'_steps'] += 1
                time_s, joints, action_index = e.after_time_s, e.joints, action_index+1
            else:
                if (e.after_time_s != time_s or e.joints != joints or e.backend_action_index is not None
                        or e.proposed_targets_rad or e.filtered_targets_rad
                        or (e.kind != "rejected" and (e.action is not None or e.port_token or e.failure_codes))):
                    raise ValueError("MAPPING_ATOMICITY: non-step operation moved or rewrote physics")
            if (e.state, e.lease_token, e.policy_sequence) != (state, token, sequence):
                raise ValueError("MAPPING_LIFECYCLE: receipt disagrees with independently replayed state")
        if action_index != len(physical.actions) or abs(time_s-physical.samples[-1].stamp.capture_time_s) > 1e-9:
            raise ValueError("MAPPING_COVERAGE: raw actions or physics omitted")
        report['mapping_passed'] = True
    except (ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
        report['findings'].append(str(exc))
    return report
