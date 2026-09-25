"""Pixel-only screen evidence and bounded append-editor feedback contracts.

No simulator, pygame, OCR package, or shell is imported by this module.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import struct
import subprocess
import tempfile
import time
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

MAX_TEXT_CHARACTERS = 48
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


@dataclass(frozen=True)
class PixelFrame:
    frame_id: int
    captured_at_s: float
    width: int
    height: int
    rgb: bytes
    source: str
    consumed_event_count: int = 0
    focus_region: tuple[int, int, int, int] = (24, 68, 980, 120)
    text_region: tuple[int, int, int, int] = (24, 154, 980, 224)

    def __post_init__(self) -> None:
        if type(self.frame_id) is not int or self.frame_id < 1:
            raise ValueError("frame ID must be a positive integer")
        if not math.isfinite(self.captured_at_s) or self.captured_at_s < 0:
            raise ValueError("capture timestamp must be finite and nonnegative")
        if not 1 <= self.width <= 4096 or not 1 <= self.height <= 4096 or len(self.rgb) != self.width * self.height * 3:
            raise ValueError("frame must contain a bounded RGB8 image")
        if type(self.consumed_event_count) is not int or self.consumed_event_count < 0:
            raise ValueError("event watermark must be a nonnegative integer")
        for x0, y0, x1, y1 in (self.focus_region, self.text_region):
            if not (0 <= x0 < x1 <= self.width and 0 <= y0 < y1 <= self.height):
                raise ValueError("OCR regions must be inside the captured image")

    @property
    def pixel_sha256(self) -> str:
        return hashlib.sha256(self.rgb).hexdigest()

    def crop_ppm(self, region: tuple[int, int, int, int]) -> bytes:
        x0, y0, x1, y1 = region
        data = b"".join(self.rgb[(y * self.width + x0) * 3:(y * self.width + x1) * 3]
                        for y in range(y0, y1))
        return f"P6\n{x1-x0} {y1-y0}\n255\n".encode() + data


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def encode_png_rgb(width: int, height: int, rgb: bytes) -> bytes:
    if not 1 <= width <= 4096 or not 1 <= height <= 4096 or len(rgb) != width * height * 3:
        raise ValueError("invalid RGB8 image")
    rows = b"".join(b"\0" + rgb[y * width * 3:(y + 1) * width * 3] for y in range(height))
    return (PNG_SIGNATURE + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + _png_chunk(b"IDAT", zlib.compress(rows)) + _png_chunk(b"IEND", b""))


def decode_png_rgb(data: bytes) -> tuple[int, int, bytes]:
    """Validate/read our RGB8 non-interlaced, filter-zero transport PNG subset."""
    if not data.startswith(PNG_SIGNATURE) or len(data) > 64 * 1024 * 1024:
        raise ValueError("invalid or oversized PNG")
    offset, width, height = 8, 0, 0
    compressed = bytearray()
    ended = False
    seen_header = False
    while offset < len(data):
        if offset + 12 > len(data):
            raise ValueError("truncated PNG chunk")
        size = struct.unpack(">I", data[offset:offset + 4])[0]
        kind = data[offset + 4:offset + 8]
        end = offset + 12 + size
        if end > len(data):
            raise ValueError("truncated PNG data")
        content = data[offset + 8:offset + 8 + size]
        crc = struct.unpack(">I", data[offset + 8 + size:end])[0]
        if crc != zlib.crc32(kind + content) & 0xFFFFFFFF:
            raise ValueError("PNG CRC mismatch")
        if kind == b"IHDR":
            if seen_header or offset != 8 or size != 13:
                raise ValueError("invalid PNG header order")
            width, height, bits, color, compression, filtering, interlace = struct.unpack(">IIBBBBB", content)
            if not (1 <= width <= 4096 and 1 <= height <= 4096 and
                    (bits, color, compression, filtering, interlace) == (8, 2, 0, 0, 0)):
                raise ValueError("unsupported PNG format")
            seen_header = True
        elif kind == b"IDAT":
            if not seen_header:
                raise ValueError("PNG data precedes header")
            compressed.extend(content)
        elif kind == b"IEND":
            if size or end != len(data):
                raise ValueError("invalid PNG ending")
            ended = True
            break
        else:
            raise ValueError("unsupported PNG transport chunk")
        offset = end
    if not ended or not compressed:
        raise ValueError("incomplete PNG")
    expected = (width * 3 + 1) * height
    inflater = zlib.decompressobj()
    rows = inflater.decompress(bytes(compressed), expected + 1)
    if len(rows) != expected or not inflater.eof or inflater.unused_data or inflater.unconsumed_tail:
        raise ValueError("invalid or oversized PNG pixel data")
    if any(rows[y * (width * 3 + 1)] != 0 for y in range(height)):
        raise ValueError("only filter-zero transport PNGs are supported")
    rgb = b"".join(rows[y * (width * 3 + 1) + 1:(y + 1) * (width * 3 + 1)] for y in range(height))
    return width, height, rgb


def _atomic_write(path: Path, data: bytes) -> None:
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def publish_frame(frame: PixelFrame, directory: str | Path) -> Path:
    """Publish immutable PNG first, then atomically swap a text-free manifest."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    filename = f"frame-{frame.frame_id:08d}.png"
    png = encode_png_rgb(frame.width, frame.height, frame.rgb)
    path = directory / filename
    if path.exists() and path.read_bytes() != png:
        raise ValueError("refusing to replace a frame ID with different pixels")
    _atomic_write(path, png)
    manifest = {
        "schema_version": 1, "frame_id": frame.frame_id, "captured_at_s": frame.captured_at_s,
        "sha256": hashlib.sha256(png).hexdigest(), "pixel_sha256": frame.pixel_sha256,
        "pixel_size": [frame.width, frame.height], "format": "png", "file": filename,
        "source": frame.source, "consumed_event_count": frame.consumed_event_count,
        "provenance": {"capture": "rendered_rgb_pixels", "renderer": frame.source,
                       "clock": "local_monotonic", "text_oracle": False},
    }
    target = directory / "frame.json"
    _atomic_write(target, (json.dumps(manifest, indent=2, allow_nan=False) + "\n").encode())
    return target


