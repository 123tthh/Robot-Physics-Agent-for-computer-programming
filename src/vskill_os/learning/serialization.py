"""Strict, version-friendly JSON for the small standard-library record types."""

from __future__ import annotations

from dataclasses import asdict, fields, is_dataclass
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
from types import UnionType
from typing import Literal, Self, Union, get_args, get_origin, get_type_hints


@lru_cache(maxsize=None)
def _hints(kind: type) -> dict:
    return get_type_hints(kind)


def _decode(kind: type, value: object, path: str) -> object:
    origin, args = get_origin(kind), get_args(kind)
    if origin in (UnionType, Union):
        for choice in args:
            try:
                return _decode(choice, value, path)
            except ValueError:
                pass
    elif origin is Literal:
        if any(type(value) is type(choice) and value == choice for choice in args):
            return value
    elif origin is tuple:
        if isinstance(value, (list, tuple)) and args[-1] is Ellipsis:
            return tuple(_decode(args[0], item, f"{path}[{i}]") for i, item in enumerate(value))
    elif is_dataclass(kind):
        if type(value) is kind:
            return value
        if isinstance(value, dict):
            names = {field.name for field in fields(kind)}
            if set(value) != names:
                raise ValueError(f"{path}: fields differ; missing={sorted(names - set(value))}, "
                                 f"unknown={sorted(set(value) - names)}")
            return kind(**{name: _decode(_hints(kind)[name], item, f"{path}.{name}")
                           for name, item in value.items()})
    elif kind is float:
        if type(value) in (int, float):
            try:
                number = float(value)
                if math.isfinite(number):
                    return number
            except OverflowError:
                pass
    elif type(value) is kind:
        return value
    raise ValueError(f"{path}: expected {kind}, got {value!r}")


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


class JsonRecord:
    """Frozen dataclasses inherit strict construction, decoding and hashing.

    A digest binds a record's contents. It is not a signature or a sensor source
    authentication mechanism. Tuples and finite floats normalize identically on
    construction and JSON import, so their hashes survive a round trip.
    """

    def __post_init__(self) -> None:
        for field in fields(self):
            value = _decode(_hints(type(self))[field.name], getattr(self, field.name), field.name)
            object.__setattr__(self, field.name, value)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2, allow_nan=False) + "\n"

    def sha256(self) -> str:
        canonical = json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True,
                               separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @classmethod
    def from_dict(cls, data: dict) -> Self:
        return _decode(cls, data, cls.__name__)

    @classmethod
    def from_json(cls, text: str) -> Self:
        def reject_constant(value: str) -> None:
            raise ValueError(f"non-finite JSON number: {value}")

        return cls.from_dict(json.loads(text, object_pairs_hook=_unique_object,
                                        parse_constant=reject_constant))

    @classmethod
    def read(cls, path: str | Path) -> Self:
        return cls.from_json(Path(path).read_text(encoding="utf-8"))

    def write(self, path: str | Path) -> None:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(self.to_json(), encoding="utf-8")
