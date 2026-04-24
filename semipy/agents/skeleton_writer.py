"""
Write the structured ``#< key: value`` steering surface around each slot anchor.

``#>`` lines are user spec (never modified). ``#<`` lines are system-managed:
on GENERATE/ADAPT we recompute the slot's :class:`SteeringBlock` and splice
two micro-blocks around the slot's anchor line — a provenance zone above and
an effect zone below — at the anchor's own indent.

Zone P (provenance, above the anchor): ``intent``, ``given``, ``by``, ``unless``.
Zone E (effect, below the anchor):     ``yields``, ``verified``.

Placement is structural (no substring anchors, no body-start fallback).
Multiple slots in the same function get independent blocks — a write to one
slot leaves the others untouched because we only splice the contiguous ``#<``
band directly around the target anchor.

A key that has been *promoted* (the user has written the same key on a ``#>``
line inside this slot's spec block) is omitted from the ``#<`` surface so the
two never duplicate each other.
"""
from __future__ import annotations

import re
import threading
import traceback
from pathlib import Path
from typing import Any

from semipy.agents.console_io import print_pipeline_log
from semipy.lowering import strip_skeleton_lines
from semipy.models import CommitmentRecord, SteeringBlock
from semipy.types import CacheEntry, SlotSpec, SemiCallSite


# In-process (not cross-process) per-file write coordination.
_file_write_locks: dict[str, threading.Lock] = {}
_file_write_locks_mutex = threading.Lock()

# Steering keys grouped by zone.
_ZONE_P_KEYS: tuple[str, ...] = ("intent", "given", "by", "unless")
_ZONE_E_KEYS: tuple[str, ...] = ("yields", "verified")

_MAX_BLOCK_LINES = 10
_MAX_VALUE_WORDS = 14

_KEY_LINE_RE = re.compile(r"^\s*#<\s*([A-Za-z_]+)\s*:\s*(.*?)\s*$")
_PROMOTED_KEY_RE = re.compile(
    r"^\s*#\s*>\s*(intent|given|by|unless|yields|alt|verified|goal|commits|because)\s*:",
    re.IGNORECASE,
)


def _log_surface(slot_spec: SlotSpec, message: str) -> None:
    call_site = SemiCallSite(
        filename=slot_spec.source_span[0],
        lineno=slot_spec.source_span[1],
        func_qualname=slot_spec.enclosing_function_qualname,
    )
    print_pipeline_log(call_site, "surface", message)


def _lock_for_path(path: Path) -> threading.Lock:
    key = str(path.resolve())
    with _file_write_locks_mutex:
        if key not in _file_write_locks:
            _file_write_locks[key] = threading.Lock()
        return _file_write_locks[key]


def _atomic_write_text(path: Path, content: str) -> None:
    tmp = path.with_name(path.name + ".semipy_skeleton.tmp")
    try:
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(path)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Anchor detection
# ---------------------------------------------------------------------------


def _is_hash_arrow_line(line: str) -> bool:
    stripped = line.lstrip()
    return stripped.startswith("#>") or stripped.startswith("# >")


def _is_skeleton_lt_line(line: str) -> bool:
    stripped = line.lstrip()
    return stripped.startswith("#<") or stripped.startswith("# <")


def _leading_indent(line: str) -> str:
    return line[: len(line) - len(line.lstrip())]


def _is_pure_comment_arrow(line: str) -> bool:
    """Return True when the line is a standalone ``#>`` comment (no code before)."""
    return _is_hash_arrow_line(line)


def _line_has_inline_code_with_arrow(line: str) -> bool:
    """``name = ... #> spec`` style — code before the ``#>`` marker."""
    m = re.search(r"#\s*>", line)
    if m is None:
        return False
    code_part = line[: m.start()]
    return bool(code_part.strip())


