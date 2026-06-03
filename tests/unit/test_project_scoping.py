from __future__ import annotations

from pathlib import Path

from semipy.session_anchor import resolve_project
from semipy.types import module_name_for_project, session_id_for_project


def test_explicit_cache_dir_is_honored(tmp_path):
    cache = tmp_path / "proj" / ".semiformal"
    cache.mkdir(parents=True)
    src = tmp_path / "proj" / "a.py"
    src.write_text("x = 1")
    resolved_cache, project_root = resolve_project(str(src), cache)
    assert resolved_cache == cache
    assert project_root == cache.resolve().parent


def test_walk_up_finds_nearest_semiformal(tmp_path):
    root = tmp_path / "proj"
    (root / ".semiformal").mkdir(parents=True)
    (root / "src").mkdir()
    src = root / "src" / "a.py"
    src.write_text("x = 1")
    # Default cache_dir -> discovery walks up to the project .semiformal/.
    resolved_cache, project_root = resolve_project(str(src), Path(".semiformal"))
    assert resolved_cache == (root / ".semiformal").resolve()
    assert project_root == root.resolve()


def test_cwd_fallback_when_no_semiformal(tmp_path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    src = work / "a.py"
    src.write_text("x = 1")
    resolved_cache, project_root = resolve_project(str(src), Path(".semiformal"))
    assert project_root == work.resolve()
    assert resolved_cache == work.resolve() / ".semiformal"


def test_same_basename_different_dirs_share_one_project(tmp_path):
    """Regression: two same-named files under one project must NOT collide.

    Pre-fix, session_id hashed only the basename, so src/analysis.py and
    tools/analysis.py mapped to the same portal and corrupted each other.
    """
    root = tmp_path / "proj"
    (root / ".semiformal").mkdir(parents=True)
    (root / "src").mkdir()
    (root / "tools").mkdir()
    a = root / "src" / "analysis.py"
    a.write_text("x = 1")
    b = root / "tools" / "analysis.py"
    b.write_text("y = 2")

    _, root_a = resolve_project(str(a), Path(".semiformal"))
    _, root_b = resolve_project(str(b), Path(".semiformal"))
    # Same project -> same portal (cross-file reuse), not a collision.
    assert root_a == root_b
    assert session_id_for_project(root_a) == session_id_for_project(root_b)


def test_different_projects_get_different_sessions(tmp_path):
    p1 = tmp_path / "p1"
    p2 = tmp_path / "p2"
    (p1 / ".semiformal").mkdir(parents=True)
    (p2 / ".semiformal").mkdir(parents=True)
    a = p1 / "analysis.py"
    a.write_text("x = 1")
    b = p2 / "analysis.py"
    b.write_text("y = 2")
    _, root_a = resolve_project(str(a), Path(".semiformal"))
    _, root_b = resolve_project(str(b), Path(".semiformal"))
    assert session_id_for_project(root_a) != session_id_for_project(root_b)


def test_session_id_stable_across_trailing_slash_and_case(tmp_path):
    d = tmp_path / "Proj"
    d.mkdir()
    assert session_id_for_project(str(d)) == session_id_for_project(str(d) + "/")


def test_module_name_sanitization(tmp_path):
    weird = tmp_path / "my-project.v2"
    weird.mkdir()
    name = module_name_for_project(str(weird))
    assert name.isidentifier()
    assert module_name_for_project("/") == "project" or module_name_for_project("/").isidentifier()
