"""Host desktop worker with PTY terminal and optional Pygame rendering."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import os
from pathlib import Path
import pty
import select
import signal
import subprocess
import time
from typing import Protocol

from ..ipc import MessageHeader, MessageKind, WorkerMessage
from .keyboard_bridge import KeyEvent, KeyEventType


class DesktopStatus(StrEnum):
    STOPPED = "stopped"
    READY = "ready"
    TERMINAL_FOCUSED = "terminal_focused"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ScreenFrame:
    frame_id: int
    monotonic_time: float
    focused: bool
    visible: bool
    text: str
    source: str = "desktop_text_snapshot"

    def to_message(self, header: MessageHeader) -> WorkerMessage:
        return WorkerMessage(
            MessageKind.SCREEN_FRAME,
            header,
            {
                "frame_id": self.frame_id,
                "frame_time": self.monotonic_time,
                "focused": self.focused,
                "visible": self.visible,
                "text": self.text,
                "source": self.source,
            },
        )


class ScreenRenderer(Protocol):
    def render(self, frame: ScreenFrame) -> None:
        """Render a worker-owned screen frame."""

    def close(self) -> None:
        """Release renderer resources."""


class PygameScreenRenderer:
    """Optional Pygame display; imported only inside the desktop worker."""

    def __init__(self, *, width: int = 960, height: int = 540) -> None:
        try:
            import pygame
        except ImportError as exc:  # optional adapter dependency
            raise RuntimeError("pygame is not installed in the desktop-worker environment") from exc
        self._pygame = pygame
        pygame.init()
        self._surface = pygame.display.set_mode((width, height))
        pygame.display.set_caption("V-Skill OS simulated desktop")
        self._font = pygame.font.SysFont("monospace", 20)

    def render(self, frame: ScreenFrame) -> None:
        pygame = self._pygame
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                continue
        self._surface.fill((18, 20, 24))
        for row, line in enumerate(frame.text.splitlines()[-24:]):
            glyphs = self._font.render(line[:100], True, (220, 224, 230))
            self._surface.blit(glyphs, (16, 14 + row * 21))
        pygame.display.flip()

    def close(self) -> None:
        self._pygame.quit()


class PtyDesktopWorker:
    """Consumes bridge events and drives a real shell through a restricted PTY."""

    MODIFIERS = frozenset({"left_ctrl", "right_ctrl", "left_alt", "right_alt", "left_shift", "right_shift"})

    def __init__(
        self,
        workspace: str | Path,
        *,
        shell: str = "/bin/sh",
        renderer: ScreenRenderer | None = None,
    ) -> None:
        root = Path(workspace).resolve()
        root.mkdir(parents=True, exist_ok=True)
        if not root.is_dir():
            raise ValueError("desktop workspace must be a directory")
        self.workspace = root
        self.shell = shell
        self.renderer = renderer
        self.status = DesktopStatus.STOPPED
        self.focused = False
        self.visible = False
        self.modifiers: set[str] = set()
        self._process: subprocess.Popen[bytes] | None = None
        self._master_fd: int | None = None
        self._buffer = bytearray()
        self._frame_id = 0

    @property
    def process_id(self) -> int | None:
        return None if self._process is None else self._process.pid

    def start(self) -> None:
        if self.status is not DesktopStatus.STOPPED:
            return
        self.status = DesktopStatus.READY

    def handle_event(self, event: KeyEvent) -> ScreenFrame:
        if self.status is DesktopStatus.STOPPED:
            raise RuntimeError("desktop worker is not started")
        self._refresh_process_state()
        key = event.key
        if event.event_type is KeyEventType.KEYUP:
            self.modifiers.discard(key)
            return self.snapshot()
        if key in self.MODIFIERS:
            self.modifiers.add(key)
            return self.snapshot()
        if key == "t" and self._ctrl_down() and self._alt_down():
            self.open_terminal()
            return self.snapshot()
        if self.focused and self._master_fd is not None:
            encoded = self._encode_key(key)
            if encoded:
                os.write(self._master_fd, encoded)
        return self.snapshot()

    def open_terminal(self) -> None:
        self._refresh_process_state()
        if self._process is not None and self._process.poll() is None:
            self.visible = True
            self.focused = True
            self.status = DesktopStatus.TERMINAL_FOCUSED
            return
        master_fd, slave_fd = pty.openpty()
        os.set_blocking(master_fd, False)
        environment = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(self.workspace),
            "PWD": str(self.workspace),
            "TERM": "dumb",
            "PS1": "",
            "LANG": "C.UTF-8",
        }
        try:
            self._process = subprocess.Popen(
                [self.shell],
                cwd=self.workspace,
                env=environment,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                start_new_session=True,
                close_fds=True,
            )
        finally:
            os.close(slave_fd)
        self._master_fd = master_fd
        self.visible = True
        self.focused = True
        self.status = DesktopStatus.TERMINAL_FOCUSED

    def snapshot(self) -> ScreenFrame:
        self._refresh_process_state()
        self._read_available()
        self._frame_id += 1
        frame = ScreenFrame(
            frame_id=self._frame_id,
            monotonic_time=time.monotonic(),
            focused=self.focused,
            visible=self.visible,
            text=self._buffer.decode("utf-8", errors="replace"),
        )
        if self.renderer is not None:
            self.renderer.render(frame)
        return frame

    def recover(self) -> None:
        self._release_process()
        self.modifiers.clear()
        self.focused = False
        self.visible = False
        self.status = DesktopStatus.READY

    def stop(self) -> None:
        self._release_process()
        self.modifiers.clear()
        self.focused = False
        self.visible = False
        self.status = DesktopStatus.STOPPED
        if self.renderer is not None:
            self.renderer.close()

    def _read_available(self) -> None:
        if self._master_fd is None:
            return
        while True:
            readable, _, _ = select.select([self._master_fd], [], [], 0.0)
            if not readable:
                return
            try:
                chunk = os.read(self._master_fd, 65536)
            except (BlockingIOError, OSError):
                return
            if not chunk:
                return
            self._buffer.extend(chunk)

    def _refresh_process_state(self) -> None:
        if self._process is None or self._process.poll() is None:
            return
        self._close_master()
        self._process = None
        self.modifiers.clear()
        self.focused = False
        self.status = DesktopStatus.FAILED

    def _release_process(self) -> None:
        process = self._process
        self._process = None
        if process is not None and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=1.0)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=1.0)
        self._close_master()

    def _close_master(self) -> None:
        if self._master_fd is not None:
            try:
                os.close(self._master_fd)
            except OSError:
                pass
            self._master_fd = None

    def _ctrl_down(self) -> bool:
        return bool(self.modifiers & {"left_ctrl", "right_ctrl"})

    def _alt_down(self) -> bool:
        return bool(self.modifiers & {"left_alt", "right_alt"})

    def _encode_key(self, key: str) -> bytes:
        if key == "enter":
            return b"\n"
        if key == "backspace":
            return b"\x7f"
        if key == "space":
            return b" "
        if len(key) != 1:
            return b""
        character = key
        if self.modifiers & {"left_shift", "right_shift"}:
            character = character.upper()
        return character.encode("utf-8")

    def __enter__(self) -> "PtyDesktopWorker":
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.stop()


def render_terminal_text(raw_text: str) -> str:
    """Apply basic CR/backspace terminal editing for screen-text verification."""

    rendered: list[str] = []
    cursor = 0
    line_start = 0
    for character in raw_text:
        if character == "\r":
            cursor = line_start
            continue
        if character in {"\b", "\x7f"}:
            cursor = max(line_start, cursor - 1)
            continue
        if character == "\n":
            if cursor < len(rendered):
                cursor = len(rendered)
            rendered.append(character)
            cursor = len(rendered)
            line_start = cursor
            continue
        if cursor < len(rendered):
            rendered[cursor] = character
        else:
            rendered.append(character)
        cursor += 1
    return "".join(rendered)