def _find_anchor_line(
    file_lines: list[str],
    stripped_file_text: str,
    slot_spec: SlotSpec,
) -> tuple[int, str] | None:
    """Return ``(anchor_line_idx, anchor_indent)`` or None.

    Scans outward from the slot's source span start line. Preference order:
    1. First ``#>`` line on or after the hint.
    2. First line at-or-after the hint with an inline ``#>``, ``name = ...``, or
       ``semi(`` call — skipping any ``#<`` lines.

    We match on the raw file lines (not the stripped scaffold) so line numbers
    remain the same as the on-disk file.
    """
    hint_1based = slot_spec.source_span[1]
    if hint_1based <= 0 or not file_lines:
        return None

    # Clamp hint to file range (0-based index)
    hint_idx = max(0, min(hint_1based - 1, len(file_lines) - 1))

    # Use the stripped view only to verify the hint points at a slot region
    # (we don't actually need it for placement — raw lines are the truth).
    del stripped_file_text  # stripped view currently unused; kept for future use

    def _candidate_kind(line: str) -> int:
        """Rank candidates: higher = more preferred.

        4 — ``name = ... #> ...`` (inline anchor with code; the definitive anchor).
        3 — ``name = ...`` (placeholder binding without inline ``#>``).
        2 — ``semi(...)`` call.
        1 — standalone ``#>`` comment line (first line of a spec block, but
            a promoted steering-key line ranks lower so it loses to inlines).
        0 — not a candidate.
        A promoted-key comment line (``#> goal:``, ``#> verified:`` etc.) is
        only used when nothing else matches.
        """
        stripped = line.lstrip()
        if stripped.startswith("#<"):
            return 0
        if _line_has_inline_code_with_arrow(line):
            return 4
        trimmed = stripped
        if re.match(r"[A-Za-z_]\w*\s*=\s*\.\.\.", trimmed):
            return 3
        if "semi(" in trimmed:
            return 2
        if _is_hash_arrow_line(line):
            # Promoted steering-key `#>` lines (#> goal:, #> verified: …) are
            # spec contract lines, not the slot's primary anchor; rank them
            # below inlines / placeholders so we never plant Zone E just
            # under a promoted key.
            if _PROMOTED_KEY_RE.match(line):
                return 1  # still a valid last-resort anchor
            return 1
        return 0

    def _scan_range(indices: list[int]) -> tuple[int, str] | None:
        # First pass: accept only strong anchors (rank >= 2).
        fallback: tuple[int, str] | None = None
        for idx in indices:
            line = file_lines[idx]
            rank = _candidate_kind(line)
            if rank >= 2:
                return idx, _leading_indent(line)
            if rank == 1 and fallback is None:
                fallback = (idx, _leading_indent(line))
        return fallback

    # Scan forward from the hint for a strong anchor; if that fails, try
    # backward (line shifts).  Fall back to the first `#>` comment if no
    # code-bearing anchor exists (pure `#>` block slots).
    forward = list(range(hint_idx, len(file_lines)))
    found = _scan_range(forward)
    if found is not None:
        return found
    backward = list(range(hint_idx - 1, -1, -1))
    return _scan_range(backward)


def _find_zone_e_anchor_end(
    file_lines: list[str],
    anchor_idx: int,
    anchor_indent: str,
) -> int:
    """Return the index whose *next* line is where Zone E should start.

    For inline ``name = ... #> spec`` the anchor line itself is the end — Zone E
    goes immediately after it.  For a ``#>`` comment block (standalone ``#>``
    lines at ``anchor_indent``) scan forward over contiguous ``#>`` lines at
    the same indent; the last one is the anchor-end.  For a multi-line
    ``semi( ... )`` call, walk forward until the parenthesis balance returns
    to zero so Zone E lands *after* the closing ``)`` — never inside the call
    arguments.
    """
    anchor_line = file_lines[anchor_idx]
    # If the anchor line has inline code followed by `#>`, Zone E sits after it.
    if _line_has_inline_code_with_arrow(anchor_line):
        return _walk_to_call_end(file_lines, anchor_idx)
    if not _is_pure_comment_arrow(anchor_line):
        # Non-arrow anchor (e.g. `name = ...` without inline `#>` or `semi(` call);
        # Zone E goes directly after that line (after any trailing multi-line call).
        return _walk_to_call_end(file_lines, anchor_idx)
    # Standalone `#>` block — walk forward over contiguous `#>` lines at same indent.
    end = anchor_idx
    i = anchor_idx + 1
    while i < len(file_lines):
        line = file_lines[i]
        core = line.rstrip("\r\n")
        if core.strip() == "":
            break
        if _leading_indent(line) != anchor_indent:
            break
        if not _is_hash_arrow_line(line):
            break
        end = i
        i += 1
    return end


