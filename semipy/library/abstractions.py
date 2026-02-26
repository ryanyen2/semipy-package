"""Data structures for DreamCoder-style abstraction discovery: patterns, primitives, library."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ASTPattern:
    """Structural pattern extracted from generated code (normalized AST)."""

    pattern_id: str
    normalized_source: str
    parameter_names: list[str]
    ast_hash: str
    embedding_id: str = ""


@dataclass
class PatternOccurrence:
    """One occurrence of a pattern in a commit."""

    session_id: str
    slot_id: str
    commit_id: str
    start_line: int
    end_line: int
    binding: dict[str, str]


@dataclass
class LibraryPrimitive:
    """Reusable function extracted from mined patterns (LLM-named and validated)."""

    primitive_id: str
    name: str
    source: str
    signature: str
    pattern_id: str
    occurrence_count: int
    commit_ids: list[str] = field(default_factory=list)
    validated: bool = False
    description: str = ""
    tags: list[str] = field(default_factory=list)
    embedding_id: str = ""


@dataclass
class AbstractionLibrary:
    """In-memory library of mined patterns and compressed primitives."""

    primitives: dict[str, LibraryPrimitive] = field(default_factory=dict)
    patterns: dict[str, ASTPattern] = field(default_factory=dict)
    occurrences: list[PatternOccurrence] = field(default_factory=list)
    version: int = 0
    last_analyzed_commits: set[str] = field(default_factory=set)
