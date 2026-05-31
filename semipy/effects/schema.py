"""Artifact schema: the key/uniqueness facts a blast-radius proof reasons over.

A backend reports an :class:`ArtifactSchema` for a target via ``backend.schema``.
The only fact the prover needs is which column sets are *unique*: a mutating
``update``/``delete`` whose selector contains a unique key provably affects at
most one record, for all inputs and all artifact states (see ``effects/prove.py``).
Data-agnostic: a unique key is a set of column names; semipy never interprets what
the columns mean.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ArtifactSchema:
    target: str
    #: Each entry is a set of columns that is unique (primary key / unique index).
    unique_keys: list[frozenset[str]] = field(default_factory=list)

    def has_unique_subset(self, selector_keys: set[str]) -> bool:
        """True iff some unique key is fully contained in ``selector_keys``.

        Containing a unique key means the selector pins at most one record.
        """
        return any(uk and uk <= selector_keys for uk in self.unique_keys)
