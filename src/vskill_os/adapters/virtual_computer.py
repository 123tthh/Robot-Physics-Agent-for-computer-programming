"""Optional pixel desktop backed by a real PTY; no simulator dependency."""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import subprocess
import time

from ..simulation.desktop import PtyDesktopWorker, ScreenFrame
from ..simulation.keyboard_bridge import KeyEvent


class PygameTextureRenderer:
    """ANSI terminal rendered into an offscreen Pygame surface."""

    def __init__(self, width=960, height=560):
        import pygame
        import pyte

        self.pygame = pygame
        pygame.font.init()
        self.surface = pygame.Surface((width, height), flags=pygame.SRCALPHA)
        self.font = pygame.font.SysFont('DejaVu Sans Mono', 26)
        self.small_font = pygame.font.SysFont('DejaVu Sans Mono', 18)
        self.terminal = pyte.Screen(56, 14)
        self.stream = pyte.Stream(self.terminal)
        self.previous_text = ''
        self.render(ScreenFrame(0, time.monotonic(), True, True, ''))

    def render(self, frame: ScreenFrame):
        if not frame.text.startswith(self.previous_text):
            self.terminal.reset()
            self.previous_text = ''
        self.stream.feed(frame.text[len(self.previous_text):])
        self.previous_text = frame.text
        self.surface.fill((16, 24, 35, 255))
        self.pygame.draw.rect(self.surface, (34, 48, 65), (0, 0, self.surface.get_width(), 48))
        self.surface.blit(self.small_font.render('TERMINAL  /  PHYSICAL KEYBOARD', True, (177, 208, 224)), (22, 14))
        for row, line in enumerate(self.terminal.display):
            self.surface.blit(self.font.render(line, True, (225, 241, 233)), (24, 65 + row * 32))
        self.surface.blit(self.small_font.render('PTY shell  |  input: keycap contact', True, (145, 171, 190)), (24, 532))

    def rgba(self):
        import numpy as np
        return np.frombuffer(self.pygame.image.tobytes(self.surface, 'RGBA'), dtype=np.uint8).reshape(self.surface.get_height(), self.surface.get_width(), 4).copy()

    def save(self, path):
        self.pygame.image.save(self.surface, str(path))

    def close(self):
        # Other adapters may also use Pygame fonts in this process.
        pass


class VirtualComputer:
    """Forward observed events, expose pixels, preserve the evidence chain.

    The workspace is a working directory, not an operating-system sandbox.
    This adapter never types a desired string or claims camera verification.
    """

    def __init__(self, output: Path):
        self.output = Path(output)
        self.output.mkdir(parents=True, exist_ok=True)
        self.renderer = PygameTextureRenderer()
        self.desktop = PtyDesktopWorker(self.output / 'workspace', renderer=self.renderer)
        self.events = []
        self.frames = []
        self.last_hash = None
        self.desktop.start()
        self.desktop.open_terminal()

    def handle_event(self, event: KeyEvent):
        self.events.append(asdict(event))
        self.desktop.handle_event(event)

    def snapshot(self, simulation_time_s: float):
        frame = self.desktop.snapshot()
        pixels = self.renderer.rgba()
        digest = hashlib.sha256(pixels.tobytes()).hexdigest()
        changed = digest != self.last_hash
        if changed:
            pixel_path = self.output / f'screen-{frame.frame_id:06d}.png'
            self.renderer.save(pixel_path)
            self.frames.append({'frame_id': frame.frame_id, 'simulation_time_s': simulation_time_s,
                                'monotonic_time': frame.monotonic_time, 'rgba_sha256': digest,
                                'png_path': str(pixel_path), 'png_sha256': hashlib.sha256(pixel_path.read_bytes()).hexdigest(),
                                'text': frame.text, 'terminal_rows': list(self.renderer.terminal.display)})
            self.last_hash = digest
            self.renderer.save(self.output / 'screen.png')
        return pixels, changed

    def close(self):
        self.snapshot(self.frames[-1]['simulation_time_s'] if self.frames else 0.)
        self.desktop.stop()
        result = {'events': self.events, 'frames': self.frames,
                  'input_source': 'forwarded_key_events', 'camera_ocr_verified': False,
                  'code_agent_executed': False, 'workspace_is_os_sandbox': False}
        (self.output / 'desktop.json').write_text(json.dumps(result, indent=2) + '\n')
        return result


class IndependentTestExecutor:
    """Run a caller-specified test process and record its actual exit status."""

    def run(self, argv: list[str], workspace: Path, *, timeout_s=30.):
        if not isinstance(argv, list) or not argv or not all(isinstance(x, str) and x for x in argv):
            raise ValueError('test argv must be nonempty strings')
        started = time.monotonic()
        try:
            completed = subprocess.run(argv, cwd=workspace, capture_output=True, text=True, timeout=timeout_s)
            return {'argv': argv, 'exit_code': completed.returncode, 'stdout': completed.stdout,
                    'stderr': completed.stderr, 'passed': completed.returncode == 0,
                    'duration_s': time.monotonic() - started, 'source': 'independent_process_exit'}
        except subprocess.TimeoutExpired:
            return {'argv': argv, 'exit_code': None, 'passed': False, 'failure': 'TEST_TIMEOUT',
                    'duration_s': time.monotonic() - started, 'source': 'independent_process_exit'}
