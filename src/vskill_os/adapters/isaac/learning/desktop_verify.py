"""Independent physical/PTY/texture/robot-RGB-D and feedback verification.

No simulator starts. OCR is rerun on the saved full camera image, never on a
terminal texture or text buffer. Asset FK is only an offline camera-mount audit.
"""
from dataclasses import asdict
import hashlib
import json
import math
from pathlib import Path
import xml.etree.ElementTree as ET

from ....learning.camera_evaluation import check_camera_feedback
from ....learning.camera_feedback import CameraActorObservation, CameraReadout, DesktopCameraSpec
from ....learning.contracts import EpisodeRecord
from ....learning.physical import PhysicsTrace
from ....learning.serialization import _unique_object
from ....simulation.desktop import ScreenFrame
from ....simulation.keyboard_bridge import KeyEvent, KeyEventType
from ...camera_ocr import image_region, recognize, runtime_identity
from ...learning_desktop import LearningTerminalRenderer
from .desktop_env import DESKTOP_SOURCES
from .single_hand_env import DEFAULT_URDF, ROOT, file_sha256
from .verify import verify_bundle


def rotation(quaternion):
    import numpy as np
    q = np.asarray(quaternion, dtype=float)
    if q.shape != (4,) or not np.isfinite(q).all() or abs(float(q@q)-1.) > .0001:
        raise ValueError('INVALID_CAMERA_QUATERNION')
    w, x, y, z = q/np.linalg.norm(q)
    return np.asarray(((1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)),
                       (2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)),
                       (2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y))))


def head_pose(sample, names):
    import numpy as np
    joints = {j.find('child').get('link'): j for j in ET.parse(DEFAULT_URDF).getroot().findall('joint')}
    chain, child = [], 'head_link'
    while child in joints:
        joint = joints[child]
        chain.append(joint)
        child = joint.find('parent').get('link')
    if child != 'pelvis':
        raise ValueError('HEAD_ASSET_CHAIN')
    values = dict(zip(names, sample.joint_positions, strict=True))
    transform = np.eye(4)
    transform[:3, 3] = sample.pelvis_xyz_m
    def axis_rotation(axis, angle):
        axis = np.asarray(axis, dtype=float)
        axis /= np.linalg.norm(axis)
        x, y, z = axis
        cross = np.asarray(((0, -z, y), (z, 0, -x), (-y, x, 0)))
        return np.eye(3)*math.cos(angle)+(1-math.cos(angle))*np.outer(axis, axis)+math.sin(angle)*cross
    for joint in reversed(chain):
        local = np.eye(4)
        origin = joint.find('origin')
        local[:3, 3] = [float(v) for v in origin.get('xyz', '0 0 0').split()]
        r, p, y = [float(v) for v in origin.get('rpy', '0 0 0').split()]
        local[:3, :3] = axis_rotation((0, 0, 1), y)@axis_rotation((0, 1, 0), p)@axis_rotation((1, 0, 0), r)
        if joint.get('type') != 'fixed':
            axis = [float(v) for v in joint.find('axis').get('xyz').split()]
            local[:3, :3] = local[:3, :3]@axis_rotation(axis, values[joint.get('name')])
        transform = transform@local
    return transform[:3, 3], transform[:3, :3]


