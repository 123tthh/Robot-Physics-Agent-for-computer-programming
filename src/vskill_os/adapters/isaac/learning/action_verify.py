"""CPU-only source-bound verification of normalized mapping and raw physics."""

import hashlib
import json

from ....learning.action_evaluation import check_mapping_trace
from ....learning.contracts import EpisodeRecord
from ....learning.environment import ResetSpec
from ....learning.physical import PhysicsTrace
from ....learning.policy_manifest import ActionSchema, MappingTrace
from ....learning.serialization import _unique_object
from .action_smoke import ACTION_SOURCES
from .config import control_spec
from .single_hand_env import DEFAULT_URDF, ROOT, file_sha256
from .verify import verify_bundle


def verify_action_bundle(directory, *, physical_config, scale, case, seed):
    report = dict(accepted=False, mapping_passed=False, physical_primitive_passed=False, findings=[], case=case,
                  recording=str(directory.resolve()), learned_policy=False, camera_verified=False, real_robot_verified=False)
    try:
        manifest = json.loads((directory/'action_manifest.json').read_text(), object_pairs_hook=_unique_object)
        names = ('action_mapping.json', 'manifest.json', 'episode.json', 'trace.json')
        if (manifest['schema_version'] != 'vf-action-bundle/1' or set(manifest['files']) != set(names)
                or any(manifest['files'][name] != file_sha256(directory/name) for name in names)):
            raise ValueError('ACTION_ARTIFACT_HASH: action or physical bundle was changed')
        sources = {name: file_sha256(ROOT/name) for name in ACTION_SOURCES}
        if manifest['sources'] != sources:
            raise ValueError('ACTION_SOURCE_HASH: verify with the captured collector revision')
        schema = ActionSchema.from_control(control_spec(DEFAULT_URDF, physical_config), scale)
        mapping = MappingTrace.read(directory/'action_mapping.json')
        trace = PhysicsTrace.read(directory/'trace.json')
        episode = EpisodeRecord.read(directory/'episode.json')
        reset = ResetSpec.read(directory/'reset.json')
        if reset != ResetSpec(case, seed=seed) or mapping.episode_id != case:
            raise ValueError('ACTION_CASE: changed episode, seed or horizon')
        physical = verify_bundle(directory, expected_config=physical_config)
        report['physical'] = physical
        report['physical_primitive_passed'] = physical['physical_primitive_passed']
        report.update(check_mapping_trace(mapping, trace, schema))
        report['source_sha256'] = hashlib.sha256(json.dumps(sources, sort_keys=True).encode()).hexdigest()
        report['mapping_sha256'] = mapping.sha256()
        report['schema_sha256'] = schema.sha256()
        report['evaluator_sha256'] = hashlib.sha256(b''.join((ROOT/name).read_bytes() for name in (
            'src/vskill_os/learning/action_evaluation.py',
            'src/vskill_os/adapters/isaac/learning/action_verify.py',
            'src/vskill_os/adapters/isaac/learning/action_runner.py',
        ))).hexdigest()
        report['control_dt_s'] = schema.control.control_dt_s
        report['rejection_codes'] = [e.failure_codes[0] for e in mapping.events if e.kind == 'rejected']
        report['lease_tokens'] = [e.lease_token for e in mapping.events if e.kind in ('reset', 'resume')]
        report['backend_token'] = trace.owner_token
        expected = []
        if case in ('paused_press', 'press_after_reset'):
            expected.append('CONTROL_OWNERSHIP')  # old policy port after physical reset
        if case == 'paused_press':
            expected += ['CONTROL_OWNERSHIP', 'JOINT_MAPPING', 'ACTION_SEQUENCE', 'ACTION_RANGE',
                         'ACTION_EXPIRED', 'ACTION_EXPIRED', 'CONTROL_STOPPED', 'CONTROL_OWNERSHIP']
        if report['rejection_codes'] != expected:
            report['findings'].append('missing frozen rejection probes')
        if report['masked_nonzero_values'] != report['policy_steps']*22 or report['rate_limited_values'] == 0:
            report['findings'].append('diagnostic did not exercise mask and rate filter')
        stops = [e for e in mapping.events if e.kind == 'stop']
        resumes = [e for e in mapping.events if e.kind == 'resume']
        if case == 'paused_press':
            if len(stops) != 1 or len(resumes) != 1 or report['hold_steps'] != round(.1/schema.control.control_dt_s):
                raise ValueError('missing bounded stop/hold/resume sequence')
            stop, resume = stops[0], resumes[0]
            key_index = trace.key_names.index('a')
            held = [s for s in trace.samples if stop.after_time_s <= s.stamp.capture_time_s <= resume.before_time_s]
            physically_down = bool(held) and all(-s.key_joint_positions_m[key_index] >= .001 for s in held)
            before_down = any(e.event_type == 'keydown' and e.stamp.capture_time_s <= stop.before_time_s for e in episode.events)
            after_up = any(e.event_type == 'keyup' and e.reason == 'release_threshold' and e.stamp.capture_time_s > resume.after_time_s
                           for e in episode.events)
            report['stop_hold_evidence'] = dict(key_stayed_physically_down=physically_down,
                down_before_stop=before_down, up_after_resume=after_up,
                duration_s=resume.after_time_s-stop.before_time_s, pause_is_release=False)
            if not (physically_down and before_down and after_up):
                report['findings'].append('raw contact/event evidence did not establish loaded pause then release')
        elif stops or resumes or report['hold_steps']:
            report['findings'].append('unexpected lifecycle operations')
        report['accepted'] = report['mapping_passed'] and report['physical_primitive_passed'] and not report['findings']
    except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
        report['findings'].append(str(exc))
    return report
