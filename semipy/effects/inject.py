"""Helpers to detect effectful generated functions and run them with ``fx``.

Kept dependency-light (only :mod:`inspect` + :mod:`semipy.effects` data types) so
the validator, gist runner, and slot resolver can import it without cycles.

A slot is *effectful* iff its generated function declares an ``fx`` parameter --
an inferred signal (no new user syntax). The generation prompt instructs the
model to add ``fx`` only when the spec implies mutating an external artifact.
"""
from __future__ import annotations

import inspect
from typing import Any, Callable, Optional

from semipy.effects.capability import EffectRecorder


def fn_is_effectful(fn: Callable[..., Any]) -> bool:
    """True iff ``fn``'s signature declares a parameter named ``fx``."""
    try:
        return "fx" in inspect.signature(fn).parameters
    except (ValueError, TypeError):
        return False


def make_recorder(
    provenance: Optional[dict[str, Any]] = None,
    world: Optional[Any] = None,
) -> EffectRecorder:
    return EffectRecorder(provenance=provenance, world=world)


