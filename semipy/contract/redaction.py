"""Capture-time redaction and provenance-based ship-flag defaults (R6, R14, U3).

KTD-4: redaction runs when evidence is *persisted to the ledger*, not only at
ship time -- a recorded webhook payload (D2) can carry secrets the developer's
own disk should never hold in the clear. Redaction is pattern-based (bearer
tokens, API-key shapes, emails, long digit runs) plus structural (fields named
like secrets: password/token/secret/api_key/authorization/...). Redacted spans
are replaced with an auditable ``<REDACTED:...>`` marker, never silently
dropped, so a case stays honest about what it no longer pins.

Ship eligibility (R14) defaults from provenance: a case whose input derives
from an external source defaults to ``ship=False``; synthetic,
relation-generated, or user-adjudicated cases default to ``ship=True``.
Redaction is defense in depth for what does ship -- the provenance-based
default is the real guard for external data (KTD-4).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from semipy.contract.models import ContractCase, SlotContract

# Provenance category that flips the ship-flag default to False (R14).
EXTERNAL_PROVENANCE = "external"

# Structural rule: a dict key whose name looks like a secret gets its whole
# value masked, regardless of content or shape.
_SECRET_FIELD_SUBSTRINGS = (
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "access_key",
    "private_key",
    "credential",
)

# Pattern-based rules: content that looks like a secret regardless of the
# field it lives in.
_BEARER_RE = re.compile(r"Bearer\s+[A-Za-z0-9\-_.=]+", re.IGNORECASE)
_EMAIL_RE = re.compile(r"[A-Za-z0-9_.+\-]+@[A-Za-z0-9\-]+\.[A-Za-z0-9\-.]+")
_LONG_DIGIT_RE = re.compile(r"\d{9,}")
# API-key shape: a long opaque token (>=24 word chars) mixing letters and
# digits -- natural-language words are almost never this long, so this stays
# conservative. ``<``/``>``/``:`` are excluded from the class, so the
# ``<REDACTED:...>`` marker itself can never satisfy this pattern (idempotent).
_TOKEN_RE = re.compile(r"\b[A-Za-z0-9_\-]{24,}\b")

# A fully-masked value (the whole thing this case pins is the marker, not just
# a span within a larger value) -- see ``redact_case``'s ``target_lost``. The
# optional quotes account for ``expected_repr``, which is a Python ``repr()``
# string, so a fully-redacted *string* output is quoted (``"'<REDACTED:...>'"``).
_FULL_MARKER_RE = re.compile(r"^['\"]?<REDACTED:[a-z0-9_\-]+>['\"]?$")


def _is_secret_field_name(name: str) -> bool:
    n = name.strip().lower().replace("-", "_")
    return any(s in n for s in _SECRET_FIELD_SUBSTRINGS)


def _structural_marker(key: str) -> str:
    return f"<REDACTED:{key.strip().lower()}>"


def _is_full_marker(s: str) -> bool:
    return bool(_FULL_MARKER_RE.fullmatch(s))


def _sub_pattern(text: str, pattern: re.Pattern[str], label: str) -> tuple[str, bool]:
    changed = False

    def repl(_m: re.Match[str]) -> str:
        nonlocal changed
        changed = True
        return f"<REDACTED:{label}>"

    return pattern.sub(repl, text), changed


def _redact_tokens(text: str) -> tuple[str, bool]:
    changed = False
    out: list[str] = []
    last = 0
    for m in _TOKEN_RE.finditer(text):
        tok = m.group(0)
        if not (any(c.isdigit() for c in tok) and any(c.isalpha() for c in tok)):
            continue
        out.append(text[last : m.start()])
        out.append("<REDACTED:api_key>")
        last = m.end()
        changed = True
    if not changed:
        return text, False
    out.append(text[last:])
    return "".join(out), True


def _redact_string(s: str) -> tuple[str, bool]:
    changed = False
    s, c = _sub_pattern(s, _BEARER_RE, "bearer_token")
    changed = changed or c
    s, c = _sub_pattern(s, _EMAIL_RE, "email")
    changed = changed or c
    s, c = _sub_pattern(s, _LONG_DIGIT_RE, "digits")
    changed = changed or c
    s, c = _redact_tokens(s)
    changed = changed or c
    return s, changed


def redact_value(value: Any, *, key: str | None = None) -> tuple[Any, bool]:
    """Recursively redact secret-shaped content in *value*.

    ``key`` is the dict key *value* was found under (``None`` at the top level
    or inside a list) -- a secret-named key masks its whole value, regardless
    of type or content. Returns ``(new_value, changed)``; idempotent: redacting
    an already-redacted value is a no-op (the ``<REDACTED:...>`` marker never
    matches a secret pattern and is recognised verbatim for the structural rule).
    """
    if key is not None and _is_secret_field_name(key):
        marker = _structural_marker(key)
        if value == marker:
            return value, False
        return marker, True
    if isinstance(value, dict):
        changed = False
        out: dict[Any, Any] = {}
        for k, v in value.items():
            nv, c = redact_value(v, key=str(k) if isinstance(k, str) else None)
            out[k] = nv
            changed = changed or c
        return out, changed
    if isinstance(value, (list, tuple)):
        changed = False
        items: list[Any] = []
        for v in value:
            nv, c = redact_value(v, key=None)
            items.append(nv)
            changed = changed or c
        return (tuple(items) if isinstance(value, tuple) else items), changed
    if isinstance(value, str):
        return _redact_string(value)
    return value, False


def _primary_key(input_sample: dict[str, Any]) -> str | None:
    """Mirrors ``ContractCase.primary_input`` but returns the key, not the value."""
    for k in input_sample:
        if isinstance(k, str) and (k.startswith("_") or k == "self"):
            continue
        return k
    return None


@dataclass
class RedactionResult:
    changed: bool
    # True when redaction replaced the *entire* value this case actually
    # asserts on (its primary input, or -- for an "example" case -- the pinned
    # output) rather than a span within a larger structure. Such a case no
    # longer honestly pins anything and must not be silently kept active.
    target_lost: bool = False


def redact_case(case: ContractCase) -> RedactionResult:
    """Scrub secrets/PII from a case's stored content in place (KTD-4).

    Redacts ``input_sample`` and ``source_profile`` (any external-input
    provenance profile), and -- for "example" cases -- the pinned
    ``expected_repr``. Idempotent: re-running on an already-redacted case
    changes nothing further.
    """
    changed = False
    primary_key = _primary_key(case.input_sample)

    new_input, c = redact_value(case.input_sample)
    if c:
        case.input_sample = new_input
        changed = True

    if case.source_profile:
        new_profile, c = redact_value(case.source_profile)
        if c:
            case.source_profile = new_profile
            changed = True

    target_lost = False
    if primary_key is not None:
        primary_after = case.input_sample.get(primary_key)
        if isinstance(primary_after, str) and _is_full_marker(primary_after):
            target_lost = True

    if case.kind == "example" and case.expected_repr:
        new_expected, c = _redact_string(case.expected_repr)
        if c:
            case.expected_repr = new_expected
            changed = True
            if _is_full_marker(new_expected):
                target_lost = True

    return RedactionResult(changed=changed, target_lost=target_lost)


def default_ship_flag(provenance: str) -> bool:
    """Ship-eligibility default from provenance (R14 / KTD-4).

    A case whose input derives from an external source defaults to
    ``ship=False``; synthetic, relation-generated, or user-adjudicated cases
    (and cases with no provenance category recorded) default to ``ship=True``.
    """
    return provenance != EXTERNAL_PROVENANCE


def apply_capture_time_policy(case: ContractCase, contract: SlotContract) -> RedactionResult:
    """Run capture-time redaction and the provenance ship-flag default on a case
    about to be persisted (KTD-4).

    Auto-quarantines the case, via the contract's existing quarantine
    machinery, when redaction removed the value it actually asserts on --
    the case would otherwise stay active while silently pinning nothing.
    """
    result = redact_case(case)
    case.ship = default_ship_flag(case.provenance)
    if result.target_lost and case.is_active():
        contract.quarantine(
            case.case_id,
            "auto-quarantined: capture-time redaction removed the asserted value",
        )
    return result
