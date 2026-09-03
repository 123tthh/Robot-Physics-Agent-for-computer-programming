"""Strict JSON configuration loading without runtime third-party dependencies."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .contracts import SkillContract
from .graph import SkillGraph


def load_json(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"configuration not found: {source}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {source}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"top-level JSON value must be an object: {source}")
    return data


def load_skill_contract(path: str | Path) -> SkillContract:
    return SkillContract.from_dict(load_json(path))


def load_skill_graph(path: str | Path) -> SkillGraph:
    return SkillGraph.from_dict(load_json(path))


def validate_contract_matches(implementation: SkillContract, configured: SkillContract) -> None:
    if implementation != configured:
        raise ValueError(
            f"configured contract for {configured.name!r} does not match "
            f"implementation version {implementation.version}"
        )
