"""Versioned, composable finite-horizon action graphs.

Action graphs sit below :class:`vskill_os.graph.SkillGraph`: a Skill selects a
bounded behavior, while an ActionGraph describes executor-neutral Cartesian,
contact, and event objectives for that behavior.  Joint commands are rejected
at the schema boundary so the Agent and graph compiler cannot bypass the
low-level safety controller.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
import json
import math
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

from ..domain import EvidenceDomain


_VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_PLACEHOLDER_PATTERN = re.compile(r"\$\{([a-zA-Z][a-zA-Z0-9_]*)\}")
_FORBIDDEN_CONTROL_FIELDS = frozenset(
    {
        "joint",
        "joints",
        "joint_command",
        "joint_commands",
        "joint_position",
        "joint_positions",
        "joint_target",
        "joint_targets",
        "joint_velocity",
        "joint_velocities",
        "joint_torque",
        "joint_torques",
        "actuator_command",
        "actuator_commands",
        "torque_command",
        "torque_commands",
    }
)


class ActionKind(StrEnum):
    """Portable actions understood by simulation and hardware adapters."""

    CARTESIAN_MOVE = "cartesian_move"
    CONTACT_GATE = "contact_gate"
    KEY_ACTUATE = "key_actuate"
    KEY_HOLD = "key_hold"
    KEY_RELEASE = "key_release"
    KEY_RELEASE_GROUP = "key_release_group"
    CLEARANCE_GATE = "clearance_gate"
    BARRIER = "barrier"
    SCREEN_GATE = "screen_gate"


@dataclass(frozen=True, slots=True)
class EvidenceRequirement:
    """Evidence an independent verifier must observe for one action."""

    domain: EvidenceDomain
    kind: str
    minimum_confidence: float = 1.0
    minimum_count: int = 1
    allowed_sources: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.kind.strip():
            raise ValueError("evidence kind cannot be empty")
        if not 0.0 <= self.minimum_confidence <= 1.0:
            raise ValueError("minimum evidence confidence must be within [0, 1]")
        if self.minimum_count <= 0:
            raise ValueError("minimum evidence count must be positive")

    def to_dict(self) -> dict[str, object]:
        return {
            "domain": self.domain.value,
            "kind": self.kind,
            "minimum_confidence": self.minimum_confidence,
            "minimum_count": self.minimum_count,
            "allowed_sources": list(self.allowed_sources),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EvidenceRequirement":
        return cls(
            domain=EvidenceDomain(str(data["domain"])),
            kind=str(data["kind"]),
            minimum_confidence=float(data.get("minimum_confidence", 1.0)),
            minimum_count=int(data.get("minimum_count", 1)),
            allowed_sources=tuple(str(item) for item in data.get("allowed_sources", ())),
        )


@dataclass(frozen=True, slots=True)
class ActionNode:
    """One bounded, executor-neutral action and its verification contract."""

    node_id: str
    kind: ActionKind
    executor: str
    parameters: Mapping[str, Any] = field(default_factory=dict)
    depends_on: tuple[str, ...] = ()
    resources: tuple[str, ...] = ()
    evidence: tuple[EvidenceRequirement, ...] = ()
    failure_codes: tuple[str, ...] = ()
    timeout_seconds: float = 1.0

    def __post_init__(self) -> None:
        if not self.node_id.strip() or not self.executor.strip():
            raise ValueError("action node id and executor are required")
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0.0:
            raise ValueError("action timeout_seconds must be finite and positive")
        if len(self.resources) != len(set(self.resources)):
            raise ValueError(f"action {self.node_id!r} contains duplicate resources")
        if len(self.failure_codes) != len(set(self.failure_codes)):
            raise ValueError(f"action {self.node_id!r} contains duplicate failure codes")
        _reject_low_level_commands(self.parameters, path=f"action {self.node_id}")

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.node_id,
            "kind": self.kind.value,
            "executor": self.executor,
            "parameters": dict(self.parameters),
            "depends_on": list(self.depends_on),
            "resources": list(self.resources),
            "evidence": [requirement.to_dict() for requirement in self.evidence],
            "failure_codes": list(self.failure_codes),
            "timeout_seconds": self.timeout_seconds,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ActionNode":
        return cls(
            node_id=str(data["id"]),
            kind=ActionKind(str(data["kind"])),
            executor=str(data["executor"]),
            parameters=dict(data.get("parameters", {})),
            depends_on=tuple(str(item) for item in data.get("depends_on", ())),
            resources=tuple(str(item) for item in data.get("resources", ())),
            evidence=tuple(
                EvidenceRequirement.from_dict(item)
                for item in data.get("evidence", ())
            ),
            failure_codes=tuple(str(item) for item in data.get("failure_codes", ())),
            timeout_seconds=float(data.get("timeout_seconds", 1.0)),
        )


@dataclass(frozen=True, slots=True)
class ModulePort:
    name: str
    value_type: str
    description: str = ""
    required: bool = True
    node: str | None = None

    def __post_init__(self) -> None:
        if not self.name.strip() or not self.value_type.strip():
            raise ValueError("module port name and type are required")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ModulePort":
        node = data.get("node")
        return cls(
            name=str(data["name"]),
            value_type=str(data["type"]),
            description=str(data.get("description", "")),
            required=bool(data.get("required", True)),
            node=None if node is None else str(node),
        )

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "name": self.name,
            "type": self.value_type,
            "description": self.description,
            "required": self.required,
        }
        if self.node is not None:
            payload["node"] = self.node
        return payload


@dataclass(frozen=True, slots=True)
class ModuleInstance:
    namespace: str
    module_name: str
    module_version: str
    nodes: tuple[ActionNode, ...]
    inputs: Mapping[str, Any]
    outputs: Mapping[str, str]


class ActionGraphModule:
    """Reusable action subgraph with declared inputs and output nodes."""

    def __init__(
        self,
        *,
        name: str,
        version: str,
        contract_version: str,
        inputs: Iterable[ModulePort],
        outputs: Iterable[ModulePort],
        nodes: Iterable[ActionNode],
        description: str = "",
    ) -> None:
        self.name = name
        self.version = version
        self.contract_version = contract_version
        self.description = description
        self.inputs = tuple(inputs)
        self.outputs = tuple(outputs)
        self.nodes = tuple(nodes)
        _validate_named_version(name, version, "action module")
        _validate_version(contract_version, "action module contract")
        if not self.nodes:
            raise ValueError("action module must contain at least one node")
        if len({port.name for port in self.inputs}) != len(self.inputs):
            raise ValueError("action module contains duplicate input ports")
        if len({port.name for port in self.outputs}) != len(self.outputs):
            raise ValueError("action module contains duplicate output ports")
        graph = ActionGraph(
            name=f"module:{name}",
            version=version,
            contract_version=contract_version,
            nodes=self.nodes,
        )
        for output in self.outputs:
            if output.node is None:
                raise ValueError(f"output port {output.name!r} must name a node")
            if output.node not in graph.node_ids:
                raise ValueError(
                    f"output port {output.name!r} references unknown node {output.node!r}"
                )
        declared = {port.name for port in self.inputs}
        referenced = _collect_placeholders(
            [node.to_dict() for node in self.nodes]
        )
        unknown = referenced - declared
        if unknown:
            raise ValueError(f"module contains undeclared inputs: {sorted(unknown)}")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ActionGraphModule":
        interface = dict(data.get("interface", {}))
        return cls(
            name=str(data["name"]),
            version=str(data["version"]),
            contract_version=str(data.get("contract_version", "1.0.0")),
            description=str(data.get("description", "")),
            inputs=(ModulePort.from_dict(item) for item in interface.get("inputs", ())),
            outputs=(ModulePort.from_dict(item) for item in interface.get("outputs", ())),
            nodes=(ActionNode.from_dict(item) for item in data.get("nodes", ())),
        )

    @classmethod
    def from_json(cls, path: str | Path) -> "ActionGraphModule":
        with Path(path).open("r", encoding="utf-8") as stream:
            data = json.load(stream)
        if not isinstance(data, dict):
            raise ValueError("action module JSON must contain an object")
        return cls.from_dict(data)

    def instantiate(
        self,
        namespace: str,
        bindings: Mapping[str, Any],
        *,
        after: Iterable[str] = (),
    ) -> ModuleInstance:
        if not namespace.strip() or "." in namespace:
            raise ValueError("module namespace must be non-empty and cannot contain '.'")
        declared = {port.name: port for port in self.inputs}
        missing = {
            name for name, port in declared.items() if port.required and name not in bindings
        }
        extra = set(bindings) - declared.keys()
        if missing or extra:
            raise ValueError(
                f"invalid bindings for {self.name}: missing={sorted(missing)}, extra={sorted(extra)}"
            )
        dependencies = tuple(after)
        local_ids = {node.node_id for node in self.nodes}
        instantiated: list[ActionNode] = []
        for node in self.nodes:
            local_dependencies = tuple(f"{namespace}.{item}" for item in node.depends_on)
            if not node.depends_on:
                local_dependencies = tuple(dict.fromkeys((*dependencies, *local_dependencies)))
            rendered = _substitute(node.to_dict(), bindings)
            rendered["id"] = f"{namespace}.{node.node_id}"
            rendered["depends_on"] = list(local_dependencies)
            instantiated.append(ActionNode.from_dict(rendered))
        outputs = {
            port.name: f"{namespace}.{port.node}"
            for port in self.outputs
            if port.node in local_ids
        }
        return ModuleInstance(
            namespace=namespace,
            module_name=self.name,
            module_version=self.version,
            nodes=tuple(instantiated),
            inputs=dict(bindings),
            outputs=outputs,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "version": self.version,
            "contract_version": self.contract_version,
            "description": self.description,
            "interface": {
                "inputs": [port.to_dict() for port in self.inputs],
                "outputs": [port.to_dict() for port in self.outputs],
            },
            "nodes": [node.to_dict() for node in self.nodes],
        }


class ActionGraph:
    """Validated DAG made from one or more namespaced action modules."""

    def __init__(
        self,
        *,
        name: str,
        version: str,
        contract_version: str,
        nodes: Iterable[ActionNode],
        modules: Iterable[Mapping[str, str]] = (),
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        self.name = name
        self.version = version
        self.contract_version = contract_version
        node_list = tuple(nodes)
        self._nodes = {node.node_id: node for node in node_list}
        self.modules = tuple(dict(item) for item in modules)
        self.metadata = dict(metadata or {})
        _validate_named_version(name, version, "action graph")
        _validate_version(contract_version, "action graph contract")
        if not node_list:
            raise ValueError("action graph must contain at least one node")
        if len(self._nodes) != len(node_list):
            raise ValueError("action graph contains duplicate node ids")
        _reject_low_level_commands(self.metadata, path="action graph metadata")
        self._validate()

    def _validate(self) -> None:
        for node in self._nodes.values():
            unknown = set(node.depends_on) - self._nodes.keys()
            if unknown:
                raise ValueError(
                    f"action {node.node_id!r} has unknown dependencies: {sorted(unknown)}"
                )
            if node.node_id in node.depends_on:
                raise ValueError(f"action {node.node_id!r} depends on itself")
        self.topological_order()

    def topological_order(self) -> tuple[str, ...]:
        remaining = {
            node_id: set(node.depends_on) for node_id, node in self._nodes.items()
        }
        ordered: list[str] = []
        while remaining:
            ready = sorted(
                node_id for node_id, dependencies in remaining.items() if not dependencies
            )
            if not ready:
                raise ValueError("action graph contains a cycle")
            ordered.extend(ready)
            for node_id in ready:
                remaining.pop(node_id)
            for dependencies in remaining.values():
                dependencies.difference_update(ready)
        return tuple(ordered)

    def parallel_layers(self) -> tuple[tuple[str, ...], ...]:
        remaining = {
            node_id: set(node.depends_on) for node_id, node in self._nodes.items()
        }
        layers: list[tuple[str, ...]] = []
        while remaining:
            ready = tuple(
                sorted(
                    node_id
                    for node_id, dependencies in remaining.items()
                    if not dependencies
                )
            )
            if not ready:
                raise ValueError("action graph contains a cycle")
            layers.append(ready)
            for node_id in ready:
                remaining.pop(node_id)
            for dependencies in remaining.values():
                dependencies.difference_update(ready)
        return tuple(layers)

    def node(self, node_id: str) -> ActionNode:
        try:
            return self._nodes[node_id]
        except KeyError as exc:
            raise KeyError(f"unknown action graph node: {node_id}") from exc

    @property
    def node_ids(self) -> set[str]:
        return set(self._nodes)

    @property
    def contains_low_level_commands(self) -> bool:
        # Construction rejects these fields.  The property makes the invariant
        # directly inspectable by acceptance tests and external tooling.
        return False

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "version": self.version,
            "contract_version": self.contract_version,
            "modules": list(self.modules),
            "metadata": dict(self.metadata),
            "nodes": [
                self._nodes[node_id].to_dict() for node_id in self.topological_order()
            ],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ActionGraph":
        return cls(
            name=str(data["name"]),
            version=str(data["version"]),
            contract_version=str(data.get("contract_version", "1.0.0")),
            modules=tuple(dict(item) for item in data.get("modules", ())),
            metadata=dict(data.get("metadata", {})),
            nodes=(ActionNode.from_dict(item) for item in data.get("nodes", ())),
        )

    @classmethod
    def from_json(cls, path: str | Path) -> "ActionGraph":
        with Path(path).open("r", encoding="utf-8") as stream:
            data = json.load(stream)
        if not isinstance(data, dict):
            raise ValueError("action graph JSON must contain an object")
        return cls.from_dict(data)


class ActionGraphAssembler:
    """Compose modules through namespaced entry and output-node connections."""

    def __init__(self) -> None:
        self._instances: list[ModuleInstance] = []
        self._namespaces: set[str] = set()

    def add(
        self,
        module: ActionGraphModule,
        *,
        namespace: str,
        bindings: Mapping[str, Any],
        after: Iterable[str] = (),
    ) -> ModuleInstance:
        if namespace in self._namespaces:
            raise ValueError(f"duplicate action module namespace: {namespace}")
        known_nodes = {
            node.node_id for instance in self._instances for node in instance.nodes
        }
        dependencies = tuple(after)
        unknown = set(dependencies) - known_nodes
        if unknown:
            raise ValueError(
                f"module {namespace!r} has unknown external dependencies: {sorted(unknown)}"
            )
        instance = module.instantiate(namespace, bindings, after=dependencies)
        self._instances.append(instance)
        self._namespaces.add(namespace)
        return instance

    def build(
        self,
        *,
        name: str,
        version: str,
        contract_version: str = "1.0.0",
        metadata: Mapping[str, Any] | None = None,
    ) -> ActionGraph:
        return ActionGraph(
            name=name,
            version=version,
            contract_version=contract_version,
            nodes=(node for instance in self._instances for node in instance.nodes),
            modules=(
                {
                    "namespace": instance.namespace,
                    "name": instance.module_name,
                    "version": instance.module_version,
                }
                for instance in self._instances
            ),
            metadata=metadata,
        )


def _validate_named_version(name: str, version: str, subject: str) -> None:
    if not name.strip():
        raise ValueError(f"{subject} name cannot be empty")
    _validate_version(version, subject)


def _validate_version(version: str, subject: str) -> None:
    if not _VERSION_PATTERN.fullmatch(version):
        raise ValueError(f"{subject} version must use MAJOR.MINOR.PATCH")


def _reject_low_level_commands(value: Any, *, path: str) -> None:
    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            key = str(raw_key).strip().lower()
            if key in _FORBIDDEN_CONTROL_FIELDS:
                raise ValueError(f"{path} contains forbidden low-level field {raw_key!r}")
            _reject_low_level_commands(child, path=f"{path}.{raw_key}")
    elif isinstance(value, (tuple, list)):
        for index, child in enumerate(value):
            _reject_low_level_commands(child, path=f"{path}[{index}]")


def _collect_placeholders(value: Any) -> set[str]:
    if isinstance(value, str):
        return set(_PLACEHOLDER_PATTERN.findall(value))
    if isinstance(value, Mapping):
        found: set[str] = set()
        for key, child in value.items():
            found.update(_collect_placeholders(key))
            found.update(_collect_placeholders(child))
        return found
    if isinstance(value, (tuple, list)):
        found = set()
        for child in value:
            found.update(_collect_placeholders(child))
        return found
    return set()


def _substitute(value: Any, bindings: Mapping[str, Any]) -> Any:
    if isinstance(value, str):
        exact = _PLACEHOLDER_PATTERN.fullmatch(value)
        if exact:
            return bindings[exact.group(1)]
        return _PLACEHOLDER_PATTERN.sub(
            lambda match: str(bindings[match.group(1)]), value
        )
    if isinstance(value, Mapping):
        return {
            str(_substitute(key, bindings)): _substitute(child, bindings)
            for key, child in value.items()
        }
    if isinstance(value, tuple):
        return tuple(_substitute(child, bindings) for child in value)
    if isinstance(value, list):
        return [_substitute(child, bindings) for child in value]
    return value
