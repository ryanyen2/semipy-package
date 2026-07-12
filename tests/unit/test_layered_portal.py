"""U8: layered portal -- read-only shipped baseline + local overlay (R15/R17).

The baseline is the built package data ``semipy build`` writes under
``_semiformal/`` next to a library's modules -- never written to again. The
overlay is an ordinary portal in the *consuming* project's own
``.semiformal/``, populated by the same generate/adapt pipeline a developer
uses. These tests exercise the seam that makes that layering real:

  - ``distribution.baseline.installed_baseline_version`` reads "what baseline
    is currently installed" for a slot's call site.
  - ``store._get_active_commit`` becomes baseline-aware: an overlay commit
    stamped with a baseline version that no longer matches the installed one
    is demoted to "needs revalidation" (returns ``None``, as if unadapted)
    without deleting it -- U9's floor-gated adapt does not exist yet, so this
    is simulated by hand-stamping ``commitment_record["baseline_version"]``
    on fixture commits, exactly as U9 will eventually do for real.
  - ``session_anchor.resolve_portal_anchor`` anchors a call site living
    inside an installed package to the *consuming* project (cwd), not to the
    library's own install location.
  - ``cli._why_answer`` labels a resolution ``baseline-certified`` or
    ``locally-annealed`` (R17).
"""
from __future__ import annotations

import json

from semipy.cli import cmd_why
from semipy.distribution.baseline import installed_baseline_version, is_stale_overlay_commit
from semipy.distribution.build import build_package_data
from semipy.history.version_control import Branch, Commit, Portal, Slot
from semipy.session_anchor import resolve_portal_anchor
from semipy.store import _get_active_commit, save_portal, write_dispatch_module

LIBRARY_SOURCE_V1 = "def f(xs):\n    return len(xs)\n"
LIBRARY_SOURCE_V2 = "def f(xs):\n    return len(xs) + 1\n"


# ---------------------------------------------------------------------------
# Fixtures (mirrors tests/integration/test_consumer_runtime.py's ``_build``)
# ---------------------------------------------------------------------------


def _library_commit(source: str, commit_id: str = "libc1") -> Commit:
    return Commit(
        commit_id=commit_id, parent_ids=(), generated_source=source, source_hash="h",
        template_fingerprint="t", constants_snapshot=(), operation_signature="op",
        prompt_snapshot="", timestamp=1.0, message="", decision="GENERATE",
    )


def _library_slot(spec_equivalence_key: str = "eq-1", source: str = LIBRARY_SOURCE_V1) -> Slot:
    """The library author's own slot -- what ``semipy build`` distills."""
    slot = Slot(slot_id="s1", call_site_info={}, function_name_base="f")
    slot.slot_spec = {"spec_equivalence_key": spec_equivalence_key, "spec_text": "len of xs"}
    commit = _library_commit(source)
    slot.commits[commit.commit_id] = commit
    slot.refs["head"] = commit.commit_id
    return slot


def _build_baseline(output_dir, source: str = LIBRARY_SOURCE_V1) -> str:
    """Ship a baseline at *output_dir* (a ``_semiformal/`` dir); returns its
    ``baseline_hash``."""
    portal = Portal(session_id="sess", source_file="lib.py", module_name="mod")
    portal.slots["s1"] = _library_slot(source=source)
    result = build_package_data(portal, output_dir)
    return result.manifest.baseline_hash


def _consumer_slot(mod_path) -> Slot:
    """The consumer-side ``Slot`` a call to the shipped ``f`` would populate
    in the consumer's own overlay portal: ``call_site_info["filename"]`` is
    the *shipped library's* module path (``_ensure_slot`` sets this from the
    call site, which is textually inside the library)."""
    return Slot(
        slot_id="s1",
        call_site_info={"filename": str(mod_path), "lineno": 1, "func_qualname": "f"},
        function_name_base="f",
        slot_spec={"spec_equivalence_key": "eq-1", "spec_text": "len of xs", "expected_type": "<class 'int'>"},
    )


