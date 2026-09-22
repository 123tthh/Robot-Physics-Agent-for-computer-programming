"""Deterministic, held-out curricula for two index fingertips."""

from __future__ import annotations

import random

from vskill_os.standing_typing import StandingTypingPlanner

from .contracts import Stroke, TypingGoal

STAGES = ("single_key", "alternating_bimanual", "short_text", "shift_chords")
TEXT_BANKS = {
    "single_key": ("f", "j", "a", "k", "d", "l"),
    "alternating_bimanual": ("flfl", "akak", "djdk", "fjak"),
    "short_text": ("hello", "robot", "skill", "type", "data"),
    "shift_chords": ("Hi!", "Aa", "Go!", "OK", "A1!"),
}
SPLIT_OFFSETS = {"train": 0, "validation": 1_000_000_000, "evaluation": 2_000_000_000}


def episode_seed(seed: int, split: str, index: int) -> int:
    if split not in SPLIT_OFFSETS or not 0 <= index < 1_000_000:
        raise ValueError("unknown split or episode index outside split budget")
    # Different namespaces never overlap, even after many checkpoint resumes.
    return SPLIT_OFFSETS[split] * 1_000_000 + seed * 1_000_000 + index


def goal_from_text(text: str, stage: str = "custom") -> TypingGoal:
    if not text:
        raise ValueError("typing goal cannot be empty")
    if any(character in text for character in "\n\r\b\t"):
        raise ValueError("training text supports printable characters only")
    plan = StandingTypingPlanner().plan_text(text)
    strokes = [Stroke(
        key=step.primary.key, character=step.character, primary_hand=step.primary.hand,
        shift_key=step.modifier.key if step.modifier else None,
        shift_hand=step.modifier.hand if step.modifier else None, wave=step.wave,
    ) for step in plan.steps]
    return TypingGoal(text, tuple(strokes), stage)


def sample_goal(stage: str, seed: int) -> TypingGoal:
    if stage not in TEXT_BANKS:
        raise ValueError(f"unknown curriculum stage: {stage}")
    return goal_from_text(random.Random(seed).choice(TEXT_BANKS[stage]), stage)
