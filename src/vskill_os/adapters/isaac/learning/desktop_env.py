"""T02 physics plus a real PTY texture and a fixed robot-mounted camera.

Physical collector sources remain unchanged. This supervisor owns all desktop
truth and artifact paths. A policy receives only request-gated pixel OCR and
physical observations; it cannot read PTY text or request a free final capture.
"""
from dataclasses import dataclass
from pathlib import Path
import time

from ....learning.camera_feedback import CameraActorObservation, CameraFeedbackQueue, CameraReadout, DesktopCameraSpec
from ....learning.contracts import EpisodeRecord
from ....learning.serialization import JsonRecord
from ....simulation.keyboard_bridge import KeyEvent, KeyEventType
from ...camera_ocr import recognize, runtime_identity
from ...learning_desktop import LearningTerminalRenderer
from ...virtual_computer import VirtualComputer
from ..virtual_screen import IsaacVirtualScreen
from .provenance import COLLECTOR_FILES
from .single_hand_env import ROOT, file_sha256, write_json

CASES = ('visible_input', 'visible_after_reset', 'focus_lost', 'frozen_texture', 'missing_camera', 'finish_before_camera')
DESKTOP_SOURCES = tuple(dict.fromkeys((*COLLECTOR_FILES,
    'src/vskill_os/learning/feedback.py',
    'src/vskill_os/learning/camera_feedback.py',
    'src/vskill_os/adapters/camera_ocr.py',
    'src/vskill_os/adapters/learning_desktop.py',
    'src/vskill_os/adapters/virtual_computer.py',
    'src/vskill_os/simulation/desktop.py',
    'src/vskill_os/adapters/isaac/virtual_screen.py',
    'src/vskill_os/adapters/isaac/learning/desktop_env.py',
    'src/vskill_os/adapters/isaac/learning/desktop_smoke.py',
)))


@dataclass(frozen=True, slots=True)
class CameraActorStep(JsonRecord):
    observation: CameraActorObservation
    terminated: bool
    truncated: bool
    failure_codes: tuple[str, ...]


class CameraPolicyPort:
    __slots__ = ('__owner', '__generation')

    def __init__(self, owner, generation):
        self.__owner, self.__generation = owner, generation

    def observe(self):
        return self.__owner.observe(self.__generation)

    def step(self, action):
        return self.__owner.step(self.__generation, action)

    def request_verification(self, purpose='intermediate'):
        return self.__owner.request(self.__generation, purpose)

    def finish(self):
        self.__owner.finish(self.__generation)


