"""Compile keyboard plans into reusable ActionGraph modules."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from ..typing import (
    ChordPlan,
    HandSide,
    KeyAssignment,
    KeyRole,
    Keystroke,
    TypingPlan,
    TypingPlanner,
    TypingRepair,
    normalize_key,
)
from .model import ActionGraph, ActionGraphAssembler, ActionGraphModule


class ActionModuleCatalog:
    """Pinned collection of versioned ActionGraph building blocks."""

    REQUIRED_MODULES = (
        "bimanual_prepose",
        "key_prepose",
        "key_down",
        "key_up",
        "modifier_release_group",
        "screen_gate",
    )

    def __init__(self, modules: Iterable[ActionGraphModule]) -> None:
        module_list = tuple(modules)
        self._modules = {module.name: module for module in module_list}
        if len(self._modules) != len(module_list):
            raise ValueError("action module catalog contains duplicate names")
        missing = set(self.REQUIRED_MODULES) - self._modules.keys()
        if missing:
            raise ValueError(f"action module catalog is incomplete: {sorted(missing)}")

    @classmethod
    def from_directory(cls, path: str | Path) -> "ActionModuleCatalog":
        root = Path(path)
        return cls(
            ActionGraphModule.from_json(module_path)
            for module_path in sorted(root.glob("*.json"))
        )

    def module(self, name: str) -> ActionGraphModule:
        try:
            return self._modules[name]
        except KeyError as exc:
            raise KeyError(f"unknown action graph module: {name}") from exc

    @property
    def versions(self) -> dict[str, str]:
        return {name: module.version for name, module in sorted(self._modules.items())}


class KeyboardActionCompiler:
    """Lower key objectives to Cartesian/contact/event actions, never joints."""

    def __init__(self, catalog: ActionModuleCatalog) -> None:
        self.catalog = catalog

    def compile_single_key(self, stroke: Keystroke) -> ActionGraph:
        if stroke.modifiers:
            return self.compile_text(TypingPlan(stroke.expected_text, (stroke,)))
        assignment = KeyAssignment(
            logical_key=stroke.key,
            physical_key=normalize_key(stroke.key),
            role=KeyRole.PRIMARY,
            hand=stroke.hand,
            finger=stroke.finger,
        )
        assembler = ActionGraphAssembler()
        last = self._add_tap(assembler, assignment, after=(), sequence=0)
        return assembler.build(
            name=f"single_key_{assignment.physical_key}",
            version="1.0.0",
            metadata={
                "plan_type": "single_key",
                "expected_text": stroke.expected_text,
                "terminal_node": last,
            },
        )

    def compile_chord(
        self,
        plan: ChordPlan,
        *,
        verify_terminal: bool = True,
    ) -> ActionGraph:
        assembler = ActionGraphAssembler()
        after: tuple[str, ...] = ()
        left = next(
            (item for item in plan.assignments if item.hand is HandSide.LEFT), None
        )
        right = next(
            (item for item in plan.assignments if item.hand is HandSide.RIGHT), None
        )
        if left is not None and right is not None:
            prepose = assembler.add(
                self.catalog.module("bimanual_prepose"),
                namespace="prepose",
                bindings={
                    "left_key": left.physical_key,
                    "left_finger": left.finger.value,
                    "right_key": right.physical_key,
                    "right_finger": right.finger.value,
                },
            )
            after = (prepose.outputs["ready"],)

            # Prepose every remaining finger before the first modifier is
            # pressed.  This prevents a large same-hand transfer from
            # disturbing a modifier that must already be held.
            first_by_hand = {left.executor, right.executor}
            for sequence, assignment in enumerate(plan.assignments):
                if assignment.executor in first_by_hand:
                    continue
                extra = assembler.add(
                    self.catalog.module("key_prepose"),
                    namespace=(
                        f"prepose_{sequence:03d}_{_safe_name(assignment.physical_key)}"
                    ),
                    bindings=_bindings(assignment),
                    after=after,
                )
                after = (extra.outputs["ready"],)

        by_key = {item.physical_key: item for item in plan.assignments}
        held_outputs: dict[str, str] = {}
        for sequence, key in enumerate(plan.press_order):
            assignment = by_key[key]
            instance = self._add_down(
                assembler,
                assignment,
                after=after,
                sequence=sequence,
            )
            held_outputs[key] = instance.outputs["held"]
            after = (instance.outputs["held"],)

        primary_key = plan.release_order[0]
        primary_release = self._add_up(
            assembler,
            by_key[primary_key],
            after=after,
            sequence=0,
        )
        after = (primary_release.outputs["released"],)
        modifier_release_order = plan.release_order[1:]
        modifier_assignments = tuple(by_key[key] for key in modifier_release_order)
        if (
            len(modifier_assignments) == 2
            and modifier_assignments[0].hand is modifier_assignments[1].hand
        ):
            first, second = modifier_assignments
            group = assembler.add(
                self.catalog.module("modifier_release_group"),
                namespace="up_modifiers",
                bindings={
                    "key_a": first.physical_key,
                    "finger_a": first.finger.value,
                    "key_b": second.physical_key,
                    "finger_b": second.finger.value,
                    "hand": first.hand.value,
                },
                after=after,
            )
            after = (group.outputs["released"],)
        else:
            for release_index, assignment in enumerate(
                modifier_assignments, start=1
            ):
                instance = self._add_up(
                    assembler,
                    assignment,
                    after=after,
                    sequence=release_index,
                )
                after = (instance.outputs["released"],)

        if verify_terminal:
            screen = assembler.add(
                self.catalog.module("screen_gate"),
                namespace="screen",
                bindings={
                    "terminal_required": True,
                    "expected_text": "",
                    "match_mode": "contains",
                },
                after=after,
            )
            after = (screen.outputs["verified"],)

        return assembler.build(
            name=(
                "ctrl_alt_t"
                if len(plan.logical_keys) == 3
                and set(plan.logical_keys) == {"ctrl", "alt", "t"}
                else "keyboard_chord"
            ),
            version="1.0.0",
            metadata={
                "plan_type": "chord",
                "plan_version": plan.version,
                "logical_keys": list(plan.logical_keys),
                "assignments": [
                    {
                        "logical_key": item.logical_key,
                        "physical_key": item.physical_key,
                        "executor": item.executor,
                        "role": item.role.value,
                    }
                    for item in plan.assignments
                ],
                "expected_events": [
                    {
                        "event_type": item.event_type.value,
                        "key": item.key,
                        "modifiers": list(item.modifiers),
                    }
                    for item in plan.expected_events
                ],
                "maximum_duration_seconds": plan.maximum_duration_seconds,
                "terminal_node": after[0],
            },
        )

    def compile_text(
        self,
        plan: TypingPlan,
        *,
        expected_screen_text: str | None = None,
        match_mode: str = "suffix",
    ) -> ActionGraph:
        return self._compile_strokes(
            graph_name="type_text",
            strokes=plan.strokes,
            expected_text=(
                plan.text if expected_screen_text is None else expected_screen_text
            ),
            match_mode=match_mode,
            plan_type="text",
        )

    def compile_repair(self, repair: TypingRepair) -> ActionGraph:
        planner = TypingPlanner()
        backspace_hand, backspace_finger = planner._hand_and_finger("backspace")
        backspace = Keystroke(
            key="backspace",
            modifiers=(),
            hand=backspace_hand,
            finger=backspace_finger,
            expected_text="",
        )
        strokes = (backspace,) * repair.backspaces + repair.replacement.strokes
        return self._compile_strokes(
            graph_name="repair_text",
            strokes=strokes,
            expected_text=repair.expected,
            match_mode="suffix",
            plan_type="repair",
            metadata={
                "observed": repair.observed,
                "common_prefix_length": repair.common_prefix_length,
                "backspaces": repair.backspaces,
            },
        )

    def _compile_strokes(
        self,
        *,
        graph_name: str,
        strokes: tuple[Keystroke, ...],
        expected_text: str,
        match_mode: str,
        plan_type: str,
        metadata: dict[str, object] | None = None,
    ) -> ActionGraph:
        if not strokes:
            raise ValueError("typing ActionGraph requires at least one stroke")
        assembler = ActionGraphAssembler()
        after: tuple[str, ...] = ()
        sequence = 0
        for stroke in strokes:
            if stroke.modifiers:
                plan = TypingPlanner().plan_chord_detailed(
                    [*stroke.modifiers, stroke.key]
                )
                by_key = {item.physical_key: item for item in plan.assignments}
                for key in plan.press_order:
                    instance = self._add_down(
                        assembler,
                        by_key[key],
                        after=after,
                        sequence=sequence,
                    )
                    sequence += 1
                    after = (instance.outputs["held"],)
                for key in plan.release_order:
                    instance = self._add_up(
                        assembler,
                        by_key[key],
                        after=after,
                        sequence=sequence,
                    )
                    sequence += 1
                    after = (instance.outputs["released"],)
                continue
            assignment = KeyAssignment(
                logical_key=stroke.key,
                physical_key=normalize_key(stroke.key),
                role=KeyRole.PRIMARY,
                hand=stroke.hand,
                finger=stroke.finger,
            )
            terminal = self._add_tap(
                assembler, assignment, after=after, sequence=sequence
            )
            sequence += 1
            after = (terminal,)

        screen = assembler.add(
            self.catalog.module("screen_gate"),
            namespace="screen",
            bindings={
                "terminal_required": True,
                "expected_text": expected_text,
                "match_mode": match_mode,
            },
            after=after,
        )
        graph_metadata: dict[str, object] = {
            "plan_type": plan_type,
            "expected_text": expected_text,
            "terminal_node": screen.outputs["verified"],
        }
        graph_metadata.update(metadata or {})
        return assembler.build(
            name=graph_name,
            version="1.0.0",
            metadata=graph_metadata,
        )

    def _add_tap(
        self,
        assembler: ActionGraphAssembler,
        assignment: KeyAssignment,
        *,
        after: tuple[str, ...],
        sequence: int,
    ) -> str:
        down = self._add_down(
            assembler, assignment, after=after, sequence=sequence
        )
        up = self._add_up(
            assembler,
            assignment,
            after=(down.outputs["held"],),
            sequence=sequence,
        )
        return up.outputs["released"]

    def _add_down(
        self,
        assembler: ActionGraphAssembler,
        assignment: KeyAssignment,
        *,
        after: tuple[str, ...],
        sequence: int,
    ):
        return assembler.add(
            self.catalog.module("key_down"),
            namespace=f"down_{sequence:03d}_{_safe_name(assignment.physical_key)}",
            bindings=_bindings(assignment),
            after=after,
        )

    def _add_up(
        self,
        assembler: ActionGraphAssembler,
        assignment: KeyAssignment,
        *,
        after: tuple[str, ...],
        sequence: int,
    ):
        return assembler.add(
            self.catalog.module("key_up"),
            namespace=f"up_{sequence:03d}_{_safe_name(assignment.physical_key)}",
            bindings=_bindings(assignment),
            after=after,
        )


def _bindings(assignment: KeyAssignment) -> dict[str, str]:
    return {
        "key": assignment.physical_key,
        "hand": assignment.hand.value,
        "finger": assignment.finger.value,
        "role": assignment.role.value,
    }


def _safe_name(key: str) -> str:
    return "".join(character if character.isalnum() else "_" for character in key)