def _stamp_overlay_commit(slot: Slot, baseline_version: str, commit_id: str = "ov1") -> Commit:
    """Attach a local overlay commit stamped with the baseline version it was
    built against -- what U9's floor-gated adapt will eventually write for
    real; hand-stamped here since U9 is out of scope."""
    commit = Commit(
        commit_id=commit_id, parent_ids=(), generated_source="def f(xs): return len(xs)", source_hash="h",
        template_fingerprint="t", constants_snapshot=(), operation_signature="op",
        prompt_snapshot="", timestamp=2.0, message="", decision="ADAPT",
        commitment_record={"baseline_version": baseline_version},
    )
    slot.commits[commit.commit_id] = commit
    slot.branches["main"] = Branch(name="main", head=commit.commit_id)
    return commit


# ---------------------------------------------------------------------------
# Scenario 1: first consumer call resolves to the shipped baseline artifact
# ---------------------------------------------------------------------------


def test_first_consumer_call_has_no_overlay_and_resolves_to_baseline(tmp_path):
    package_dir = tmp_path / "fixturepkg"
    package_dir.mkdir()
    mod_path = package_dir / "mod.py"
    mod_path.write_text("# fixture module\n", encoding="utf-8")
    baseline_version = _build_baseline(package_dir / "_semiformal")

    slot = _consumer_slot(mod_path)  # never adapted: no overlay commits at all

    assert installed_baseline_version(slot) == baseline_version
    assert _get_active_commit(slot, installed_baseline_version=baseline_version) is None


# ---------------------------------------------------------------------------
# Scenario 2: after a local adaptation, resolution prefers the overlay head
# ---------------------------------------------------------------------------


def test_local_adaptation_makes_overlay_head_win(tmp_path):
    package_dir = tmp_path / "fixturepkg"
    package_dir.mkdir()
    mod_path = package_dir / "mod.py"
    mod_path.write_text("# fixture module\n", encoding="utf-8")
    baseline_version = _build_baseline(package_dir / "_semiformal")

    slot = _consumer_slot(mod_path)
    overlay = _stamp_overlay_commit(slot, baseline_version)

    active = _get_active_commit(slot, installed_baseline_version=baseline_version)
    assert active is not None
    assert active.commit_id == overlay.commit_id


# ---------------------------------------------------------------------------
# Scenario 3: a package upgrade demotes the overlay commit to needs-revalidation
# ---------------------------------------------------------------------------


def test_package_upgrade_demotes_stale_overlay_commit(tmp_path):
    package_dir = tmp_path / "fixturepkg"
    package_dir.mkdir()
    mod_path = package_dir / "mod.py"
    mod_path.write_text("# fixture module\n", encoding="utf-8")
    baseline_v1 = _build_baseline(package_dir / "_semiformal", source=LIBRARY_SOURCE_V1)

    slot = _consumer_slot(mod_path)
    overlay = _stamp_overlay_commit(slot, baseline_v1)

    # Still fresh against v1: overlay wins.
    assert _get_active_commit(slot, installed_baseline_version=baseline_v1) is overlay

    # Upgrade the installed package: same location, new content -> new hash.
    baseline_v2 = _build_baseline(package_dir / "_semiformal", source=LIBRARY_SOURCE_V2)
    assert baseline_v2 != baseline_v1

    # The overlay commit is demoted (not deleted) -- looks unadapted again.
    assert _get_active_commit(slot, installed_baseline_version=baseline_v2) is None
    assert slot.commits[overlay.commit_id] is overlay  # never removed
    assert is_stale_overlay_commit(overlay, baseline_v2) is True
    assert is_stale_overlay_commit(overlay, baseline_v1) is False

    # write_dispatch_module (the mechanism that makes "demoted" mean
    # "re-enters generate/adapt on next call") skips the now-stale slot.
    portal = Portal(session_id="sess", source_file=str(mod_path), module_name="mod")
    portal.slots["s1"] = slot
    cache_dir = tmp_path / ".semiformal"
    cache_dir.mkdir()
    save_portal(cache_dir, portal)
    _, fn_line_map = write_dispatch_module(cache_dir, portal)
    assert "s1" not in fn_line_map


# ---------------------------------------------------------------------------
# Scenario 4: why() distinguishes baseline-certified from locally-annealed
# ---------------------------------------------------------------------------