def _walk_to_call_end(file_lines: list[str], start_idx: int) -> int:
    """Return the index of the last line that belongs to the logical statement
    starting at ``start_idx``.

    Handles multi-line ``semi( ... )`` or ``fn( ... )`` calls by tracking
    parenthesis / bracket / brace depth.  String literals are respected so
    parens inside strings do not desync the counter.  When the opener is
    balanced on the starting line, returns ``start_idx`` unchanged.
    """
    n = len(file_lines)
    if start_idx < 0 or start_idx >= n:
        return start_idx

    def _count_delims(text: str) -> tuple[int, int, int]:
        """Return (paren, bracket, brace) depth delta ignoring strings/comments."""
        paren = bracket = brace = 0
        i = 0
        in_str: str | None = None
        while i < len(text):
            ch = text[i]
            if in_str is not None:
                if ch == "\\":
                    i += 2
                    continue
                if ch == in_str:
                    in_str = None
                i += 1
                continue
            if ch == "#":
                break
            if ch in ("'", '"'):
                # Triple-quote detection
                if text[i : i + 3] in ("'''", '"""'):
                    triple = text[i : i + 3]
                    end = text.find(triple, i + 3)
                    if end == -1:
                        return paren, bracket, brace
                    i = end + 3
                    continue
                in_str = ch
                i += 1
                continue
            if ch == "(":
                paren += 1
            elif ch == ")":
                paren -= 1
            elif ch == "[":
                bracket += 1
            elif ch == "]":
                bracket -= 1
            elif ch == "{":
                brace += 1
            elif ch == "}":
                brace -= 1
            i += 1
        return paren, bracket, brace

    p = b = c = 0
    i = start_idx
    while i < n:
        dp, db, dc = _count_delims(file_lines[i])
        p += dp
        b += db
        c += dc
        if p <= 0 and b <= 0 and c <= 0:
            return i
        i += 1
    return n - 1


# ---------------------------------------------------------------------------
# Existing zone parse
# ---------------------------------------------------------------------------


def _parse_zone_p(
    file_lines: list[str],
    anchor_idx: int,
    anchor_indent: str,
) -> tuple[list[int], int]:
    """Identify existing Zone P lines — ``#<`` lines in the annotation band above ``anchor_idx``.

    Walk backward from ``anchor_idx - 1`` while the line is at ``anchor_indent``
    and begins with either ``#<`` (steering) or ``#>`` (user contract).  ``#>``
    lines are passed through but never returned (they are not ours to rewrite);
    we only return the indices of ``#<`` lines.  The scan stops at the first
    blank line, differently-indented line, or non-comment code line.

    Returns ``(indices, band_start_idx)`` — the indices of ``#<`` lines to
    replace and the start of the full annotation band (first index that belongs
    to the band).  Callers splice ``#<`` lines by removing them individually.
    """
    if anchor_idx <= 0:
        return [], anchor_idx

    band_start = anchor_idx
    lt_indices: list[int] = []
    i = anchor_idx - 1
    while i >= 0:
        line = file_lines[i]
        core = line.rstrip("\r\n")
        if core.strip() == "":
            break
        if _leading_indent(line) != anchor_indent:
            break
        stripped = core.strip()
        if stripped.startswith("#<"):
            lt_indices.append(i)
            band_start = i
        elif stripped.startswith("#>") or stripped.startswith("# >"):
            # User contract line; passthrough — does not stop the scan but
            # also is not something we rewrite.
            band_start = i
        else:
            break
        i -= 1

    lt_indices.sort()
    return lt_indices, band_start


