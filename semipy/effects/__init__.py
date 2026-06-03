"""semipy.effects -- reified, verifiable, version-controlled real-world effects.

An effectful slot's generated function emits a reified :class:`EffectScript` via
the ``fx`` capability instead of touching the world. A trusted handler stages it
in a shadow (per a pluggable :class:`ArtifactBackend`), verifies and gates it,
then commits and ledgers it -- so the program's *effect* becomes a first-class,
provenance-tracked, revertable artifact.

The subsystem spans: the data model + ``fx`` capability, the backend Protocol
(memory / SQLite / external backends), static verification and gating, an
append-only effect ledger with provenance, and compensating revert. It is
opt-in via ``configure(effects_enabled=True)`` and the ``effect_*`` flags.
"""
from __future__ import annotations

from semipy.effects.backends import (
    ArtifactBackend,
    StateDelta,
    register_artifact_backend,
    registered_schemes,
    resolve_backend,
    scheme_of,
    unregister_artifact_backend,
)
from semipy.effects.backends.external import ExternalArtifactBackend
from semipy.effects.backends.memory import MemoryArtifactBackend
from semipy.effects.backends.sqlite import SqliteArtifactBackend
from semipy.effects.capability import EffectRecorder
from semipy.effects.compensate import revert, revert_ledger_event
from semipy.effects.provenance import ProvenanceChain, provenance_for
from semipy.effects.models import (
    DESTRUCTIVE_OPS,
    READ_OPS,
    Effect,
    EffectOp,
    EffectRefused,
    EffectResult,
    EffectScript,
    LedgerEvent,
)

__all__ = [
    "Effect",
    "EffectOp",
    "EffectScript",
    "EffectResult",
    "EffectRefused",
    "LedgerEvent",
    "READ_OPS",
    "DESTRUCTIVE_OPS",
    "EffectRecorder",
    "ArtifactBackend",
    "StateDelta",
    "MemoryArtifactBackend",
    "SqliteArtifactBackend",
    "ExternalArtifactBackend",
    "register_artifact_backend",
    "unregister_artifact_backend",
    "resolve_backend",
    "registered_schemes",
    "scheme_of",
    "revert",
    "revert_ledger_event",
    "provenance_for",
    "ProvenanceChain",
]
