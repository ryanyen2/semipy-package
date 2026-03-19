from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Document:
    text: str
    metadata: dict[str, Any]
    blocks: list[dict[str, Any]]


@dataclass
class ExtractionSchema:
    fields: list[str]
    field_types: dict[str, type]
    required: list[str]
    description: str

