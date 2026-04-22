from __future__ import annotations

import ast

import pytest

from semipy.history.version_control import Portal
from semipy.store import load_portal, save_portal, write_dispatch_module


@pytest.fixture
def minimal_portal(tmp_path):
    cache_dir = tmp_path / ".semiformal"
    cache_dir.mkdir()
    return load_portal(cache_dir, "test_session", "test.py", "test_module")


def test_load_portal_creates_new(tmp_path):
    cache_dir = tmp_path / ".semiformal"
    cache_dir.mkdir()
    portal = load_portal(cache_dir, "abc123", "myfile.py", "mymodule")
    assert portal.session_id == "abc123"
    assert portal.source_file == "myfile.py"
    assert portal.module_name == "mymodule"
    assert portal.slots == {}


def test_save_and_load_portal_round_trip(tmp_path):
    cache_dir = tmp_path / ".semiformal"
    cache_dir.mkdir()
    original = load_portal(cache_dir, "sess_rt", "src.py", "src_mod")
    save_portal(cache_dir, original)
    loaded = load_portal(cache_dir, "sess_rt", "src.py", "src_mod")
    assert loaded.session_id == original.session_id
    assert loaded.source_file == original.source_file
    assert loaded.module_name == original.module_name


def test_save_portal_creates_file(tmp_path):
    cache_dir = tmp_path / ".semiformal"
    cache_dir.mkdir()
    portal = load_portal(cache_dir, "file_check", "f.py", "f_mod")
    save_portal(cache_dir, portal)
    portal_file = cache_dir / "file_check.portal.json"
    assert portal_file.exists()


def test_write_dispatch_module_valid_python(tmp_path):
    from semipy.history.version_control import Slot, create_commit, add_commit_to_slot, freeze_constants
    cache_dir = tmp_path / ".semiformal"
    cache_dir.mkdir()
    portal = load_portal(cache_dir, "dispatch_test", "d.py", "d_mod")

    generated_source = "def _slot_fn(x):\n    return x.upper()\n"
    commit = create_commit(
        parent_ids=(),
        generated_source=generated_source,
        template_fingerprint="abc",
        constants_snapshot=freeze_constants({}),
        prompt_snapshot="",
        decision="GENERATE",
        usage_id="u1",
    )
    slot_id = "slot_0001"
    slot = Slot(slot_id=slot_id, call_site_info={}, function_name_base="extract_domain")
    add_commit_to_slot(slot, commit, "default", "u1")
    portal.slots[slot_id] = slot
    save_portal(cache_dir, portal)
    path, _ = write_dispatch_module(cache_dir, portal)
    source = path.read_text(encoding="utf-8")
    ast.parse(source)