@dataclass(frozen=True)
class ScreenObservation:
    frame_id: int
    captured_at_s: float
    pixel_sha256: str
    text: str
    focused: bool
    append_mode: bool
    consumed_event_count: int
    evidence_kind: str
    recognizer: str
    focus_text: str = ""


class ScreenObserver(Protocol):
    def observe(self, frame: PixelFrame) -> ScreenObservation: ...


class TesseractScreenObserver:
    """Invokes installed local OCR on RGB crops, never reads editor state."""

    def __init__(self, executable: str | None = None, *, timeout_s: float = 5.0):
        self.executable = executable or shutil.which("tesseract")
        if not self.executable:
            raise RuntimeError("tesseract is not installed; pixel OCR is unavailable")
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            raise ValueError("OCR timeout must be finite and positive")
        self.timeout_s = timeout_s

    def _read(self, ppm: bytes) -> str:
        result = subprocess.run([self.executable, "stdin", "stdout", "--psm", "7", "-l", "eng"],
                                input=ppm, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=self.timeout_s, check=False)
        if result.returncode:
            raise RuntimeError("local tesseract OCR failed: " + result.stderr.decode(errors="replace")[:300])
        return result.stdout.decode("utf-8").strip()

    def observe(self, frame: PixelFrame) -> ScreenObservation:
        focus = self._read(frame.crop_ppm(frame.focus_region))
        text = self._read(frame.crop_ppm(frame.text_region))
        tokens = " ".join(focus.upper().split())
        return ScreenObservation(frame.frame_id, frame.captured_at_s, frame.pixel_sha256, text,
                                 tokens == "EDITOR FOCUSED APPEND MODE", "APPEND MODE" in tokens,
                                 frame.consumed_event_count, "pixel_ocr", "local_tesseract", focus)


@dataclass(frozen=True)
class EditPlan:
    expected_text: str
    observed_text: str
    common_prefix_length: int
    backspaces: int
    suffix: str
    frame_id: int
    pixel_sha256: str
    consumed_event_count: int

    @property
    def typing_text(self) -> str:
        return "\b" * self.backspaces + self.suffix


