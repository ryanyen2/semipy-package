from __future__ import annotations

import ast

import pytest

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


def test_write_dispatch_module_renders_steering_comments(tmp_path):
    """Regression: the steering block must render the real SteeringBlock fields.

    Previously write_dispatch_module read nonexistent attributes (goal/commits/
    because/alt); the AttributeError was swallowed, silently dropping every
    steering comment. Guard the actual vocabulary (intent/given/by/yields/verified).
    """
    from semipy.history.version_control import Slot, create_commit, add_commit_to_slot, freeze_constants
    from semipy.models import SteeringBlock, SteeringEntry

    cache_dir = tmp_path / ".semiformal"
    cache_dir.mkdir()
    portal = load_portal(cache_dir, "steer_test", "s.py", "s_mod")

    sb = SteeringBlock(
        intent=SteeringEntry(value="classify sentiment"),
        given=[SteeringEntry(value="text is a review")],
        by=SteeringEntry(value="keyword scan"),
        yields=SteeringEntry(value="positive|negative"),
        verified=SteeringEntry(value="3 cases hold"),
    )
    commit = create_commit(
        parent_ids=(),
        generated_source="def _slot_fn(text):\n    return 'positive'\n",
        template_fingerprint="h",
        constants_snapshot=freeze_constants({}),
        prompt_snapshot="",
        decision="GENERATE",
        usage_id="u1",
    )
    object.__setattr__(commit, "commitment_record", {"steering": sb.model_dump()})
    slot = Slot(slot_id="slot_0001", call_site_info={}, function_name_base="classify")
    add_commit_to_slot(slot, commit, "default", "u1")
    portal.slots["slot_0001"] = slot

    path, _ = write_dispatch_module(cache_dir, portal)
    source = path.read_text(encoding="utf-8")
    ast.parse(source)
    assert "# intent: classify sentiment" in source
    assert "# given: text is a review" in source
    assert "# by: keyword scan" in source
    assert "# yields: positive|negative" in source
    assert "# verified: 3 cases hold" in source
