"""Regression tests for launch-hardening fixes (multi-file, OOP, effects, concurrency)."""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from enum import Enum

from semipy.agents.executor import subprocess_env_with_user_path
from semipy.agents.generator import _collect_user_type_sources
from semipy.effects.models import Effect, EffectScript


def test_subprocess_env_propagates_import_path():
    env = subprocess_env_with_user_path()
    pp = env.get("PYTHONPATH", "")
    parts = pp.split(os.pathsep)
    # cwd must be importable in the gist subprocess (user runs from their project dir)
    assert os.getcwd() in parts
    # every parent sys.path dir that exists should be present
    for p in sys.path:
        p = p or os.getcwd()
        if os.path.isdir(p):
            assert p in parts


class _Color(str, Enum):
    RED = "red"
    BLUE = "blue"


@dataclass
class _Shape:
    color: _Color           # dependency on the enum above
    sides: int
    labels: list[str] = field(default_factory=list)


def test_collect_user_types_includes_transitive_deps_in_order():
    names = [n for n, _ in _collect_user_type_sources(_Shape)]
    # The enum a field references must be emitted, and BEFORE the type that uses it,
    # so the injected sandbox source compiles (no NameError).
    assert "_Color" in names
    assert "_Shape" in names
    assert names.index("_Color") < names.index("_Shape")


def test_collect_user_types_handles_generic_containers():
    from typing import Optional

    names = [n for n, _ in _collect_user_type_sources(Optional[list[_Shape]])]
    assert "_Shape" in names and "_Color" in names


def test_effectscript_is_sized_and_iterable():
    empty = EffectScript()
    assert len(empty) == 0 and not empty and empty.is_empty()
    one = EffectScript(effects=[Effect(op="call", target="https://x", payload={"k": 1})])
    assert len(one) == 1 and bool(one) and not one.is_empty()
    assert [e.op for e in one] == ["call"]
