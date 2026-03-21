"""
Helpers for normalizing structured slot outputs (e.g. dict rows) into dataclass instances.
"""
from __future__ import annotations

import dataclasses
from typing import Any, TypeVar

T = TypeVar("T")


def coerce_dataclass_list(rows: list[Any], cls: type[T]) -> list[T]:
    """
    Convert dict elements into instances of dataclass *cls* when all field names are present.
    Elements already of type *cls* are kept; unrecognized elements are appended unchanged.
    """
    if not dataclasses.is_dataclass(cls):
        return rows  # type: ignore[return-value]
    field_names = tuple(f.name for f in dataclasses.fields(cls))
    out: list[Any] = []
    for r in rows:
        if isinstance(r, cls):
            out.append(r)
            continue
        if isinstance(r, dict) and all(k in r for k in field_names):
            try:
                out.append(cls(**{k: r[k] for k in field_names}))
                continue
            except (TypeError, ValueError):
                pass
        out.append(r)
    return out  # type: ignore[return-value]


def coerce_dataclass(obj: Any, cls: type[T]) -> T | Any:
    """If *obj* is a dict with all fields of dataclass *cls*, construct *cls*; else return *obj*."""
    if not dataclasses.is_dataclass(cls):
        return obj
    if isinstance(obj, cls):
        return obj
    if isinstance(obj, dict):
        field_names = tuple(f.name for f in dataclasses.fields(cls))
        if all(k in obj for k in field_names):
            try:
                return cls(**{k: obj[k] for k in field_names})
            except (TypeError, ValueError):
                pass
    return obj
