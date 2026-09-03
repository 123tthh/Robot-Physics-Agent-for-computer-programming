"""Validated directed acyclic graph of verifiable skill transitions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from .orchestrator import WorkflowStep


@dataclass(frozen=True, slots=True)
class SkillNode:
    node_id: str
    skill: str
    arguments: Mapping[str, Any] = field(default_factory=dict)
    required_postconditions: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()
    timeout_seconds: float = 30.0

    def to_step(self) -> WorkflowStep:
        return WorkflowStep(
            skill=self.skill,
            arguments=self.arguments,
            required_postconditions=self.required_postconditions,
            timeout_seconds=self.timeout_seconds,
        )


class SkillGraph:
    def __init__(self, name: str, nodes: Iterable[SkillNode]) -> None:
        self.name = name
        node_list = list(nodes)
        self._nodes = {node.node_id: node for node in node_list}
        if not self.name.strip():
            raise ValueError("graph name cannot be empty")
        if not self._nodes:
            raise ValueError("graph must contain at least one node")
        if len(self._nodes) != len(node_list):
            raise ValueError("graph contains duplicate node ids")
        self._validate()

    def _validate(self) -> None:
        for node in self._nodes.values():
            unknown = set(node.depends_on) - self._nodes.keys()
            if unknown:
                raise ValueError(f"node {node.node_id!r} has unknown dependencies: {sorted(unknown)}")
            if node.node_id in node.depends_on:
                raise ValueError(f"node {node.node_id!r} depends on itself")
        self.topological_order()

    def topological_order(self) -> tuple[str, ...]:
        remaining = {key: set(node.depends_on) for key, node in self._nodes.items()}
        ordered: list[str] = []
        while remaining:
            ready = sorted(key for key, dependencies in remaining.items() if not dependencies)
            if not ready:
                raise ValueError("skill graph contains a cycle")
            for key in ready:
                ordered.append(key)
                remaining.pop(key)
            for dependencies in remaining.values():
                dependencies.difference_update(ready)
        return tuple(ordered)

    def ready_nodes(self, completed: set[str]) -> tuple[SkillNode, ...]:
        return tuple(
            self._nodes[node_id]
            for node_id in self.topological_order()
            if node_id not in completed
            and set(self._nodes[node_id].depends_on).issubset(completed)
        )

    def node(self, node_id: str) -> SkillNode:
        try:
            return self._nodes[node_id]
        except KeyError as exc:
            raise KeyError(f"unknown graph node: {node_id}") from exc

    @property
    def node_ids(self) -> set[str]:
        return set(self._nodes)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SkillGraph":
        raw_nodes = data.get("nodes")
        if not isinstance(raw_nodes, list):
            raise ValueError("workflow config requires a nodes list")
        nodes = [
            SkillNode(
                node_id=str(item["id"]),
                skill=str(item["skill"]),
                arguments=dict(item.get("arguments", {})),
                required_postconditions=tuple(item.get("required_postconditions", ())),
                depends_on=tuple(item.get("depends_on", ())),
                timeout_seconds=float(item.get("timeout_seconds", 30.0)),
            )
            for item in raw_nodes
        ]
        return cls(str(data["name"]), nodes)
