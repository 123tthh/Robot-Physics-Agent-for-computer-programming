"""In-process versioned Skill registry."""

from __future__ import annotations

from collections.abc import Iterator

from .contracts import Skill


class SkillRegistry:
    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}

    def register(self, skill: Skill) -> None:
        name = skill.contract.name
        if name in self._skills:
            current = self._skills[name].contract.version
            raise ValueError(f"skill {name!r} already registered at version {current}")
        self._skills[name] = skill

    def replace(self, skill: Skill) -> None:
        self._skills[skill.contract.name] = skill

    def get(self, name: str) -> Skill:
        try:
            return self._skills[name]
        except KeyError as exc:
            raise KeyError(f"unknown skill: {name}") from exc

    def __contains__(self, name: str) -> bool:
        return name in self._skills

    def __iter__(self) -> Iterator[str]:
        return iter(sorted(self._skills))

    def contracts(self) -> list[dict[str, object]]:
        return [
            skill.contract.to_dict()
            for skill in sorted(self._skills.values(), key=lambda item: item.contract.name)
        ]
