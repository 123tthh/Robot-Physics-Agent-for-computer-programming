"""Optional pixel-rendered safe editor; no PTY, shell, or command execution."""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import time
from dataclasses import asdict
from pathlib import Path

from vskill_os.simulation.keyboard_bridge import KeyEvent, KeyEventType
from vskill_os.simulation.screen_feedback import (
    MAX_TEXT_CHARACTERS, PixelFrame, ScreenFeedbackVerifier, ScreenObservation,
    TesseractScreenObserver, assert_plan_current, plan_text_edit, publish_frame,
)
from vskill_os.typing import SHIFTED

FONT_PATH = Path("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf")


class SafeTextEditor:
    """Bounded, single-line append/backspace editor with cursor always at end."""

    def __init__(self, initial_text: str = ""):
        if len(initial_text) > MAX_TEXT_CHARACTERS or not initial_text.isascii() or any(not c.isprintable() for c in initial_text):
            raise ValueError("editor text must contain at most 48 printable ASCII characters")
        self._text = initial_text
        self.focused = True
        self.keys_down: set[str] = set()
        self.consumed_event_count = 0
        self.last_event_at_s: float | None = None
        self._last_source_times: dict[str, float] = {}
        self.event_sources: set[str] = set()
        self._event_ids: set[str] = set()

    def handle_event(self, event: KeyEvent) -> None:
        if event.event_id and event.event_id in self._event_ids:
            raise ValueError("duplicate keyboard event ID")
        if event.monotonic_time < self._last_source_times.get(event.source, float("-inf")):
            raise ValueError("out-of-order keyboard event")
        if event.key in {"left_ctrl", "right_ctrl", "left_alt", "right_alt", "enter", "tab"}:
            raise ValueError("shell shortcuts and multiline editing are outside this safe editor contract")
        if event.event_type == KeyEventType.KEYUP:
            if event.key not in self.keys_down:
                raise ValueError("keyup has no corresponding keydown")
            self.keys_down.remove(event.key)
        elif event.event_type == KeyEventType.KEYDOWN:
            if event.key in self.keys_down:
                raise ValueError("repeated keydown without release")
            if event.key not in {"left_shift", "right_shift", "backspace", "space"} and (len(event.key) != 1 or not event.key.isascii() or not event.key.isprintable()):
                raise ValueError("unsupported key in append-only editor")
            self.keys_down.add(event.key)
            if self.focused and event.key == "backspace":
                self._text = self._text[:-1]
            elif self.focused and event.key not in {"left_shift", "right_shift"}:
                character = " " if event.key == "space" else event.key
                if self.keys_down & {"left_shift", "right_shift"}:
                    character = {value: key for key, value in SHIFTED.items()}.get(character, character.upper())
                if len(self._text) >= MAX_TEXT_CHARACTERS:
                    self.keys_down.remove(event.key)
                    raise ValueError("bounded editor capacity exceeded")
                self._text += character
        else:
            raise ValueError("unsupported keyboard event type")
        self.consumed_event_count += 1
        # Host receive time is comparable with PixelFrame's local monotonic
        # clock, while Isaac key events may carry simulation time instead.
        self.last_event_at_s = time.monotonic()
        self._last_source_times[event.source] = event.monotonic_time
        self.event_sources.add(event.source)
        if event.event_id:
            self._event_ids.add(event.event_id)

    def render_content(self) -> tuple[str, bool]:
        """Privileged UI rendering input; never hand this output to perception."""
        return self._text, self.focused

    def observe_text_oracle(self, frame: PixelFrame) -> ScreenObservation:
        """Explicit non-visual test oracle, rejected by visual plan/verification."""
        return ScreenObservation(frame.frame_id, frame.captured_at_s, frame.pixel_sha256,
                                 self._text, self.focused, True, self.consumed_event_count,
                                 "synthetic_text_oracle", "editor_internal_state")


