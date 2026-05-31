"""Pluggable artifact backends.

An :class:`ArtifactBackend` knows how to stage, snapshot, diff, compensate, and
commit effects for one ``scheme://`` family of targets (``mem``, ``db``,
``file``, ``http`` ...). The handler is artifact-agnostic: it speaks only the
fixed op vocabulary and this Protocol, never branching on a target's domain.

Selection mirrors the document-backend pattern (env var) plus the tool-registry
pattern (programmatic registration): :func:`register_artifact_backend` adds a
backend for a scheme; :func:`resolve_backend` looks one up by a target's scheme.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, runtime_checkable

from semipy.effects.models import Effect


@dataclass
class StateDelta:
    """Data-agnostic description of how a target's state changed.

    Records are identified by an opaque, backend-chosen key. Never typed by the
    artifact's domain -- only added/removed/modified record keys and counts.
    """

    target: str
    added: list[Any] = field(default_factory=list)
    removed: list[Any] = field(default_factory=list)
    modified: list[Any] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.added or self.removed or self.modified)

    def affected_count(self) -> int:
        return len(self.added) + len(self.removed) + len(self.modified)


@runtime_checkable
class ArtifactBackend(Protocol):
    """Stage / verify / commit / revert effects for one target scheme."""

    target_scheme: str
    #: ``False`` for externalized, non-shadowable targets (APIs, email) that
    #: cannot be staged or auto-rolled-back and must route to approval.
    shadowable: bool

    def open_shadow(self, target: str) -> Any:
        """Open an isolated working copy of ``target`` (a ShadowHandle)."""
        ...

    def apply(self, shadow: Any, effect: Effect) -> None:
        """Apply ``effect`` to the shadow only (never the real artifact)."""
        ...

    def read(self, shadow: Any, effect: Effect) -> Any:
        """Return the records of a ``read`` effect from the shadow."""
        ...

    def snapshot(self, shadow: Any) -> str:
        """Return an opaque, comparable ref capturing the shadow's current state."""
        ...

    def diff(self, before_ref: str, after_ref: str) -> StateDelta:
        """Compare two snapshot refs into a :class:`StateDelta`."""
        ...

    def compensation_for(self, shadow: Any, effect: Effect) -> Optional[Effect]:
        """Reify the inverse of ``effect`` from the shadow pre-image, or ``None``."""
        ...

    def schema(self, target: str) -> Any:
        """Return an ArtifactSchema for ``target`` (keys / uniqueness) or ``None``."""
        ...

    def commit(self, shadow: Any) -> None:
        """Apply the shadow's staged state to the REAL artifact."""
        ...

    def discard(self, shadow: Any) -> None:
        """Drop the shadow without touching the real artifact."""
        ...


_ARTIFACT_BACKENDS: dict[str, ArtifactBackend] = {}


def register_artifact_backend(scheme: str, backend: ArtifactBackend) -> None:
    """Register ``backend`` to handle ``scheme://`` targets (e.g. ``"db"``)."""
    _ARTIFACT_BACKENDS[str(scheme)] = backend


def unregister_artifact_backend(scheme: str) -> None:
    _ARTIFACT_BACKENDS.pop(str(scheme), None)


def registered_schemes() -> list[str]:
    return sorted(_ARTIFACT_BACKENDS)


def scheme_of(target: str) -> str:
    """Return the ``scheme`` of a ``scheme://name`` target (empty if none)."""
    if "://" in target:
        return target.split("://", 1)[0]
    return ""


def resolve_backend(target: str) -> ArtifactBackend:
    """Look up the backend for ``target``'s scheme.

    A ``SEMIPY_EFFECT_BACKEND`` env var pins a default scheme used when a target
    carries no ``scheme://`` prefix.
    """
    scheme = scheme_of(target)
    if not scheme:
        scheme = (os.environ.get("SEMIPY_EFFECT_BACKEND") or "").strip()
    backend = _ARTIFACT_BACKENDS.get(scheme)
    if backend is None:
        raise KeyError(
            f"No artifact backend registered for scheme {scheme!r} (target {target!r}). "
            f"Registered: {registered_schemes()}. Use register_artifact_backend(scheme, backend)."
        )
    return backend