def _parse_zone_e(
    file_lines: list[str],
    anchor_end_idx: int,
    anchor_indent: str,
) -> tuple[list[int], int]:
    """Identify existing Zone E lines — ``#<`` lines in the band below ``anchor_end_idx``.

    Mirror of :func:`_parse_zone_p` applied after the anchor: ``#<`` lines
    (returned) and ``#>`` lines (pass-through) form the annotation band; any
    blank line, differently-indented line, or code line stops the scan.
    """
    n = len(file_lines)
    lt_indices: list[int] = []
    i = anchor_end_idx + 1
    while i < n:
        line = file_lines[i]
        core = line.rstrip("\r\n")
        if core.strip() == "":
            break
        if _leading_indent(line) != anchor_indent:
            break
        stripped = core.strip()
        if stripped.startswith("#<"):
            lt_indices.append(i)
        elif stripped.startswith("#>") or stripped.startswith("# >"):
            pass
        else:
            break
        i += 1

    lt_indices.sort()
    return lt_indices, anchor_end_idx + 1


def _parse_key_value(line: str) -> tuple[str, str] | None:
    match = _KEY_LINE_RE.match(line.rstrip("\r\n"))
    if match is None:
        return None
    key = match.group(1).lower()
    value = match.group(2).strip()
    return key, value


# ---------------------------------------------------------------------------
# Block formatting
# ---------------------------------------------------------------------------


def _truncate_value(value: str) -> str:
    words = (value or "").split()
    if not words:
        return ""
    if len(words) <= _MAX_VALUE_WORDS:
        return " ".join(words)
    return " ".join(words[:_MAX_VALUE_WORDS])


def _render_key_lines(
    steering: SteeringBlock,
    key: str,
    indent: str,
    promoted_keys: set[str],
) -> list[str]:
    """Render ``#< key: value`` line(s) for one steering key.

    Returns an empty list when the key is promoted (already on a ``#>`` line),
    the entry is empty, or the value is all-whitespace.  The ``given`` key may
    produce up to three lines.
    """
    if key in promoted_keys:
        return []
    if key == "given":
        out: list[str] = []
        for g in list(steering.given)[:3]:
            value = _truncate_value(g.value)
            if value:
                out.append(f"{indent}#< given: {value}\n")
        return out
    if key == "unless":
        out = []
        for u in list(steering.unless)[:2]:
            value = _truncate_value(u.value)
            if value:
                out.append(f"{indent}#< unless: {value}\n")
        return out
    entry = getattr(steering, key, None)
    value = _truncate_value(getattr(entry, "value", "") or "") if entry else ""
    if not value:
        return []
    return [f"{indent}#< {key}: {value}\n"]


def _zone_p_lines(
    steering: SteeringBlock,
    indent: str,
    promoted_keys: set[str],
) -> list[str]:
    """Render Zone P (provenance) lines: intent, given, by, unless."""
    out: list[str] = []
    for key in _ZONE_P_KEYS:
        out.extend(_render_key_lines(steering, key, indent, promoted_keys))
    if len(out) > _MAX_BLOCK_LINES:
        out = out[:_MAX_BLOCK_LINES]
    return out


def _zone_e_lines(
    steering: SteeringBlock,
    indent: str,
    promoted_keys: set[str],
) -> list[str]:
    """Render Zone E (effect) lines: yields, verified."""
    out: list[str] = []
    for key in _ZONE_E_KEYS:
        out.extend(_render_key_lines(steering, key, indent, promoted_keys))
    if len(out) > _MAX_BLOCK_LINES:
        out = out[:_MAX_BLOCK_LINES]
    return out


# ---------------------------------------------------------------------------
# Promotion detection
# ---------------------------------------------------------------------------


