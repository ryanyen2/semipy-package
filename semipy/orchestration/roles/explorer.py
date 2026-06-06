"""Code-explorer role: gather read-only facts about a slot, deterministically.

Zero LLM (an optional relevance summary can be layered later). Pulls the callee
names invoked in the enclosing function (light AST walk), the downstream shape
requirements the spec implies, and a profile of the runtime inputs. All facts are
best-effort: any extraction failure degrades to empty rather than raising, so the
explorer is safe to fan out in parallel with the version-checker (it takes no
portal write lock -- it only reads).
"""
from __future__ import annotations

import ast
from typing import Any, Optional

from semipy.agents.profiler import profile_value
from semipy.orchestration.artifacts import ExplorationResult


def _call_name(func: ast.AST) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _dependency_signatures(slot_spec: Any) -> list[str]:
    src = getattr(slot_spec, "enclosing_function_source", None) or ""
    if not src:
        return []
    try:
        tree = ast.parse(src)
    except (SyntaxError, ValueError):
        return []
    seen: set[str] = set()
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _call_name(node.func)
            if name and name not in seen:
                seen.add(name)
                out.append(name)
    return out


def _upstream_requirements(slot_spec: Any) -> list[str]:
    reqs: list[str] = []
    outs = list(getattr(slot_spec, "output_names", None) or [])
    if outs:
        reqs.append("outputs: " + ", ".join(outs))
    expected_type = getattr(slot_spec, "expected_type", None)
    if expected_type is not None:
        reqs.append(f"expected_type: {expected_type!r}")
    return reqs


def _data_profile(runtime_values: Optional[dict[str, Any]]) -> str:
    if not runtime_values:
        return ""
    parts: list[str] = []
    for key, value in list(runtime_values.items())[:10]:
        try:
            parts.append(profile_value(key, value))
        except Exception:
            continue
    return "\n".join(parts)


def explore(slot_spec: Any, runtime_values: Optional[dict[str, Any]] = None) -> ExplorationResult:
    """Gather deterministic read-only facts for a slot into an ``ExplorationResult``."""
    try:
        deps = _dependency_signatures(slot_spec)
    except Exception:
        deps = []
    try:
        upstream = _upstream_requirements(slot_spec)
    except Exception:
        upstream = []
    try:
        profile = _data_profile(runtime_values)
    except Exception:
        profile = ""
    return ExplorationResult(
        dependency_signatures=deps,
        upstream_requirements=upstream,
        data_profile=profile,
    )
