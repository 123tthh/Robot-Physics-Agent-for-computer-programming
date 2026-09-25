"""Versioned desktop pixels -> Isaac monitor -> independently captured camera pixels.

Importing this module never imports Kit, CUDA, NumPy, Pillow or OpenUSD. File
transport and camera freshness gates are ordinary-Python testable. The optional
Isaac adapter must be constructed after SimulationApp and world initialization.
Texture submission is deliberately not a claim that the robot read the screen.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from fractions import Fraction
import hashlib
import json
import math
from numbers import Integral
import os
from pathlib import Path
import re
import stat
import tempfile
import time
from typing import Callable, Mapping


class ScreenFrameError(ValueError):
    """A frame cannot be used as fresh, synchronized pixel evidence."""


def _integer(value: object, name: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral) or value < minimum:
        raise ScreenFrameError(f'{name} must be an integer >= {minimum}')
    return int(value)


def render_marker(value: object) -> Fraction:
    """Isaac 6 Camera exposes fabric reference-time dictionaries, not only ints."""
    if isinstance(value, Mapping):
        try:
            return Fraction(_integer(value['referenceTimeNumerator'], 'referenceTimeNumerator'),
                            _integer(value['referenceTimeDenominator'], 'referenceTimeDenominator', 1))
        except KeyError as exc:
            raise ScreenFrameError('camera reference timestamp is incomplete') from exc
    return Fraction(_integer(value, 'rendering_frame'), 1)


def _timestamp(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise ScreenFrameError(f'{name} must be a finite timestamp')
    value = float(value)
    if not math.isfinite(value) or value < 0:
        raise ScreenFrameError(f'{name} must be finite and nonnegative')
    return value


def _read_regular_file(root: Path, name: str, maximum: int) -> bytes:
    if not isinstance(name, str) or not name or Path(name).name != name or name in {'.', '..'}:
        raise ScreenFrameError('frame file must be a relative basename')
    if '/' in name or '\\' in name:
        raise ScreenFrameError('frame file must stay inside the transport directory')
    try:
        fd = os.open(root / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
                raise ScreenFrameError('frame input must be a bounded regular file')
            data = stream.read(maximum + 1)
    except OSError as exc:
        raise ScreenFrameError(f'cannot read frame input: {exc}') from exc
    if len(data) > maximum:
        raise ScreenFrameError('frame input exceeds size limit')
    return data


@dataclass(frozen=True)
class ScreenPixels:
    frame_id: int
    captured_at_s: float
    consumed_event_count: int
    width: int
    height: int
    sha256: str
    pixel_sha256: str
    source: str
    provenance: dict
    png_bytes: bytes

    def metadata(self) -> dict:
        return {key: value for key, value in asdict(self).items() if key != 'png_bytes'}


class FrameReceiver:
    """Accept immutable producer PNGs only after validating all pixel metadata.

    A producer restart needs a new receiver; sequence rollback never silently
    starts a new session. The clock is system monotonic time, shared by local
    processes, not wall time and not simulation time.
    """

    def __init__(self, directory: Path, *, max_age_s: float = 5.0,
                 clock: Callable[[], float] = time.monotonic, max_pixels: int = 8_294_400):
        self.directory = Path(directory).resolve()
        self.max_age_s = _timestamp(max_age_s, 'max_age_s')
        if self.max_age_s <= 0:
            raise ScreenFrameError('max_age_s must be positive')
        self.clock = clock
        self.max_pixels = _integer(max_pixels, 'max_pixels', 1)
        self.last_frame_id = -1
        self.last_timestamp = -1.0
        self.last_event_count = -1

    def receive(self, *, now_s: float | None = None, minimum_event_count: int = 0) -> ScreenPixels:
        from vskill_os.simulation.screen_feedback import decode_png_rgb

        minimum_event_count = _integer(minimum_event_count, 'minimum_event_count')
        now = _timestamp(self.clock() if now_s is None else now_s, 'now_s')
        try:
            metadata = json.loads(_read_regular_file(self.directory, 'frame.json', 16_384))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ScreenFrameError('invalid frame metadata JSON') from exc
        if not isinstance(metadata, dict) or metadata.get('schema_version') != 1 or type(metadata.get('schema_version')) is not int:
            raise ScreenFrameError('unsupported frame schema_version')
        try:
            frame_id = _integer(metadata['frame_id'], 'frame_id')
            timestamp = _timestamp(metadata['captured_at_s'], 'captured_at_s')
            events = _integer(metadata['consumed_event_count'], 'consumed_event_count')
            size = metadata['pixel_size']
            if not isinstance(size, list) or len(size) != 2:
                raise ScreenFrameError('pixel_size must contain width and height')
            width, height = (_integer(value, 'pixel dimension', 1) for value in size)
            if width * height > self.max_pixels:
                raise ScreenFrameError('frame pixel count exceeds size limit')
            if metadata['format'] != 'png' or metadata['source'] not in {'pygame_desktop', 'pillow_desktop'}:
                raise ScreenFrameError('unsupported desktop pixel source or format')
            if (not isinstance(metadata['provenance'], dict)
                    or metadata['provenance'].get('capture') != 'rendered_rgb_pixels'
                    or metadata['provenance'].get('clock') != 'local_monotonic'
                    or metadata['provenance'].get('text_oracle') is not False):
                raise ScreenFrameError('desktop frame must identify rendered RGB pixels')
            for key in ('sha256', 'pixel_sha256'):
                if not isinstance(metadata[key], str) or re.fullmatch('[0-9a-f]{64}', metadata[key]) is None:
                    raise ScreenFrameError(f'invalid {key}')
            if frame_id <= self.last_frame_id or timestamp < self.last_timestamp:
                raise ScreenFrameError('duplicate or out-of-order frame')
            if events < max(minimum_event_count, self.last_event_count):
                raise ScreenFrameError('frame predates required consumed keyboard events')
            if timestamp > now + 0.05 or now - timestamp > self.max_age_s:
                raise ScreenFrameError('desktop frame timestamp is stale or in the future')
            png = _read_regular_file(self.directory, metadata['file'], 32 * 1024 * 1024)
            if hashlib.sha256(png).hexdigest() != metadata['sha256']:
                raise ScreenFrameError('PNG digest does not match the published frame')
            # Refuse oversized/deceptive IHDR before asking the decoder to allocate.
            if len(png) < 33 or png[:8] != b'\x89PNG\r\n\x1a\n':
                raise ScreenFrameError('invalid PNG signature or header')
            if (int.from_bytes(png[16:20], 'big'), int.from_bytes(png[20:24], 'big')) != (width, height):
                raise ScreenFrameError('PNG dimensions disagree with frame metadata')
            actual_width, actual_height, rgb = decode_png_rgb(png)
            if (actual_width, actual_height) != (width, height) or len(rgb) != width * height * 3:
                raise ScreenFrameError('decoded PNG size disagrees with frame metadata')
            if hashlib.sha256(rgb).hexdigest() != metadata['pixel_sha256']:
                raise ScreenFrameError('RGB digest does not match the published frame')
        except KeyError as exc:
            raise ScreenFrameError(f'missing frame field: {exc}') from exc
        except ScreenFrameError:
            raise
        except (ValueError, TypeError, OverflowError) as exc:
            raise ScreenFrameError(f'invalid frame pixels: {exc}') from exc
        self.last_frame_id, self.last_timestamp, self.last_event_count = frame_id, timestamp, events
        provenance = {name: metadata['provenance'].get(name) for name in ('capture', 'renderer', 'clock', 'text_oracle')}
        return ScreenPixels(frame_id, timestamp, events, width, height, metadata['sha256'],
                            metadata['pixel_sha256'], metadata['source'], provenance, png)


class CameraFrameGate:
    """Require a new render after texture submission, without asserting its text."""

    def __init__(self):
        self.submission: ScreenPixels | None = None
        self.render_baselines: dict[str, Fraction] = {}
        self.last_captured: dict[str, Fraction] = {}
        self.applied_simulation_time_s = 0.0

    def texture_applied(self, frame: ScreenPixels, *, rendering_frames: Mapping[str, object], simulation_time_s: float) -> None:
        baselines = {name: render_marker(value) for name, value in rendering_frames.items()}
        simulation_time = _timestamp(simulation_time_s, 'simulation_time_s')
        if self.submission and frame.frame_id <= self.submission.frame_id:
            raise ScreenFrameError('texture submissions must advance frame_id')
        self.submission, self.render_baselines = frame, baselines
        self.applied_simulation_time_s = simulation_time

    def validate(self, camera_name: str, *, rendering_frame: object, rendering_time_s: float) -> dict:
        frame = render_marker(rendering_frame)
        render_time = _timestamp(rendering_time_s, 'rendering_time_s')
        if self.submission is None or camera_name not in self.render_baselines:
            raise ScreenFrameError('camera has no corresponding texture submission')
        if frame <= max(self.render_baselines[camera_name], self.last_captured.get(camera_name, -1)):
            raise ScreenFrameError('camera pixels did not advance after texture submission')
        if render_time + 1e-8 < self.applied_simulation_time_s:
            raise ScreenFrameError('camera rendering time predates texture submission')
        self.last_captured[camera_name] = frame
        return {
            'texture_submission_frame_id': self.submission.frame_id,
            'texture_submission_sha256': self.submission.sha256,
            'texture_consumed_event_count': self.submission.consumed_event_count,
            'texture_applied_simulation_time_s': self.applied_simulation_time_s,
            'display_content_verified': False,
            'screen_text_from_privileged_state': False,
        }


def find_robot_camera_mount(stage, robot_prim_path: str = '/World/G1', link_name: str = 'd435_link') -> str:
    """Resolve the actual sensor frame below the robot, without guessing nesting."""
    from pxr import Usd, UsdGeom

    robot = stage.GetPrimAtPath(robot_prim_path)
    if not robot.IsValid():
        raise ScreenFrameError('robot prim for camera mounting does not exist')
    matches = [str(prim.GetPath()) for prim in Usd.PrimRange(robot)
               if prim.GetName() == link_name and UsdGeom.Xformable(prim)]
    if len(matches) != 1:
        raise ScreenFrameError(f'expected one {link_name} camera mount below robot; found {matches}')
    return matches[0]


def verify_robot_camera_text(metadata: dict, *, expected_text: str,
                             focus_region: tuple[int, int, int, int], text_region: tuple[int, int, int, int],
                             now_s: float | None = None, maximum_age_s: float = 10.0) -> dict:
    """Run local OCR on a saved robot-camera PNG, never on texture/editor text.

    Regions must be calibrated in actual camera pixel coordinates (or rectified
    by an explicitly recorded upstream vision transform). A failed OCR/focus
    check remains a failure; this helper does not substitute expected text.
    """
    if (metadata.get('source') != 'isaac_camera' or metadata.get('evidence_type') != 'rendered_camera_rgb'
            or not metadata.get('camera_mount_prim_path')
            or metadata.get('screen_text_from_privileged_state') is not False):
        raise ScreenFrameError('robot visual verification requires mounted Isaac-camera pixel provenance')
    now = _timestamp(time.monotonic() if now_s is None else now_s, 'now_s')
    captured = _timestamp(metadata.get('captured_at_s'), 'captured_at_s')
    maximum_age_s = _timestamp(maximum_age_s, 'maximum_age_s')
    if not 0 <= now - captured <= maximum_age_s:
        raise ScreenFrameError('robot-camera pixels are stale or from the future')
    if not isinstance(expected_text, str) or not expected_text or len(expected_text) > 48:
        raise ScreenFrameError('expected text must contain 1 to 48 characters')
    path = Path(metadata['file'])
    png = _read_regular_file(path.parent.resolve(), path.name, 32 * 1024 * 1024)
    if hashlib.sha256(png).hexdigest() != metadata.get('sha256'):
        raise ScreenFrameError('camera PNG changed after provenance capture')
    from PIL import Image
    import io
    from vskill_os.simulation.screen_feedback import PixelFrame, TesseractScreenObserver

    image = Image.open(io.BytesIO(png))
    if image.format != 'PNG' or list(image.size) != metadata.get('pixel_size') or image.width * image.height > 8_294_400:
        raise ScreenFrameError('camera PNG dimensions or format disagree with provenance')
    image = image.convert('RGB')
    marker = metadata['rendering_reference']
    frame_id = max(1, _integer(marker['numerator'], 'render reference numerator'))
    pixels = PixelFrame(frame_id, captured, image.width, image.height, image.tobytes(), 'isaac_camera',
                        _integer(metadata['texture_consumed_event_count'], 'event watermark'), focus_region, text_region)
    observed = TesseractScreenObserver().observe(pixels)
    verified = (observed.evidence_kind == 'pixel_ocr' and observed.pixel_sha256 == pixels.pixel_sha256
                and observed.focused and observed.append_mode and observed.text == expected_text)
    return {
        'schema_version': 1, 'robot_visual_verified': verified, 'physical_acceptance': False,
        'evidence_kind': 'robot_camera_pixel_ocr', 'camera_png': str(path),
        'camera_png_sha256': metadata['sha256'], 'camera_prim_path': metadata['camera_prim_path'],
        'rendering_reference': metadata['rendering_reference'], 'pixel_sha256': pixels.pixel_sha256,
        'expected_text': expected_text, 'observed_text': observed.text, 'observed_focus': observed.focus_text,
        'focus_region': list(focus_region), 'text_region': list(text_region),
        'recognizer': observed.recognizer, 'screen_text_from_privileged_state': False,
        'failure_code': None if verified else 'ROBOT_CAMERA_OCR_TEXT_OR_FOCUS_MISMATCH',
    }


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.frame-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


class IsaacMonitorScreen:
    """A UV-mapped, emissive monitor and separately captured robot/overview cameras.

    Build after SimulationApp startup. Call add_camera after world.reset, then
    update_from_transport, render, and capture. This object does not step physics
    or render, so the driver retains a single centralized simulation clock.
    """

    def __init__(self, stage, *, transport_dir: Path, output_dir: Path,
                 prim_path: str = '/World/DesktopMonitor', center=(1.08, 0.0, 1.03),
                 width_m: float = 0.48, height_m: float = 0.27, max_age_s: float = 5.0):
        from pxr import Gf, Sdf, UsdGeom, UsdShade

        if width_m <= 0 or height_m <= 0 or len(center) != 3 or not all(math.isfinite(value) for value in (*center, width_m, height_m)):
            raise ScreenFrameError('monitor geometry must be finite and positive')
        self.stage, self.prim_path = stage, prim_path
        self.output_dir = Path(output_dir).resolve()
        self.receiver = FrameReceiver(transport_dir, max_age_s=max_age_s)
        self.gate = CameraFrameGate()
        self.cameras: dict[str, object] = {}
        self.camera_mounts: dict[str, str | None] = {}
        root = UsdGeom.Xform.Define(stage, prim_path)
        root.AddTranslateOp().Set(Gf.Vec3d(*center))
        mesh = UsdGeom.Mesh.Define(stage, prim_path + '/Display')
        # Front faces the robot at -X; +Y is the operator's left.
        mesh.CreatePointsAttr([(0, width_m/2, -height_m/2), (0, -width_m/2, -height_m/2),
                              (0, -width_m/2, height_m/2), (0, width_m/2, height_m/2)])
        mesh.CreateFaceVertexCountsAttr([4])
        mesh.CreateFaceVertexIndicesAttr([0, 1, 2, 3])
        mesh.CreateNormalsAttr([(-1, 0, 0)] * 4)
        mesh.SetNormalsInterpolation(UsdGeom.Tokens.vertex)
        mesh.CreateSubdivisionSchemeAttr('none')
        mesh.CreateDoubleSidedAttr(True)
        uv = UsdGeom.PrimvarsAPI(mesh).CreatePrimvar('st', Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.vertex)
        uv.Set([(0, 0), (1, 0), (1, 1), (0, 1)])
        material = UsdShade.Material.Define(stage, prim_path + '/Material')
        shader = UsdShade.Shader.Define(stage, prim_path + '/Material/Surface')
        shader.CreateIdAttr('UsdPreviewSurface')
        shader.CreateInput('diffuseColor', Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(0))
        shader.CreateInput('roughness', Sdf.ValueTypeNames.Float).Set(1.0)
        texture = UsdShade.Shader.Define(stage, prim_path + '/Material/Pixels')
        texture.CreateIdAttr('UsdUVTexture')
        texture.CreateInput('sourceColorSpace', Sdf.ValueTypeNames.Token).Set('sRGB')
        texture.CreateInput('wrapS', Sdf.ValueTypeNames.Token).Set('clamp')
        texture.CreateInput('wrapT', Sdf.ValueTypeNames.Token).Set('clamp')
        self.texture_input = texture.CreateInput('file', Sdf.ValueTypeNames.Asset)
        reader = UsdShade.Shader.Define(stage, prim_path + '/Material/UV')
        reader.CreateIdAttr('UsdPrimvarReader_float2')
        reader.CreateInput('varname', Sdf.ValueTypeNames.Token).Set('st')
        texture.CreateInput('st', Sdf.ValueTypeNames.Float2).ConnectToSource(reader.CreateOutput('result', Sdf.ValueTypeNames.Float2))
        shader.CreateInput('emissiveColor', Sdf.ValueTypeNames.Color3f).ConnectToSource(texture.CreateOutput('rgb', Sdf.ValueTypeNames.Float3))
        material.CreateSurfaceOutput().ConnectToSource(shader.CreateOutput('surface', Sdf.ValueTypeNames.Token))
        UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim()).Bind(material)
        backing = UsdGeom.Cube.Define(stage, prim_path + '/Backing')
        backing.CreateSizeAttr(1)
        backing.AddTranslateOp().Set(Gf.Vec3d(0.012, 0, 0))
        backing.AddScaleOp().Set(Gf.Vec3f(0.02, width_m + 0.025, height_m + 0.025))
        backing.CreateDisplayColorAttr([Gf.Vec3f(0.025)])

    def add_camera(self, name: str, *, eye_world: tuple[float, float, float], target_world: tuple[float, float, float],
                   mount_prim_path: str | None = None, resolution=(960, 540)) -> str:
        import numpy as np
        from pxr import Gf
        from isaacsim.sensors.camera import Camera

        if not re.fullmatch('[A-Za-z][A-Za-z0-9_]*', name) or name in self.cameras:
            raise ScreenFrameError('camera name must be unique and contain only letters/digits/underscores')
        if mount_prim_path is not None and not self.stage.GetPrimAtPath(mount_prim_path).IsValid():
            raise ScreenFrameError('robot camera mount does not exist')
        parent = mount_prim_path or self.prim_path
        path = parent + '/' + name
        camera = Camera(prim_path=path, resolution=resolution)
        look = Gf.Matrix4d().SetLookAt(Gf.Vec3d(*eye_world), Gf.Vec3d(*target_world), Gf.Vec3d(0, 0, 1)).GetInverse()
        quat = look.ExtractRotationQuat()
        camera.set_world_pose(position=np.asarray(eye_world),
                              orientation=np.array([quat.GetReal(), *quat.GetImaginary()]), camera_axes='usd')
        camera.initialize()
        self.cameras[name], self.camera_mounts[name] = camera, mount_prim_path
        return path

    def update_from_transport(self, *, simulation_time_s: float, minimum_event_count: int = 0, now_s: float | None = None) -> dict:
        from pxr import Sdf

        frame = self.receiver.receive(now_s=now_s, minimum_event_count=minimum_event_count)
        # Copy already-verified bytes to a content-addressed local path. Changing
        # the URL avoids reusing a cached texture when a producer overwrites PNGs.
        texture_path = self.output_dir / 'textures' / f'{frame.frame_id:012d}-{frame.sha256}.png'
        _atomic_write(texture_path, frame.png_bytes)
        baselines = {name: camera.get_current_frame()['rendering_frame'] for name, camera in self.cameras.items()}
        self.texture_input.Set(Sdf.AssetPath(str(texture_path)))
        self.gate.texture_applied(frame, rendering_frames=baselines, simulation_time_s=simulation_time_s)
        return {**frame.metadata(), 'texture_path': str(texture_path), 'display_content_verified': False}

    def capture(self, camera_name: str, *, physics_tick: int, simulation_time_s: float) -> dict:
        from PIL import Image
        import io

        physics_tick = _integer(physics_tick, 'physics_tick')
        simulation_time_s = _timestamp(simulation_time_s, 'simulation_time_s')
        if camera_name not in self.cameras:
            raise ScreenFrameError('unknown camera')
        camera = self.cameras[camera_name]
        current = camera.get_current_frame(clone=True)
        rgba = current.get('rgba')
        if rgba is None or getattr(rgba, 'size', 0) == 0 or len(rgba.shape) != 3 or rgba.shape[2] not in {3, 4}:
            raise ScreenFrameError('camera has not produced RGB pixels')
        linkage = self.gate.validate(camera_name, rendering_frame=current['rendering_frame'],
                                     rendering_time_s=float(current['rendering_time']))
        image = Image.fromarray(rgba.astype('uint8')).convert('RGB')
        stream = io.BytesIO()
        image.save(stream, format='PNG')
        data = stream.getvalue()
        marker = render_marker(current['rendering_frame'])
        path = self.output_dir / 'observations' / f'{camera_name}-{marker.numerator:012d}-{marker.denominator}.png'
        _atomic_write(path, data)
        position, orientation = camera.get_world_pose(camera_axes='usd')
        metadata = {
            'schema_version': 1, 'source': 'isaac_camera', 'evidence_type': 'rendered_camera_rgb',
            'camera_name': camera_name, 'camera_prim_path': camera.prim_path,
            'camera_mount_prim_path': self.camera_mounts[camera_name],
            'captured_at_s': time.monotonic(), 'physics_tick': physics_tick,
            'simulation_time_s': simulation_time_s,
            'rendering_reference': {'numerator': marker.numerator, 'denominator': marker.denominator},
            'rendering_time_s': float(current['rendering_time']), 'pixel_size': list(image.size),
            'file': str(path), 'sha256': hashlib.sha256(data).hexdigest(),
            'camera_position_world': [float(v) for v in position],
            'camera_orientation_world_wxyz': [float(v) for v in orientation],
            'camera_axes': 'usd', **linkage,
        }
        _atomic_write(path.with_suffix('.json'), (json.dumps(metadata, indent=2) + '\n').encode())
        return metadata