def detect_promoted_keys(slot_spec: Any) -> dict[str, str]:
    """Return ``{key: promoted_value}`` for steering keys found on ``#>`` lines.

    Scans the slot's ``spec_text`` (already ``#>``-stripped; any ``key:`` prefix
    inside the multi-line block is preserved) and the enclosing-function
    source (where ``#>`` markers are still present).  First occurrence wins.
    """
    promoted: dict[str, str] = {}

    # `#>` is already stripped from `spec_text`; treat each line as if it were a
    # direct `key: value` candidate.
    spec_text = getattr(slot_spec, "spec_text", "") or ""
    for raw in spec_text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = re.match(
            r"^(goal|given|yields|commits|because|alt|verified)\s*:\s*(.*)$",
            line,
            re.IGNORECASE,
        )
        if m:
            key = m.group(1).lower()
            if key not in promoted:
                promoted[key] = m.group(2).strip()

    # Inline `#>` on an anchor line (e.g. `name = ... #> verified: ...`) and the
    # enclosing function source both preserve the `#>` marker.
    enc = getattr(slot_spec, "enclosing_function_source", "") or ""
    for raw in enc.splitlines():
        m = _PROMOTED_KEY_RE.match(raw)
        if m:
            key = m.group(1).lower()
            # Extract value after the first `:` on the matched line
            after_arrow = raw.lstrip().lstrip("#").lstrip()
            if after_arrow.startswith(">"):
                after_arrow = after_arrow[1:].lstrip()
            if ":" in after_arrow:
                value = after_arrow.split(":", 1)[1].strip()
            else:
                value = ""
            if key not in promoted:
                promoted[key] = value

    return promoted


def _detect_promoted_keys_from_file(
    file_lines: list[str],
    anchor_idx: int,
    anchor_indent: str,
) -> dict[str, str]:
    """Scan the anchor line and the contiguous ``#>`` comment block for promoted keys.

    This is a second, file-grounded check: a user may promote a key by editing
    a ``#<`` line to ``#>`` after the portal was written but before
    ``surface_skeleton`` runs.  We pick those up even if ``spec_text`` has not
    been re-scanned yet.
    """
    promoted: dict[str, str] = {}

    def _accept(raw_line: str) -> None:
        m = _PROMOTED_KEY_RE.match(raw_line)
        if m is None:
            return
        key = m.group(1).lower()
        after = raw_line.lstrip().lstrip("#").lstrip()
        if after.startswith(">"):
            after = after[1:].lstrip()
        if ":" in after:
            value = after.split(":", 1)[1].strip()
        else:
            value = ""
        promoted.setdefault(key, value)

    # Inline `#>` on the anchor line.
    if 0 <= anchor_idx < len(file_lines):
        anchor_line = file_lines[anchor_idx]
        # Inline: split on `#>`/`# >` and match `key:` on the right side.
        if _line_has_inline_code_with_arrow(anchor_line):
            arrow = re.search(r"#\s*>", anchor_line)
            if arrow is not None:
                right = anchor_line[arrow.end() :].strip()
                m = re.match(
                    r"^(goal|given|yields|commits|because|alt|verified)\s*:\s*(.*)$",
                    right,
                    re.IGNORECASE,
                )
                if m:
                    promoted.setdefault(m.group(1).lower(), m.group(2).strip())

    # Standalone `#>` block — walk backward then forward from the anchor index.
    for i in range(anchor_idx, -1, -1):
        line = file_lines[i]
        if line.strip() == "":
            break
        if _leading_indent(line) != anchor_indent:
            break
        if _is_hash_arrow_line(line):
            _accept(line)
            continue
        if _is_skeleton_lt_line(line):
            continue
        break
    for i in range(anchor_idx + 1, len(file_lines)):
        line = file_lines[i]
        if line.strip() == "":
            break
        if _leading_indent(line) != anchor_indent:
            break
        if _is_hash_arrow_line(line):
            _accept(line)
            continue
        if _is_skeleton_lt_line(line):
            continue
        break

    return promoted


