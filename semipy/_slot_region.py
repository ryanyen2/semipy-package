"""Shared helpers for reasoning about the contiguous `#>`/`#<` zone that
surrounds a slot's anchor line.

Used by:
- ``slot_resolver._capture_slot_source_snapshot`` when writing source_snapshot
  onto a commit.
- ``cli.cmd_rewind_spec`` when locating the region to overwrite in a source
  file at rewind time.
"""
from __future__ import annotations


def is_zone_line(raw: str) -> bool:
    """A line belongs to a slot's surface zone if it is blank or starts with
    ``#>`` / ``#<`` (allowing a single space after the ``#``).
    """
    t = raw.lstrip()
    if not t:
        return True
    return (
        t.startswith("#>")
        or t.startswith("#<")
        or t.startswith("# >")
        or t.startswith("# <")
    )


def expand_zone(lines: list[str], start_idx: int, end_idx: int) -> tuple[int, int]:
    """Walk up from ``start_idx`` and down from ``end_idx`` while adjacent lines
    are zone lines. Returns (region_start_idx, region_end_idx) inclusive,
    both 0-based. ``start_idx``/``end_idx`` are treated as already inside
    the region even if they are not zone lines themselves (they are the
    anchor).
    """
    n = len(lines)
    if n == 0:
        return (start_idx, end_idx)
    start_idx = max(0, start_idx)
    end_idx = min(n - 1, end_idx)
    region_start = start_idx
    i = start_idx - 1
    while i >= 0 and is_zone_line(lines[i]):
        region_start = i
        i -= 1
    region_end = end_idx
    i = end_idx + 1
    while i < n and is_zone_line(lines[i]):
        region_end = i
        i += 1
    return (region_start, region_end)
