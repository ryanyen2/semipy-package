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
from semipy.effects.models import EffectResult


def fn_is_effectful(fn: Callable[..., Any]) -> bool:
    """True iff ``fn``'s signature declares a parameter named ``fx``."""
    try:
        return "fx" in inspect.signature(fn).parameters
    except (ValueError, TypeError):
        return False


def make_recorder(
    provenance: Optional[dict[str, Any]] = None,
    reader: Optional[Callable[..., Any]] = None,
) -> EffectRecorder:
    return EffectRecorder(provenance=provenance, reader=reader)


def wrap_effect_result(recorder: EffectRecorder, value: Any, *, applied: bool = False,
                       event_id: str = "") -> EffectResult:
    """Build the :class:`EffectResult` an effectful slot returns to the caller."""
    return EffectResult(
        effect_script=recorder.script,
        value=value,
        applied=applied,
        event_id=event_id,
    )