def _detect_user_overrides(
    existing_lines: list[str],
    stored_steering: SteeringBlock,
) -> dict[str, str]:
    """Return on-disk ``key -> value`` pairs whose value differs from ``stored_steering``.

    ``given`` is compared as a joined string of its on-disk occurrences against
    the joined list of stored values, preserving order.
    """
    on_disk: dict[str, list[str]] = {}
    for line in existing_lines:
        parsed = _parse_key_value(line)
        if parsed is None:
            continue
        key, value = parsed
        on_disk.setdefault(key, []).append(value)

    overrides: dict[str, str] = {}

    for key in _ZONE_P_KEYS + _ZONE_E_KEYS:
        if key == "given":
            disk_values = on_disk.get("given", [])
            stored_values = [g.value for g in stored_steering.given if g.value]
            if disk_values != stored_values and disk_values:
                overrides["given"] = " | ".join(disk_values)
            continue
        disk_value_list = on_disk.get(key, [])
        if not disk_value_list:
            continue
        disk_value = disk_value_list[0]
        stored_entry = getattr(stored_steering, key, None)
        stored_value = getattr(stored_entry, "value", "") if stored_entry else ""
        if disk_value and disk_value != stored_value:
            overrides[key] = disk_value

    return overrides


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def _legacy_annotations_fallback(slot_spec: SlotSpec, record: CommitmentRecord) -> None:
    """Compatibility shim for old CommitmentRecord annotations.

    If a commit still has the legacy ``annotations`` list but no
    :class:`SteeringBlock`, we surface nothing — the new writer drops these
    silently. This keeps existing portal JSON loadable without clobbering
    files with stale BDD-style prose.
    """
    del slot_spec, record


def surface_skeleton(
    slot_spec: SlotSpec,
    cache_entry: CacheEntry,
) -> dict[str, str]:
    """Write the two-zone ``#< key: value`` surface for this slot.

    Returns a ``dict`` containing:
      - any on-disk user overrides detected before the write (``key -> value``
        user-edited in a ``#<`` line)
      - all promoted keys (``key -> promoted value`` found on ``#>`` lines)

    Callers persist the combined mapping as ``steering_overrides`` hints for
    the next generation.
    """
    try:
        return _surface_skeleton_impl(slot_spec, cache_entry)
    except Exception:
        traceback.print_exc()
        return {}


