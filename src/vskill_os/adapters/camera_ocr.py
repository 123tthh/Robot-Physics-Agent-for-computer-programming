"""OCR derives only from saved robot RGB pixels and a frozen calibration ROI."""
import csv
import hashlib
import io
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

from ..learning.camera_feedback import DesktopCameraSpec


def image_region(path: Path, spec: DesktopCameraSpec):
    from PIL import Image, ImageOps
    with Image.open(path) as image:
        if image.size != spec.image_size:
            raise ValueError('camera RGB dimensions differ from calibration')
        roi = image.convert('RGB').crop(spec.input_roi)
    roi = roi.resize((roi.width*spec.ocr_scale, roi.height*spec.ocr_scale), Image.Resampling.BICUBIC)
    return ImageOps.autocontrast(ImageOps.grayscale(roi))


def recognize(path: Path, spec: DesktopCameraSpec, *, roi_output: Path | None = None) -> dict:
    roi = image_region(path, spec)
    began = time.monotonic()
    with tempfile.TemporaryDirectory(prefix='vf05-ocr-') as temporary:
        input_path = roi_output or Path(temporary)/'input.png'
        roi.save(input_path)
        command = ['tesseract', str(input_path), 'stdout', '--psm', '7', '-l', 'eng', 'tsv']
        result = subprocess.run(command, capture_output=True, text=True, timeout=spec.ocr_timeout_s)
    if result.returncode:
        raise RuntimeError('CAMERA_OCR_PROCESS_FAILED: '+result.stderr)
    words = [row for row in csv.DictReader(io.StringIO(result.stdout), delimiter='\t')
             if row['level'] == '5' and row['text'].strip() and float(row['conf']) >= 0]
    return dict(source='tesseract_robot_rgb_roi_v1', text=' '.join(row['text'] for row in words) if words else None,
                confidence=min(float(row['conf']) for row in words)/100 if words else None,
                missing=not words, roi=list(spec.input_roi), scale=spec.ocr_scale,
                roi_pixels_sha256=hashlib.sha256(roi.tobytes()).hexdigest(), tsv=result.stdout,
                wall_s=time.monotonic()-began)


def runtime_identity():
    from PIL import __version__ as pillow_version
    executable = Path(shutil.which('tesseract') or 'missing-tesseract')
    result = subprocess.run([str(executable), '--list-langs'], capture_output=True, text=True, timeout=10)
    first = (result.stdout+result.stderr).splitlines()[0]
    directory = Path(first.split('"')[1])
    model = directory/'eng.traineddata'
    version = subprocess.run([str(executable), '--version'], capture_output=True, text=True, timeout=10).stdout.splitlines()[0]
    return dict(tesseract=version, executable_sha256=hashlib.sha256(executable.read_bytes()).hexdigest(),
                english_model_sha256=hashlib.sha256(model.read_bytes()).hexdigest(), pillow=pillow_version)