def verify_desktop_bundle(directory: Path, *, spec: DesktopCameraSpec, physical_config):
    import numpy as np
    from PIL import Image
    directory = directory.resolve()
    result = dict(schema_version='vf-desktop-camera-evaluation/1', recording=str(directory),
                  binding_passed=False, physical_primitive_passed=False, desktop_input_correct=False,
                  camera_confirmation_passed=False, end_to_end_passed=False, findings=[],
                  task_failures=[], frames=[], learned_policy=False, real_robot_verified=False, code_agent_executed=False)
    def require(condition, code):
        if not condition:
            raise ValueError(code)
    def local_file(value):
        path = Path(value).resolve()
        require(path.is_relative_to(directory) and path.is_file(), 'CAMERA_PATH_OUTSIDE_EPISODE')
        return path
    try:
        manifest = json.loads((directory/'desktop_manifest.json').read_text(), object_pairs_hook=_unique_object)
        require(manifest['schema_version'] == 'vf-desktop-camera-bundle/1', 'DESKTOP_BUNDLE_SCHEMA')
        require(manifest['sources'] == {name: file_sha256(ROOT/name) for name in DESKTOP_SOURCES}, 'DESKTOP_SOURCE_HASH')
        require(file_sha256(directory/'manifest.json') == manifest['physics_manifest_sha256']
                and file_sha256(directory.parent/'desktop_scene.usda') == manifest['desktop_scene_sha256'], 'DESKTOP_PHYSICS_BINDING')
        require(DesktopCameraSpec.read(directory/'desktop_spec.json') == spec, 'DESKTOP_FROZEN_CONFIG')
        require({'desktop_spec.json', 'desktop_episode.json', 'desktop/desktop.json'} <= set(manifest['files']), 'DESKTOP_FILE_SET')
        for name, digest in manifest['files'].items():
            path = local_file(directory/name)
            require(file_sha256(path) == digest, 'DESKTOP_ARTIFACT_HASH: '+name)
        record = json.loads((directory/'desktop_episode.json').read_text(), object_pairs_hook=_unique_object)
        require(record['schema_version'] == 'vf-desktop-camera-episode/1', 'DESKTOP_EPISODE_SCHEMA')
        require(record['case'] == record['episode_id'] == directory.name, 'DESKTOP_EPISODE_ID')
        require(record['ocr_runtime'] == runtime_identity(), 'OCR_RUNTIME_CHANGED')
        require(record['learned_policy'] is False and record['real_robot_verified'] is False
                and record['code_agent_executed'] is False, 'UNSUPPORTED_CAPABILITY_CLAIM')
        physical = verify_bundle(directory, expected_config=physical_config)
        result['physical_primitive_passed'] = physical['physical_primitive_passed']
        require(physical['physical_primitive_passed'], 'PHYSICAL_PRIMITIVE_FAILED: '+str(physical['findings']))
        episode, trace = EpisodeRecord.read(directory/'episode.json'), PhysicsTrace.read(directory/'trace.json')
        require(episode.sha256() == record['physical_episode_sha256'] and episode.env_id == record['env_id']
                and episode.episode_id == record['episode_id'] and episode.end_time_s == record['end_time_s']
                and episode.termination == record['termination'] == 'finish_requested', 'DESKTOP_PHYSICAL_EPISODE')
        require([e.key for e in episode.events if e.event_type == 'keydown'] == ['a'], 'DESKTOP_PHYSICAL_INPUT')
        require([r['event'] for r in record['routes']] == [e.to_dict() for e in episode.events], 'DESKTOP_EVENT_ROUTING')
        focused = record['case'] != 'focus_lost'
        for routed in record['routes']:
            stamp = routed['event']['stamp']
            require(routed['focused'] == focused and stamp['capture_time_s'] <= routed['dispatch_time_s'] <= stamp['capture_time_s']+trace.control_spec.control_dt_s+1e-9,
                    'DESKTOP_EVENT_DISPATCH_TIME_OR_FOCUS')
        desktop = record['desktop']
        require(json.loads((directory/'desktop/desktop.json').read_text()) == desktop, 'DESKTOP_DUPLICATE_RECORD')
        forwarded = [asdict(KeyEvent(KeyEventType(e.event_type), e.key, e.stamp.capture_time_s,
                                    'keyboard_event_bridge', e.reason, e.stamp.record_id)) for e in episode.events]
        require(desktop['events'] == forwarded and desktop['input_source'] == 'forwarded_key_events'
                and desktop['workspace_is_os_sandbox'] is False and desktop['camera_ocr_verified'] is False, 'DESKTOP_CHANGED_EVENTS_OR_CLAIMS')
        require(bool(desktop['frames']), 'DESKTOP_EMPTY_FRAMES')
        renderer = LearningTerminalRenderer()
        previous_id, previous_text = -1, ''
        first_down = next(e.stamp.capture_time_s for e in episode.events if e.event_type == 'keydown')
        for frame in desktop['frames']:
            require(frame['frame_id'] > previous_id and frame['text'].startswith(previous_text), 'PTY_FRAME_ORDER')
            previous_id, previous_text = frame['frame_id'], frame['text']
            png = local_file(frame['png_path'])
            require(str(png.relative_to(directory)) in manifest['files'] and file_sha256(png) == frame['png_sha256'], 'TEXTURE_PNG_HASH')
            with Image.open(png) as image:
                pixels = image.convert('RGBA').tobytes()
            require(hashlib.sha256(pixels).hexdigest() == frame['rgba_sha256'], 'TEXTURE_PIXEL_HASH')
            renderer.render(ScreenFrame(frame['frame_id'], frame['monotonic_time'], focused, True, frame['text']))
            require(list(renderer.terminal.display) == frame['terminal_rows'] and renderer.rgba().tobytes() == pixels, 'PTY_TEXT_TO_PIXELS_MISMATCH')
            # The v1 ROI is the second terminal row beneath this actual /bin/sh
            # startup line. Reject other layouts; do not search for the target.
            require(frame['terminal_rows'][0].rstrip() in ('', "/bin/sh: 0: can't access tty; job control turned off"), 'TERMINAL_LAYOUT_CHANGED')
            require(all(not line.strip() for line in frame['terminal_rows'][2:]), 'EXTRA_TERMINAL_INPUT')
            if frame['simulation_time_s'] < first_down:
                require(not frame['terminal_rows'][1].strip(), 'DESKTOP_RESET_LEAK')
        renderer.close()
        actual_text = desktop['frames'][-1]['terminal_rows'][1].rstrip()
        expected_text = 'a' if focused else ''
        require(actual_text == expected_text, 'UNEXPECTED_ACTUAL_TERMINAL_TEXT')
        result.update(actual_terminal_text=actual_text, desktop_input_correct=actual_text == 'a')
        frames_by_id = {f['frame_id']: f for f in desktop['frames']}
        displayed, last_time = None, -1.
        for index, texture in enumerate(record['textures']):
            require(last_time <= texture['time_s'] <= record['end_time_s'] and texture['frame_id'] in frames_by_id, 'TEXTURE_TIME_OR_ID')
            last_time = texture['time_s']
            require(texture['terminal_alive'] and texture['terminal_pid'] == record['terminal_pid']
                    and texture['focused'] == focused, 'TERMINAL_PROCESS_OR_FOCUS')
            expected_publish = index == 0 or texture['changed'] and record['case'] != 'frozen_texture'
            require(texture['published'] == expected_publish, 'TEXTURE_PUBLICATION_POLICY')
            if expected_publish: displayed = texture['frame_id']
            require(texture['displayed_frame_id'] == displayed, 'TEXTURE_DISPLAY_BINDING')
            require(texture['texture_uri'] == f"dynamic://vf05_{record['episode_id']}_{displayed}", 'TEXTURE_RESOURCE_ISOLATION')
        require(record['textures'][0]['time_s'] == 0., 'TEXTURE_RESET_MISSING')
        errors = check_camera_feedback(record, spec.feedback, trace.control_spec.control_dt_s)
        require(not errors, ','.join(errors))
        samples = {s.stamp.record_id: s for s in trace.samples}
        names = [limit.name for limit in trace.coordinate_limits]
        mount = record['camera_mount']
        require(mount == dict(body='/World/G1/head_link', position_local_m=list(spec.mount_position),
                quaternion_local_wxyz=list(spec.mount_quaternion), pose_source='measured_physx_link_pose_times_constant_mount'), 'CAMERA_MOUNT_CONFIG')
        require(len({f['frame_id'] for f in record['camera_frames']}) == len(record['camera_frames']), 'CAMERA_DUPLICATE_FRAME')
        captures = {c['frame_id']: CameraReadout.from_dict(c) for c in record['captures'] if c['frame_id'] is not None}
        require(set(captures) == {f['frame_id'] for f in record['camera_frames']}, 'CAMERA_CAPTURE_SET')
        for index, frame in enumerate(record['camera_frames']):
            camera, readout = frame['camera'], captures[frame['frame_id']]
            require(frame['frame_id'] == f"{record['env_id']}:{record['episode_id']}:camera:{index}"
                    and frame['request_id'] == readout.request_id and frame['time_s'] == readout.capture_time_s == readout.produced_time_s == camera['simulation_time_s'], 'CAMERA_FRAME_REQUEST_OR_CLOCK')
            raw = samples[frame['physical_sample_id']]
            require(raw.stamp.capture_time_s == frame['time_s'], 'CAMERA_PHYSICS_TIMESTAMP')
            require(frame['render_frames'] == spec.render_frames and frame['wall_s'] >= frame['ocr']['wall_s'] >= 0, 'CAMERA_RENDER_ACCOUNTING')
            matching = [t for t in record['textures'] if t['time_s'] <= frame['time_s']]
            require(matching[-1]['displayed_frame_id'] == frame['displayed_frame_id'], 'CAMERA_TEXTURE_BINDING')
            require(frame['texture_asset'] == matching[-1]['texture_uri'] and frame['antialiasing'] == 2,
                    'CAMERA_TEMPORAL_RENDER_OR_TEXTURE_ASSET')
            rgb, depth_path = local_file(camera['rgb']), local_file(camera['depth'])
            for path, digest in ((rgb, camera['rgb_sha256']), (depth_path, camera['depth_sha256'])):
                require(str(path.relative_to(directory)) in manifest['files'] and file_sha256(path) == digest, 'CAMERA_FILE_HASH')
            with Image.open(rgb) as image:
                require(image.size == spec.image_size, 'CAMERA_RGB_SHAPE')
            depth = np.load(depth_path, allow_pickle=False).squeeze()
            require(depth.shape == (spec.image_size[1], spec.image_size[0]), 'CAMERA_DEPTH_SHAPE')
            hp, hr = head_pose(raw, names)
            require(np.allclose(hp, frame['head_position'], atol=1e-5, rtol=0)
                    and np.allclose(hr, rotation(frame['head_quaternion']), atol=1e-5, rtol=0), 'CAMERA_HEAD_FK')
            position = hp+hr@np.asarray(spec.mount_position)
            basis = hr@rotation(spec.mount_quaternion)@np.asarray(((0, 0, 1), (-1, 0, 0), (0, -1, 0)))
            require(np.allclose(position, camera['position_w'], atol=1e-5, rtol=0)
                    and np.allclose(basis, rotation(camera['quaternion_w_ros']), atol=1e-5, rtol=0), 'CAMERA_FIXED_MOUNT')
            intrinsic = np.asarray(camera['intrinsic_matrix'])
            require(intrinsic.shape == (3, 3) and np.allclose(intrinsic, ((610.832763671875, 0, 640), (0, 610.832763671875, 360), (0, 0, 1)), atol=1e-4, rtol=0), 'CAMERA_INTRINSIC_CALIBRATION')
            left, top, right, bottom = spec.input_roi
            yy, xx = np.mgrid[top:bottom, left:right]
            rays = np.stack((xx, yy, np.ones_like(xx)), axis=-1)@np.linalg.inv(intrinsic).T@basis.T
            expected_depth = (.830-.0105-position[0])/rays[:, :, 0]
            observed_depth = depth[top:bottom, left:right]
            valid = np.isfinite(observed_depth) & (observed_depth > .02) & (np.abs(observed_depth-expected_depth) < .01)
            fraction = float(valid.mean())
            require(fraction >= spec.minimum_depth_fraction, 'CAMERA_DEPTH_NOT_SCREEN')
            fresh_ocr = recognize(rgb, spec)
            for field in ('source', 'text', 'confidence', 'missing', 'roi', 'scale', 'roi_pixels_sha256', 'tsv'):
                require(fresh_ocr[field] == frame['ocr'][field], 'CAMERA_OCR_REPLAY: '+field)
            roi_path = local_file(frame['roi_path'])
            require(str(roi_path.relative_to(directory)) in manifest['files'], 'CAMERA_ROI_UNHASHED')
            with Image.open(roi_path) as roi:
                require(roi.tobytes() == image_region(rgb, spec).tobytes(), 'CAMERA_ROI_DERIVATION')
            require((readout.text, readout.confidence, readout.missing) == (fresh_ocr['text'], fresh_ocr['confidence'], fresh_ocr['missing']), 'CAMERA_READOUT_FROM_WRONG_SOURCE')
            if index == 0:
                require(frame['time_s'] < first_down and not (readout.text == 'a' and readout.confidence >= spec.feedback.minimum_confidence),
                        'CAMERA_RESET_RETAINED_OLD_INPUT')
            result['frames'].append(dict(frame_id=frame['frame_id'], time_s=frame['time_s'],
                rgb_sha256=camera['rgb_sha256'], ocr_text=fresh_ocr['text'], confidence=fresh_ocr['confidence'],
                displayed_input=frames_by_id[frame['displayed_frame_id']]['terminal_rows'][1].rstrip(),
                display_is_current=frames_by_id[frame['displayed_frame_id']]['rgba_sha256'] ==
                    frames_by_id[matching[-1]['frame_id']]['rgba_sha256'],
                depth_screen_fraction=fraction, capture_wall_s=frame['wall_s']))
        no_frame = [c for c in record['captures'] if c['frame_id'] is None]
        require(len(no_frame) == (1 if record['case'] == 'missing_camera' else 0)
                and all(c['missing'] and c['request_id'] == record['attempts'][-1]['request_id'] for c in no_frame), 'UNDECLARED_CAMERA_LOSS')
        observations = {f.stamp.capture_time_s: f for f in episode.observations}
        for publication in record['publications']:
            base = CameraActorObservation.from_dict(publication['observation']).physical
            raw = observations[base.time_s]
            prior = [a for a in trace.actions if a.time_s < base.time_s-1e-9]
            previous = tuple(a-b for a, b in zip(prior[-1].applied_targets_rad, prior[-1].previous_targets_rad, strict=True)) if prior else (0.,)*len(base.joints)
            require(base.keys == raw.keys and base.joints == raw.joints and base.target_key == 'a'
                    and base.previous_delta_rad == previous, 'CAMERA_POLICY_PHYSICAL_BINDING')
        final = CameraActorObservation.from_dict(record['publications'][-1]['observation'])
        last_event_time = max(e.stamp.capture_time_s for e in episode.events)
        confirmed = any(f.purpose == 'final' and f.status == 'readable' and f.text == 'a'
                        and f.capture_time_s >= last_event_time for f in final.feedback)
        final_frames = [f for f in result['frames'] if f['time_s'] > last_event_time]
        displayed_correctly = bool(final_frames) and final_frames[-1]['display_is_current'] and final_frames[-1]['displayed_input'] == 'a'
        result.update(binding_passed=True, camera_confirmation_passed=confirmed,
            end_to_end_passed=result['desktop_input_correct'] and confirmed and displayed_correctly,
            terminal_pid=record['terminal_pid'], physical_episode_sha256=episode.sha256(),
            desktop_manifest_sha256=file_sha256(directory/'desktop_manifest.json'),
            verifier_sha256=hashlib.sha256(Path(__file__).read_bytes()+(ROOT/'src/vskill_os/learning/camera_evaluation.py').read_bytes()).hexdigest(),
            end_time_s=record['end_time_s'], accepted_requests=final.budget.accepted_requests,
            verification_wait_s=final.budget.elapsed_wait_s,
            final_feedback=[f.to_dict() for f in final.feedback if f.purpose == 'final'])
        if not result['desktop_input_correct']: result['task_failures'].append('DESKTOP_TEXT_INCORRECT')
        if not confirmed: result['task_failures'].append('CAMERA_FINAL_UNCONFIRMED')
        if final_frames and not final_frames[-1]['display_is_current']: result['task_failures'].append('DISPLAY_TEXTURE_STALE')
    except (OSError, ValueError, KeyError, TypeError, IndexError, StopIteration, AttributeError) as exc:
        result['findings'].append(str(exc))
    return result