def test_why_labels_baseline_certified_then_locally_annealed(tmp_path, capsys):
    package_dir = tmp_path / "fixturepkg"
    package_dir.mkdir()
    mod_path = package_dir / "mod.py"
    mod_path.write_text("# fixture module\n", encoding="utf-8")
    baseline_version = _build_baseline(package_dir / "_semiformal")

    cache_dir = tmp_path / ".semiformal"
    cache_dir.mkdir()

    # Before adaptation: baseline-certified.
    slot = _consumer_slot(mod_path)
    portal = Portal(session_id="sess", source_file=str(mod_path), module_name="mod")
    portal.slots["s1"] = slot
    portal_path = cache_dir / "sess.portal.json"
    save_portal(cache_dir, portal)

    cmd_why(portal_path, "s1", None, None, True)
    answer = json.loads(capsys.readouterr().out)
    assert answer["provenance"] == {"status": "baseline-certified", "baseline_version": baseline_version}

    # After a local adaptation: locally-annealed, naming the baseline it extends.
    _stamp_overlay_commit(slot, baseline_version)
    save_portal(cache_dir, portal)

    cmd_why(portal_path, "s1", None, None, True)
    answer = json.loads(capsys.readouterr().out)
    assert answer["provenance"] == {"status": "locally-annealed", "baseline_version": baseline_version}


def test_why_omits_provenance_for_an_ordinary_developer_slot(tmp_path, capsys):
    """No installed baseline at all for this call site -- pre-U8 dev-side
    ``why()`` behavior is unaffected."""
    slot = Slot(
        slot_id="s1", call_site_info={"filename": "app.py", "lineno": 1, "func_qualname": "f"},
        function_name_base="f", slot_spec={"spec_text": "len of xs", "expected_type": "<class 'int'>"},
    )
    slot.commits["c1"] = _library_commit(LIBRARY_SOURCE_V1, commit_id="c1")
    slot.branches["main"] = Branch(name="main", head="c1")
    cache_dir = tmp_path / ".semiformal"
    cache_dir.mkdir()
    portal = Portal(session_id="sess", source_file="app.py", module_name="mod")
    portal.slots["s1"] = slot
    portal_path = cache_dir / "sess.portal.json"
    save_portal(cache_dir, portal)

    cmd_why(portal_path, "s1", None, None, True)
    answer = json.loads(capsys.readouterr().out)
    assert answer["provenance"] is None


# ---------------------------------------------------------------------------
# Scenario 5: deleting the overlay restores pristine shipped behavior
# ---------------------------------------------------------------------------


def test_deleting_the_overlay_restores_pristine_baseline_behavior(tmp_path):
    package_dir = tmp_path / "fixturepkg"
    package_dir.mkdir()
    mod_path = package_dir / "mod.py"
    mod_path.write_text("# fixture module\n", encoding="utf-8")
    baseline_version = _build_baseline(package_dir / "_semiformal")

    slot = _consumer_slot(mod_path)
    _stamp_overlay_commit(slot, baseline_version)
    assert _get_active_commit(slot, installed_baseline_version=baseline_version) is not None

    # "Deleting the overlay" (e.g. rm -rf .semiformal/) means the consumer's
    # next call starts from a slot with no commits at all -- identical to the
    # pristine, never-adapted state from scenario 1.
    pristine_slot = _consumer_slot(mod_path)
    assert _get_active_commit(pristine_slot, installed_baseline_version=baseline_version) is None


# ---------------------------------------------------------------------------
# session_anchor: consumer overlays anchor to the consuming project, not the
# library's own install location (U8's trickiest seam)
# ---------------------------------------------------------------------------


def test_resolve_portal_anchor_anchors_installed_package_source_to_cwd(tmp_path):
    package_dir = tmp_path / "fixturepkg"
    package_dir.mkdir()
    mod_path = package_dir / "mod.py"
    mod_path.write_text("# fixture module\n", encoding="utf-8")
    _build_baseline(package_dir / "_semiformal")

    anchor = resolve_portal_anchor(str(mod_path))
    assert anchor == str(__import__("pathlib").Path.cwd().resolve())
    assert not anchor.startswith(str(package_dir))


def test_resolve_portal_anchor_leaves_ordinary_dev_files_untouched(tmp_path):
    dev_file = tmp_path / "app.py"
    dev_file.write_text("# dev module, no _semiformal sibling\n", encoding="utf-8")

    anchor = resolve_portal_anchor(str(dev_file))
    assert anchor == str(dev_file.resolve())
