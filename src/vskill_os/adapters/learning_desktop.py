"""Readable single-line PTY fixture for fixed-workstation camera calibration.

The line is decoded by the existing ANSI terminal from actual PTY output. The
renderer has no target text, key-event counter or task-success input. It only
uses larger glyphs for the task's input row; other terminal layouts are rejected
by the independent v1 evaluator.
"""
from .virtual_computer import PygameTextureRenderer


class LearningTerminalRenderer(PygameTextureRenderer):
    def __init__(self):
        self.input_font = None
        super().__init__()
        self.input_font = self.pygame.font.SysFont('DejaVu Sans Mono', 52)

    def render(self, frame):
        super().render(frame)
        if self.input_font is not None:
            self.pygame.draw.rect(self.surface, (16, 24, 35, 255), (0, 96, 960, 96))
            self.surface.blit(self.input_font.render(self.terminal.display[1], True, (225, 241, 233)), (24, 96))
