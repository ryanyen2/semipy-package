"""semipy.effects -- reified, verifiable, version-controlled real-world effects.

An effectful slot's generated function emits a reified :class:`EffectScript` via
the ``fx`` capability instead of touching the world. A trusted handler stages it
in a shadow (per a pluggable :class:`ArtifactBackend`), verifies and gates it,
then commits and ledgers it -- so the program's *effect* becomes a first-class,
provenance-tracked, revertable artifact.

Stage 0 surface: the data model, the ``fx`` capability, and the backend Protocol
plus the in-memory backend. Verification, ledger, provenance, and revert land in
later stages.
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
    EFFECT_INVARIANT_NAMES,
    READ_OPS,
    Effect,
    EffectCase,
    EffectInvariant,
    EffectOp,
    EffectResult,
    EffectScript,
    LedgerEvent,
    SlotEffectContract,
)

__all__ = [
    "Effect",
    "EffectOp",
    "EffectScript",
    "EffectResult",
    "EffectInvariant",
    "EffectCase",
    "SlotEffectContract",
    "LedgerEvent",
    "EFFECT_INVARIANT_NAMES",
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