def _surface_skeleton_impl(
    slot_spec: SlotSpec,
    cache_entry: CacheEntry,
) -> dict[str, str]:
    target = Path(slot_spec.source_span[0])
    try:
        resolved = str(target.resolve())
    except OSError:
        return {}
    if "ipykernel" in resolved.replace("\\", "/").lower():
        return {}
    if not target.is_file() or target.suffix.lower() != ".py":
        return {}

    steering = getattr(cache_entry, "steering", None)
    if not isinstance(steering, SteeringBlock):
        # Legacy commits may still have CommitmentRecord.annotations but no SteeringBlock.
        record = getattr(cache_entry, "commitment_record", None)
        if isinstance(record, CommitmentRecord):
            _legacy_annotations_fallback(slot_spec, record)
        _log_surface(slot_spec, "Steering surface skipped: no SteeringBlock on cache entry.")
        return {}

    lock = _lock_for_path(target)
    with lock:
        try:
            file_text = target.read_text(encoding="utf-8")
        except OSError:
            return {}

        # Build a stripped view so `#<` placeholder lines do not interfere with
        # anchor matching downstream.  `strip_skeleton_lines` replaces each
        # `#<` line with a single `#` so absolute line numbers stay aligned.
        stripped_view = strip_skeleton_lines(file_text)

        file_lines = file_text.splitlines(keepends=True)
        anchor = _find_anchor_line(file_lines, stripped_view, slot_spec)
        if anchor is None:
            _log_surface(slot_spec, "Steering surface skipped: no anchor line found.")
            return {}
        anchor_idx, anchor_indent = anchor
        anchor_end_idx = _find_zone_e_anchor_end(file_lines, anchor_idx, anchor_indent)

        # Promotion detection: merge spec_text / enclosing-function source
        # signals with a file-grounded check on the contiguous `#>` block.
        promoted = detect_promoted_keys(slot_spec)
        promoted.update(_detect_promoted_keys_from_file(file_lines, anchor_idx, anchor_indent))
        promoted_set = {k for k, v in promoted.items() if v or k in promoted}

        # Parse existing zone blocks (indices of `#<` lines to replace) and
        # detect user edits before rewriting.
        existing_p_idxs, zone_p_insert_idx = _parse_zone_p(
            file_lines, anchor_idx, anchor_indent
        )
        existing_e_idxs, zone_e_insert_idx = _parse_zone_e(
            file_lines, anchor_end_idx, anchor_indent
        )
        existing_lines_for_overrides = [
            file_lines[i] for i in (existing_p_idxs + existing_e_idxs)
        ]
        user_overrides = _detect_user_overrides(existing_lines_for_overrides, steering)

        # Render the new zone blocks, skipping any promoted key.
        new_p = _zone_p_lines(steering, anchor_indent, promoted_set)
        new_e = _zone_e_lines(steering, anchor_indent, promoted_set)

        existing_p_lines = [file_lines[i] for i in existing_p_idxs]
        existing_e_lines = [file_lines[i] for i in existing_e_idxs]

        # Detect identity rewrite: both zones identical to disk.
        if existing_p_lines == new_p and existing_e_lines == new_e:
            _log_surface(slot_spec, "Steering surface already up to date.")
            return {**promoted, **user_overrides}

        # Splice in two passes: Zone E first (higher indices) so Zone P indices
        # remain valid.  We remove each stale `#<` line individually (preserving
        # the `#>` contract lines that may sit between them) and insert the new
        # block at the first removed index, or at the top of the band if none
        # existed.
        remove_idxs = set(existing_p_idxs) | set(existing_e_idxs)

        # Strip legacy BDD-style and old-vocabulary lines that never appear in
        # current output: `#< [Task] ...` (V1 BDD) and renamed keys from V2
        # (`goal`, `commits`, `because`) that were replaced in V3.
        _LEGACY_LINE = re.compile(
            r"^\s*#<\s*(?:"
            r"\[(?:Task|Given|Then|When|And|But|Verify)\]"
            r"|(?:goal|commits|because|alt)\s*:"
            r")"
        )
        for _i, _line in enumerate(file_lines):
            if _LEGACY_LINE.match(_line):
                remove_idxs.add(_i)

        p_insert = existing_p_idxs[0] if existing_p_idxs else zone_p_insert_idx
        e_insert = existing_e_idxs[0] if existing_e_idxs else zone_e_insert_idx

        new_file_lines: list[str] = []
        inserted_p = False
        inserted_e = False
        for idx, line in enumerate(file_lines):
            if idx == p_insert and not inserted_p:
                new_file_lines.extend(new_p)
                inserted_p = True
            if idx == e_insert and not inserted_e:
                new_file_lines.extend(new_e)
                inserted_e = True
            if idx in remove_idxs:
                continue
            new_file_lines.append(line)
        # Handle insert points past EOF (rare; only when anchor is the last
        # line of the file and Zone E would be empty on disk).
        if not inserted_p:
            new_file_lines.extend(new_p)
        if not inserted_e:
            new_file_lines.extend(new_e)

        new_text = "".join(new_file_lines)
        if new_text != file_text:
            _atomic_write_text(target, new_text)
            _log_surface(slot_spec, "Steering surface written.")
        else:
            _log_surface(slot_spec, "Steering surface no-op (identical content).")

    # Combined mapping: promoted (from #>) + user-edited (from #<)
    combined: dict[str, str] = {}
    combined.update(promoted)
    combined.update(user_overrides)
    return combined