def plan_text_edit(observation: ScreenObservation, expected_text: str) -> EditPlan:
    if observation.evidence_kind != "pixel_ocr":
        raise ValueError("visual feedback planning requires pixel OCR evidence")
    if not observation.focused or not observation.append_mode:
        raise ValueError("editor focus and append mode must be observed in pixels")
    if not expected_text or len(expected_text) > MAX_TEXT_CHARACTERS or not expected_text.isascii() or not expected_text.isprintable():
        raise ValueError("target must be 1 to 48 printable ASCII characters")
    if expected_text != expected_text.strip():
        raise ValueError("leading/trailing whitespace is outside this OCR demo contract")
    if len(observation.text) > MAX_TEXT_CHARACTERS or not observation.text.isascii() or any(c in observation.text for c in "\n\r\t"):
        raise ValueError("observed text is outside the bounded single-line editor contract")
    prefix = 0
    while prefix < min(len(observation.text), len(expected_text)) and observation.text[prefix] == expected_text[prefix]:
        prefix += 1
    return EditPlan(expected_text, observation.text, prefix, len(observation.text) - prefix,
                    expected_text[prefix:], observation.frame_id, observation.pixel_sha256,
                    observation.consumed_event_count)


def assert_plan_current(plan: EditPlan, observation: ScreenObservation) -> None:
    if (observation.evidence_kind != "pixel_ocr" or not observation.focused or not observation.append_mode
            or observation.frame_id < plan.frame_id or observation.pixel_sha256 != plan.pixel_sha256
            or observation.consumed_event_count != plan.consumed_event_count or observation.text != plan.observed_text):
        raise ValueError("SCREEN_CHANGED_BEFORE_EDIT: discard the plan and observe again")


@dataclass(frozen=True)
class FeedbackVerification:
    passed: bool
    failure_code: str | None
    expected_text: str
    observed_text: str
    frame_id: int
    pixel_sha256: str
    evidence_kind: str
    physical_acceptance: bool = False


class ScreenFeedbackVerifier:
    def __init__(self, *, maximum_frame_age_s: float = 10.0, observer: ScreenObserver | None = None):
        if not math.isfinite(maximum_frame_age_s) or maximum_frame_age_s <= 0:
            raise ValueError("maximum frame age must be finite and positive")
        self.maximum_frame_age_s = maximum_frame_age_s
        self.observer = observer

    def verify(self, *, expected_text: str, before: ScreenObservation, after: ScreenObservation,
               frame: PixelFrame, last_event_at_s: float | None = None,
               now_s: float | None = None) -> FeedbackVerification:
        now_s = time.monotonic() if now_s is None else now_s
        failure = None
        if (after.evidence_kind != "pixel_ocr" or before.evidence_kind != "pixel_ocr"):
            failure = "NON_VISUAL_EVIDENCE"
        elif (after.frame_id != frame.frame_id or after.captured_at_s != frame.captured_at_s
              or after.pixel_sha256 != frame.pixel_sha256
              or after.consumed_event_count != frame.consumed_event_count):
            failure = "FRAME_EVIDENCE_MISMATCH"
        elif (after.frame_id <= before.frame_id or not math.isfinite(before.captured_at_s)
              or after.captured_at_s <= before.captured_at_s
              or not math.isfinite(now_s) or now_s < after.captured_at_s
              or now_s - after.captured_at_s > self.maximum_frame_age_s):
            failure = "STALE_SCREEN_FRAME"
        elif last_event_at_s is not None and (not math.isfinite(last_event_at_s) or after.captured_at_s < last_event_at_s):
            failure = "FRAME_PRECEDES_INPUT"
        elif (after.consumed_event_count < before.consumed_event_count
              or (before.text != expected_text and after.consumed_event_count <= before.consumed_event_count)):
            failure = "MISSING_INPUT_EVIDENCE"
        elif not after.focused or not after.append_mode:
            failure = "WRONG_EDITOR_FOCUS_OR_MODE"
        elif after.text != expected_text:
            failure = "SCREEN_TEXT_MISMATCH"
        if failure is None:
            # Independently re-read the captured pixels; a forged text field
            # paired with the correct image digest is not verification evidence.
            independent = (self.observer or TesseractScreenObserver()).observe(frame)
            if (independent.evidence_kind != "pixel_ocr" or independent.frame_id != frame.frame_id
                    or independent.pixel_sha256 != frame.pixel_sha256
                    or independent.text != after.text or independent.focused != after.focused
                    or independent.append_mode != after.append_mode):
                failure = "OCR_EVIDENCE_MISMATCH"
        return FeedbackVerification(failure is None, failure, expected_text, after.text,
                                    after.frame_id, after.pixel_sha256, after.evidence_kind)
