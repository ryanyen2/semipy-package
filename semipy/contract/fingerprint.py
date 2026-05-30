"""Structural (digit-normalised) input fingerprinting.

Single source of truth for the normaliser used both to bucket a slot's observed
inputs (``slot_observations._obs_content_fingerprint``) and to key a contract
case by input *pattern*. Two concrete inputs that differ only in numeric IDs,
dates, or IPs (e.g. ``03/14/2025`` vs ``03/20/2025``) normalise to the same
token and therefore share a fingerprint — so one case covers a whole pattern and
the effect-diff can classify changes by pattern rather than by exact value.
"""
from __future__ import annotations

import hashlib
import re

_DIGIT_RE = re.compile(r"\d+")
_PREFIX_LEN = 24


def normalize_token(value: object, *, prefix_len: int = _PREFIX_LEN) -> str:
    """Normalise one value to a coarse pattern token (digits -> ``N``, truncated)."""
    return _DIGIT_RE.sub("N", str(value))[:prefix_len]


def _is_internal_key(key: object) -> bool:
    return isinstance(key, str) and (key.startswith("_") or key == "self")


def structural_input_fingerprint(
    runtime_values: dict[str, object],
    *,
    free_variables: list[str] | None = None,
) -> str:
    """Pattern fingerprint of one input row.

    Restricts to ``free_variables`` when provided (and any are present), so the
    fingerprint reflects only the values the slot actually consumes.
    """
    if not runtime_values:
        return hashlib.sha256(b"{}").hexdigest()[:16]
    keys = sorted((k for k in runtime_values.keys()), key=str)
    if free_variables:
        fv = {v for v in free_variables if v != "self"}
        restricted = [k for k in keys if k in fv]
        if restricted:
            keys = restricted
    parts: list[str] = []
    for k in keys:
        if _is_internal_key(k):
            continue
        parts.append(f"{k}={normalize_token(runtime_values[k])}")
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]