class PixelDesktop:
    """Capture actual UI surface RGB; backend selection never starts Isaac."""

    def __init__(self, editor: SafeTextEditor, *, backend: str = "auto", visible: bool = False,
                 width: int = 1024, height: int = 320):
        if width != 1024 or height != 320:
            raise ValueError("this version uses the calibrated 1024x320 editor layout")
        if backend == "auto":
            backend = "pygame" if importlib.util.find_spec("pygame") else "pillow"
        if backend not in {"pygame", "pillow"}:
            raise ValueError("renderer must be auto, pygame, or pillow")
        if not FONT_PATH.is_file():
            raise RuntimeError(f"the calibrated OCR font is missing: {FONT_PATH}")
        self.editor, self.backend, self.visible = editor, backend, visible
        self.width, self.height = width, height
        self.frame_id = 0
        if backend == "pygame":
            import pygame
            self._pygame = pygame
            pygame.font.init()
            self._fonts = {size: pygame.font.Font(str(FONT_PATH), size) for size in (20, 26, 30)}
            if visible:
                pygame.display.init()
                self._surface = pygame.display.set_mode((width, height))
                pygame.display.set_caption("Dex3 safe append editor")
            else:
                self._surface = pygame.Surface((width, height))
        else:
            if visible:
                raise ValueError("visible windows require the pygame backend")
            from PIL import Image, ImageDraw, ImageFont
            self._image_cls, self._draw_cls = Image, ImageDraw
            self._fonts = {size: ImageFont.truetype(str(FONT_PATH), size) for size in (20, 26, 30)}

    def capture(self) -> PixelFrame:
        text, focused = self.editor.render_content()
        lines = (
            ((30, 18), "DEX3 LOCAL EDITOR", 20, (45, 53, 62)),
            ((30, 76), ("EDITOR FOCUSED" if focused else "OTHER FOCUSED") + "   APPEND MODE", 26, (20, 20, 20)),
            ((36, 162), text, 30, (0, 0, 0)),
            ((30, 266), "Single line / cursor at end / no command execution", 20, (60, 65, 70)),
        )
        if self.backend == "pygame":
            self._surface.fill((255, 255, 255))
            for position, content, size, color in lines:
                self._surface.blit(self._fonts[size].render(content, True, color), position)
            if self.visible:
                self._pygame.event.pump()
                self._pygame.display.flip()
            rgb = self._pygame.image.tobytes(self._surface, "RGB")
        else:
            image = self._image_cls.new("RGB", (self.width, self.height), (255, 255, 255))
            drawing = self._draw_cls.Draw(image)
            for position, content, size, color in lines:
                drawing.text(position, content, font=self._fonts[size], fill=color)
            rgb = image.tobytes()
        self.frame_id += 1
        return PixelFrame(self.frame_id, time.monotonic(), self.width, self.height, rgb,
                          f"{self.backend}_desktop", self.editor.consumed_event_count)

    def close(self) -> None:
        if self.backend == "pygame":
            if self.visible:
                self._pygame.display.quit()
            self._pygame.font.quit()


def replay_text_events(editor: SafeTextEditor, text: str, *, source: str = "cpu_synthetic_key_events") -> None:
    """CPU test input replay only; the robot path must supply physical bridge events."""
    for character in text:
        key = "backspace" if character == "\b" else "space" if character == " " else SHIFTED.get(character, character.lower())
        shifted = character.isupper() or character in SHIFTED
        if shifted:
            editor.handle_event(KeyEvent(KeyEventType.KEYDOWN, "left_shift", time.monotonic(), source))
        editor.handle_event(KeyEvent(KeyEventType.KEYDOWN, key, time.monotonic(), source))
        editor.handle_event(KeyEvent(KeyEventType.KEYUP, key, time.monotonic(), source))
        if shifted:
            editor.handle_event(KeyEvent(KeyEventType.KEYUP, "left_shift", time.monotonic(), source))


def run_demo(*, output: Path, initial_text: str = "helo world", target_text: str = "hello world",
             backend: str = "auto", visible: bool = False) -> dict:
    editor = SafeTextEditor(initial_text)
    desktop = PixelDesktop(editor, backend=backend, visible=visible)
    observer = TesseractScreenObserver()
    try:
        before_frame = desktop.capture()
        publish_frame(before_frame, output / "before")
        before = observer.observe(before_frame)
        plan = plan_text_edit(before, target_text)
        # Optimistic precondition re-observation catches intervening focus/text
        # changes before any edit. It compares pixels and event watermark.
        current = observer.observe(desktop.capture())
        assert_plan_current(plan, current)
        replay_text_events(editor, plan.typing_text)
        after_frame = desktop.capture()
        publish_frame(after_frame, output / "after")
        after = observer.observe(after_frame)
        verification = ScreenFeedbackVerifier().verify(expected_text=target_text, before=before,
                    after=after, frame=after_frame, last_event_at_s=editor.last_event_at_s)
        report = {"schema_version": 1, "backend": desktop.backend, "before": asdict(before),
                  "plan": asdict(plan), "after": asdict(after), "verification": asdict(verification),
                  "input_sources": sorted(editor.event_sources), "physical_acceptance": False,
                  "scope": "Actual rendered RGB pixels and local OCR; CPU synthetic key input, no robot/Isaac."}
        output.mkdir(parents=True, exist_ok=True)
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        return report
    finally:
        desktop.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Local pixel/OCR editor feedback demo (no simulator)")
    parser.add_argument("--output", type=Path, default=Path("reports/feedback"))
    parser.add_argument("--initial", default="helo world")
    parser.add_argument("--target", default="hello world")
    parser.add_argument("--backend", choices=("auto", "pygame", "pillow"), default="auto")
    parser.add_argument("--visible", action="store_true")
    args = parser.parse_args()
    try:
        report = run_demo(output=args.output, initial_text=args.initial, target_text=args.target,
                          backend=args.backend, visible=args.visible)
    except (ValueError, RuntimeError, ImportError, OSError, subprocess.TimeoutExpired) as exc:
        parser.error(str(exc))
    print(json.dumps({"report": str(args.output / "report.json"), "backend": report["backend"],
                      "verification": report["verification"], "scope": report["scope"]}, indent=2))
    return 0 if report["verification"]["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