class DesktopCameraEnvironment:
    def __init__(self, backend, spec: DesktopCameraSpec):
        import isaaclab.sim as sim_utils
        if (spec.mount_position != (.08, 0., .49)
                or spec.mount_quaternion != (.99875026, 0., .04997917, 0.)):
            raise ValueError('v1 uses the existing audited constant head camera mount')
        self.backend, self.spec = backend, spec
        self.sources = {name: file_sha256(ROOT/name) for name in DESKTOP_SOURCES}
        self.screen = IsaacVirtualScreen(sim_utils.get_current_stage(), backend.output/'bootstrap', display_x=.830)
        # Sensors are authored before any recorded reset. The second simulator
        # initialization enables this camera; subsequent episode resets use T02.
        backend.sim.reset()
        import omni.replicator.core as rep
        # Isaac defaults to temporal DLSS. Its history can retain text across
        # non-sequential episode resets; Replicator recommends FXAA for such
        # captures because it does not accumulate previous frames.
        rep.settings.set_render_rtx_realtime(antialiasing='FXAA')
        sim_utils.get_current_stage().GetRootLayer().Export(str(backend.output/'desktop_scene.usda'))
        self.ocr_runtime = runtime_identity()
        self.queue, self.generation, self.closed = None, 0, False
        self.camera_records, self.texture_records, self.routes = [], [], []
        self.texture_resources = []
        self.screen_closed = False

    def reset(self, reset, case):
        if self.closed or self.queue is not None and not self.queue.closed or case not in CASES:
            raise RuntimeError('finish before reset; closed or unknown camera case')
        if reset.maximum_time_s != self.spec.feedback.horizon_s:
            raise ValueError('physical and feedback horizons must agree')
        if not self.screen_closed:
            self.screen.computer.close()
        base, metadata = self.backend.reset(reset)
        self.case, self.base, self.metadata = case, base, metadata
        self.screen.output = self.backend.episode_dir/'desktop'
        self.screen.computer = VirtualComputer(self.screen.output)
        self.screen.computer.renderer = LearningTerminalRenderer()
        self.screen.computer.desktop.renderer = self.screen.computer.renderer
        self.screen.camera_frames = []
        self.screen_closed = False
        self.screen.computer.desktop.focused = case != 'focus_lost'
        self.queue = CameraFeedbackQueue(base.env_id, base.episode_id, self.spec.feedback)
        self.camera_records, self.texture_records, self.routes = [], [], []
        self.consumed, self.generation = 0, self.generation+1
        self.terminal_pid = self.screen.computer.desktop.process_id
        self.last_published = None
        self._texture(force=True)
        port = CameraPolicyPort(self, self.generation)
        return port, port.observe(), metadata

    def _active(self, generation):
        if self.closed or generation != self.generation or self.queue is None or self.queue.closed:
            raise RuntimeError('stale or closed camera policy port')

    def observe(self, generation):
        self._active(generation)
        return self.queue.observe(self.base)

    def request(self, generation, purpose):
        self._active(generation)
        return self.queue.request(purpose)

    def _texture(self, *, force=False):
        pixels, changed = self.screen.computer.snapshot(self.base.time_s)
        frame = self.screen.computer.frames[-1]
        published = force or changed and self.case != 'frozen_texture'
        if published:
            import omni.ui as ui
            import isaaclab.sim as sim_utils
            from pxr import Sdf, UsdShade
            name = f'vf05_{self.base.episode_id}_{frame["frame_id"]}'
            provider = ui.DynamicTextureProvider(name)
            # Unique resources prevent the renderer from reusing a previous
            # episode's texture. Retain the CPU pixels and provider until exit:
            # uploads are asynchronous relative to physical/control stepping.
            provider.set_data_array(pixels, [pixels.shape[1], pixels.shape[0]])
            self.texture_resources.append((provider, pixels))
            self.texture_uri = 'dynamic://'+name
            texture = UsdShade.Shader.Get(sim_utils.get_current_stage(), '/World/VirtualScreen/Material/Texture')
            texture.GetInput('file').Set(Sdf.AssetPath(self.texture_uri))
            self.last_published = frame['frame_id']
        self.texture_records.append(dict(time_s=self.base.time_s, frame_id=frame['frame_id'], changed=changed,
            published=published, displayed_frame_id=self.last_published, texture_uri=self.texture_uri,
            focused=self.screen.computer.desktop.focused, terminal_pid=self.screen.computer.desktop.process_id,
            terminal_alive=self.screen.computer.desktop.process_id == self.terminal_pid))

    def _capture(self, request, now):
        if self.case == 'missing_camera' and request.purpose == 'final':
            return CameraReadout(request.request_id, now, now, None, None, None, True)
        began = time.monotonic()
        self._texture()
        self.screen.sync_mount(self.backend.robot)
        # Rendering drains the texture/camera pipeline at the same physical
        # state. It does not step physics or advance the declared capture time.
        for _ in range(self.spec.render_frames):
            self.backend.sim.render()
        label = f'request-{len(self.camera_records):03d}'
        self.screen.capture(now, label)
        camera = self.screen.camera_frames[-1]
        import carb
        import isaaclab.sim as sim_utils
        from pxr import UsdShade
        settings = carb.settings.get_settings()
        texture = UsdShade.Shader.Get(sim_utils.get_current_stage(), '/World/VirtualScreen/Material/Texture')
        rgb_path = Path(camera['rgb'])
        ocr = recognize(rgb_path, self.spec, roi_output=rgb_path.with_name(f'roi-{label}.png'))
        body = self.backend.robot.find_bodies(['head_link'])[0][0]
        frame_id = f'{self.base.env_id}:{self.base.episode_id}:camera:{len(self.camera_records)}'
        self.camera_records.append(dict(frame_id=frame_id, request_id=request.request_id, time_s=now,
            physical_sample_id=self.backend.raw_samples[-1].stamp.record_id, camera=camera, ocr=ocr,
            roi_path=str(rgb_path.with_name(f'roi-{label}.png')), displayed_frame_id=self.last_published,
            head_position=self.backend.robot.data.body_link_pos_w[0, body].tolist(),
            head_quaternion=self.backend.robot.data.body_link_quat_w[0, body].tolist(),
            texture_asset=texture.GetInput('file').Get().path,
            antialiasing=settings.get('/rtx/post/aa/op'),
            render_frames=self.spec.render_frames, wall_s=time.monotonic()-began))
        return CameraReadout(request.request_id, now, now, frame_id, ocr['text'], ocr['confidence'], ocr['missing'])

    def step(self, generation, action):
        self._active(generation)
        step = self.backend.step(action)
        self.base = step.observation
        for event in self.backend.events[self.consumed:]:
            # Forward exact identity/key/type/reason. Focus loss is an explicit
            # software routing perturbation; no physical event is removed.
            forwarded = KeyEvent(KeyEventType(event.event_type), event.key, event.stamp.capture_time_s,
                                 'keyboard_event_bridge', event.reason, event.stamp.record_id)
            self.screen.computer.handle_event(forwarded)
            self.routes.append(dict(event=event.to_dict(), dispatch_time_s=self.base.time_s,
                                    focused=self.screen.computer.desktop.focused))
        self.consumed = len(self.backend.events)
        self._texture()
        self.queue.advance(self.base.time_s, self._capture)
        visible = self.observe(generation)
        if step.terminated or step.truncated:
            self._seal()
        return CameraActorStep(visible, step.terminated, step.truncated, step.failure_codes)

    def finish(self, generation):
        self._active(generation)
        self.backend.finish()
        self.observe(generation)
        self._seal()

    def _seal(self):
        if self.queue is None or self.queue.closed:
            return
        cancelled = self.queue.close()
        # Save final desktop truth, but never render/capture/OCR after finish.
        self.screen.computer.snapshot(self.base.time_s)
        desktop = self.screen.computer.close()
        self.screen_closed = True
        directory = self.backend.episode_dir
        physical = EpisodeRecord.read(directory/'episode.json')
        self.spec.write(directory/'desktop_spec.json')
        record = dict(schema_version='vf-desktop-camera-episode/1', case=self.case,
            env_id=self.base.env_id, episode_id=self.base.episode_id, end_time_s=self.base.time_s,
            termination=self.backend.termination, physical_episode_sha256=physical.sha256(),
            routes=self.routes, textures=self.texture_records, camera_frames=self.camera_records,
            attempts=[r.to_dict() for r in self.queue.attempts], captures=[c.to_dict() for c in self.queue.captures],
            deliveries=[d.to_dict() for d in self.queue.deliveries], publications=self.queue.publications,
            cancelled_request_ids=cancelled, terminal_pid=self.terminal_pid,
            desktop=desktop, ocr_runtime=self.ocr_runtime,
            camera_mount=dict(body='/World/G1/head_link', position_local_m=list(self.spec.mount_position),
                quaternion_local_wxyz=list(self.spec.mount_quaternion),
                pose_source='measured_physx_link_pose_times_constant_mount'),
            scope='fixed-pelvis direct-joint single a; actual PTY input, simulated robot RGB-D camera',
            learned_policy=False, real_robot_verified=False, code_agent_executed=False)
        write_json(directory/'desktop_episode.json', record)
        if self.sources != {name: file_sha256(ROOT/name) for name in DESKTOP_SOURCES}:
            raise ValueError('desktop collector source changed during capture')
        files = [directory/'desktop_spec.json', directory/'desktop_episode.json', *sorted((directory/'desktop').glob('*'))]
        write_json(directory/'desktop_manifest.json', dict(schema_version='vf-desktop-camera-bundle/1', sources=self.sources,
            physics_manifest_sha256=file_sha256(directory/'manifest.json'),
            desktop_scene_sha256=file_sha256(self.backend.output/'desktop_scene.usda'),
            files={str(path.relative_to(directory)): file_sha256(path) for path in files if path.is_file()}))

    def close(self):
        if self.closed:
            return
        try:
            self.backend.close()
            self._seal()
        finally:
            if not self.screen_closed:
                self.screen.computer.close()
                self.screen_closed = True
            self.closed = True
