"""Machine-readable catalog of evaluated upstream robotics/model projects."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .config import load_json


@dataclass(frozen=True, slots=True)
class ReferenceProject:
    name: str
    url: str
    subsystem: str
    maturity: str
    integration: str
    use_for: tuple[str, ...]
    constraints: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ReferenceProject":
        return cls(
            name=str(data["name"]),
            url=str(data["url"]),
            subsystem=str(data["subsystem"]),
            maturity=str(data["maturity"]),
            integration=str(data["integration"]),
            use_for=tuple(str(item) for item in data.get("use_for", ())),
            constraints=tuple(str(item) for item in data.get("constraints", ())),
        )


class ReferenceCatalog:
    def __init__(self, projects: tuple[ReferenceProject, ...]) -> None:
        names = [project.name for project in projects]
        if len(names) != len(set(names)):
            raise ValueError("reference catalog contains duplicate project names")
        self.projects = projects

    @classmethod
    def from_json(cls, path: str | Path) -> "ReferenceCatalog":
        data = load_json(path)
        raw_projects = data.get("projects")
        if not isinstance(raw_projects, list):
            raise ValueError("reference catalog projects must be a list")
        return cls(tuple(ReferenceProject.from_dict(item) for item in raw_projects))

    def for_subsystem(self, subsystem: str) -> tuple[ReferenceProject, ...]:
        return tuple(
            project for project in self.projects if project.subsystem == subsystem
        )

    def integration_targets(self) -> tuple[ReferenceProject, ...]:
        return tuple(
            project
            for project in self.projects
            if project.integration in {"adopt", "adapter", "baseline"}
        )

