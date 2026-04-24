"""Unit tests for `semipy rewind-spec` and the source_snapshot round-trip."""
from __future__ import annotations

import pytest

from semipy.cli import cmd_rewind_spec
from semipy.history.version_control import (
    Slot,
    add_commit_to_slot,
    create_commit,
    freeze_constants,
)
from semipy.store import load_portal, save_portal


def _make_commit(source_text: str, spec_text: str, region_text: str, *, start_line: int, end_line: int, source_file: str, parent_ids=()):
    return create_commit(
        parent_ids=parent_ids,
        generated_source=source_text,
        template_fingerprint="fp",
        constants_snapshot=freeze_constants({}),
        prompt_snapshot=spec_text,
        decision="GENERATE",
        usage_id="u",
        source_snapshot={
            "slot_region_text": region_text,
            "slot_region_start_line": start_line,
            "slot_region_end_line": end_line,
            "source_file": source_file,
        },
    )


def test_rewind_spec_round_trip(tmp_path):
    """Two commits with different snapshots: rewinding between them restores the exact region text."""
    cache_dir = tmp_path / ".semiformal"
    cache_dir.mkdir()
    src_path = tmp_path / "user.py"
    version_a = """from semipy import semiformal

@semiformal
def classify(x: str) -> str:
    #> classify x into a family name
    family = ...
    return family
""".strip() + "\n"
    # Region B: just zone lines (#> + #<), the anchor `family = ...` stays in place.
    version_b_region_lines = [
        "    #> classify x into a family name (refined)",
        "    #< alt: regex | dict | fuzzy",
    ]

    src_path.write_text(version_a, encoding="utf-8")

    portal = load_portal(cache_dir, "s", str(src_path), "m")
    slot = Slot(
        slot_id="slot_a",
        call_site_info={},
        function_name_base="classify",
        slot_spec={"source_span": [str(src_path), 5, 5]},
    )
    # Commit A snapshots the initial user.py region (single #> line).
    region_a = "    #> classify x into a family name"
    commit_a = _make_commit(
        "def fn(x): return 'a'\n",
        "classify x into a family name",
        region_a,
        start_line=5,
        end_line=5,
        source_file=str(src_path),
    )
    add_commit_to_slot(slot, commit_a, "main", "u")
    region_b = "\n".join(version_b_region_lines)
    commit_b = _make_commit(
        "def fn(x): return 'b'\n",
        "classify x into a family name (refined)",
        region_b,
        start_line=5,
        end_line=6,
        source_file=str(src_path),
        parent_ids=(commit_a.commit_id,),
    )
    add_commit_to_slot(slot, commit_b, "main", "u")
    portal.slots[slot.slot_id] = slot
    save_portal(cache_dir, portal)

    portal_path = cache_dir / "s.portal.json"

    # Rewind to commit B: file should now contain version_b_region_lines.
    cmd_rewind_spec(portal_path, slot.slot_id, commit_b.commit_id)
    after_b = src_path.read_text(encoding="utf-8")
    for line in version_b_region_lines:
        assert line in after_b, f"missing line after rewind to B: {line!r}"
    assert "def classify(x: str) -> str:" in after_b
    # Anchor line remains in the file (unchanged by rewind, only zones rewritten).
    assert "    family = ..." in after_b
    assert after_b.count("    family = ...") == 1

    # Rewind back to commit A: file should contain only the original region.
    cmd_rewind_spec(portal_path, slot.slot_id, commit_a.commit_id)
    after_a = src_path.read_text(encoding="utf-8")
    assert "#> classify x into a family name (refined)" not in after_a
    assert "#< alt: regex | dict | fuzzy" not in after_a
    assert "#> classify x into a family name" in after_a

    # Idempotency: rewinding to A twice produces identical file content.
    cmd_rewind_spec(portal_path, slot.slot_id, commit_a.commit_id)
    after_a2 = src_path.read_text(encoding="utf-8")
    assert after_a == after_a2


def test_rewind_spec_legacy_commit_errors(tmp_path):
    """Commits without source_snapshot must not touch the source file and must exit non-zero."""
    cache_dir = tmp_path / ".semiformal"
    cache_dir.mkdir()
    src_path = tmp_path / "user.py"
    original_text = "#> spec\nfamily = ...\n"
    src_path.write_text(original_text, encoding="utf-8")

    portal = load_portal(cache_dir, "legacy", str(src_path), "m")
    slot = Slot(
        slot_id="slot_legacy",
        call_site_info={},
        function_name_base="fn",
        slot_spec={"source_span": [str(src_path), 1, 1]},
    )
    legacy_commit = create_commit(
        parent_ids=(),
        generated_source="def fn(): return 1\n",
        template_fingerprint="fp",
        constants_snapshot=freeze_constants({}),
        prompt_snapshot="spec",
        decision="GENERATE",
        usage_id="u",
        # no source_snapshot
    )
    add_commit_to_slot(slot, legacy_commit, "main", "u")
    portal.slots[slot.slot_id] = slot
    save_portal(cache_dir, portal)
    portal_path = cache_dir / "legacy.portal.json"

    with pytest.raises(SystemExit) as exc:
        cmd_rewind_spec(portal_path, slot.slot_id, legacy_commit.commit_id)
    assert exc.value.code != 0
    assert src_path.read_text(encoding="utf-8") == original_text
