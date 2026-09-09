"""Pure-Python P1 composition proving the Isaac-to-desktop event boundary."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
import tempfile
import time

from .assets import G1SimulationManifest
from .desktop import PtyDesktopWorker, ScreenFrame
from .keyboard_bridge import (
    KeyEvent,
    KeyEventType,
    KeycapConfig,
    KeycapSample,
    KeyboardEventBridge,
)


@dataclass(frozen=True, slots=True)
class HybridSmokeResult:
    succeeded: bool
    terminal_visible: bool
    terminal_focused: bool
    output_observed: bool
    keys_down_after_stop: tuple[str, ...]
    event_counts: dict[str, int]
    asset_capability: dict[str, object]
    screen_text: str

    def to_dict(self) -> dict[str, object]:
        return {
            "succeeded": self.succeeded,
            "terminal_visible": self.terminal_visible,
            "terminal_focused": self.terminal_focused,
            "output_observed": self.output_observed,
            "keys_down_after_stop": list(self.keys_down_after_stop),
            "event_counts": dict(self.event_counts),
            "asset_capability": dict(self.asset_capability),
            "screen_text": self.screen_text,
        }


class HybridSimulationHarness:
    """Replayable keycap probe + event bridge + host PTY desktop."""

    def __init__(
        self,
        *,
        project_root: str | Path,
        workspace: str | Path,
        manifest_path: str | Path,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.manifest = G1SimulationManifest.from_json(manifest_path)
        self.manifest.validate(root=self.project_root)
        keyboard = self.manifest.keyboard
        self.bridge = KeyboardEventBridge(
            KeycapConfig(
                press_threshold_m=float(keyboard["press_threshold_m"]),
                release_threshold_m=float(keyboard["release_threshold_m"]),
                debounce_seconds=float(keyboard["debounce_seconds"]),
                maximum_hold_seconds=float(keyboard["maximum_hold_seconds"]),
            )
        )
        self.desktop = PtyDesktopWorker(workspace)
        self._time = time.monotonic()
        self.events: list[KeyEvent] = []

    def start(self) -> None:
        self.desktop.start()

    def press_chord(self, keys: tuple[str, ...]) -> ScreenFrame:
        if len(keys) < 2:
            raise ValueError("a hybrid chord requires modifiers and one primary key")
        for key in keys[:-1]:
            self.key_down(key)
        self.tap(keys[-1])
        for key in reversed(keys[:-1]):
            self.key_up(key)
        return self.desktop.snapshot()

    def type_text(self, text: str) -> ScreenFrame:
        aliases = {" ": "space", "\n": "enter", "\b": "backspace"}
        for character in text:
            self.tap(aliases.get(character, character.lower()))
        return self.desktop.snapshot()

    def key_down(self, key: str) -> tuple[KeyEvent, ...]:
        return self._samples(
            key,
            (
                (0.0005, True),
                (self.bridge.config.press_threshold_m + 0.0002, True),
                (self.bridge.config.press_threshold_m + 0.0002, True),
            ),
        )

    def key_up(self, key: str) -> tuple[KeyEvent, ...]:
        return self._samples(
            key,
            (
                (self.bridge.config.release_threshold_m - 0.0002, True),
                (0.0, False),
            ),
        )

    def tap(self, key: str) -> tuple[KeyEvent, ...]:
        return self.key_down(key) + self.key_up(key)

    def observe_contact(
        self,
        key: str,
        *,
        adjacent_contacts: tuple[str, ...] = (),
    ) -> KeycapSample:
        """Publish a sub-threshold contact sample without generating KeyDown."""

        self._time += 0.01
        sample = KeycapSample(
            key=key,
            displacement_m=0.0005,
            in_contact=True,
            monotonic_time=self._time,
            sample_id=f"{key}-contact-{self._time:.9f}",
            adjacent_contacts=adjacent_contacts,
        )
        self._dispatch(self.bridge.process(sample, now=self._time))
        return sample

    def release_all(self, reason: str) -> tuple[KeyEvent, ...]:
        self._time += 0.01
        releases = self.bridge.all_keys_up(
            monotonic_time=self._time,
            reason=reason,
        )
        self._dispatch(releases)
        return releases

    def stop(self) -> tuple[KeyEvent, ...]:
        releases = self.release_all("hybrid_harness_stop")
        self.desktop.stop()
        return releases

    def _samples(
        self,
        key: str,
        points: tuple[tuple[float, bool], ...],
    ) -> tuple[KeyEvent, ...]:
        emitted: list[KeyEvent] = []
        for index, (displacement, contact) in enumerate(points):
            self._time += 0.01
            sample = KeycapSample(
                key=key,
                displacement_m=max(0.0, displacement),
                in_contact=contact,
                monotonic_time=self._time,
                sample_id=f"{key}-{self._time:.9f}-{index}",
            )
            emitted.extend(self.bridge.process(sample, now=self._time))
        self._dispatch(emitted)
        return tuple(emitted)

    def _dispatch(self, events: tuple[KeyEvent, ...] | list[KeyEvent]) -> None:
        for event in events:
            self.events.append(event)
            self.desktop.handle_event(event)


def run_hybrid_smoke(
    *,
    root: str | Path,
    workspace: str | Path | None = None,
    manifest_path: str | Path | None = None,
) -> HybridSmokeResult:
    project = Path(root).resolve()
    manifest = manifest_path or project / "configs/simulation/g1_29dof_5010_p1.json"
    temporary: tempfile.TemporaryDirectory[str] | None = None
    if workspace is None:
        temporary = tempfile.TemporaryDirectory(prefix="vskill-p1-")
        workspace = temporary.name
    harness = HybridSimulationHarness(
        project_root=project,
        workspace=workspace,
        manifest_path=manifest,
    )
    final_frame = ScreenFrame(0, time.monotonic(), False, False, "")
    try:
        harness.start()
        harness.press_chord(("left_ctrl", "left_alt", "t"))
        # Give the shell a moment to initialize before replaying the command.
        startup_deadline = time.monotonic() + 0.1
        while time.monotonic() < startup_deadline:
            harness.desktop.snapshot()
            time.sleep(0.005)
        harness.type_text("printf p1-ready\n")
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            final_frame = harness.desktop.snapshot()
            if final_frame.text.count("p1-ready") >= 2:
                break
            time.sleep(0.01)
        was_visible = final_frame.visible
        was_focused = final_frame.focused
        # One occurrence is terminal echo; the second proves PTY execution.
        output_observed = final_frame.text.count("p1-ready") >= 2
    finally:
        harness.stop()
        keys_down = harness.bridge.keys_down
        if temporary is not None:
            temporary.cleanup()
    counts = Counter(event.event_type.value for event in harness.events)
    balanced = counts[KeyEventType.KEYDOWN.value] == counts[KeyEventType.KEYUP.value]
    return HybridSmokeResult(
        succeeded=was_visible and was_focused and output_observed and balanced and not keys_down,
        terminal_visible=was_visible,
        terminal_focused=was_focused,
        output_observed=output_observed,
        keys_down_after_stop=keys_down,
        event_counts=dict(counts),
        asset_capability=harness.manifest.capability(),
        screen_text=final_frame.text,
    )
