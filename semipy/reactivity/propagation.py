"""Topological eager propagation: Kahn sort, propagate_eager, rebuild_spec_from_commit."""
from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import Any, Optional

from semipy.reactivity.reactive import (
    DependencyGraph,
    SlotRef,
    get_transitive_downstream,
    is_stale,
)


def topological_sort(
    graph: DependencyGraph,
    node_keys: set[str],
) -> list[SlotRef]:
    """Kahn's algorithm on the subgraph induced by node_keys. Returns slot_refs in topological order."""
    in_degree: dict[str, int] = {k: 0 for k in node_keys}
    for k in node_keys:
        for upstream_k in graph.backward_adj.get(k, set()):
            if upstream_k in node_keys:
                in_degree[k] = in_degree.get(k, 0) + 1
    queue: deque[str] = deque([k for k, d in in_degree.items() if d == 0])
    result: list[str] = []
    while queue:
        k = queue.popleft()
        result.append(k)
        for downstream_k in graph.forward_adj.get(k, set()):
            if downstream_k in node_keys:
                in_degree[downstream_k] = in_degree.get(downstream_k, 0) - 1
                if in_degree[downstream_k] == 0:
                    queue.append(downstream_k)
    refs = [graph.statuses[k].slot_ref for k in result if k in graph.statuses]
    return refs


def get_stale_downstream_refs(
    graph: DependencyGraph,
    upstream_ref: SlotRef,
    max_depth: int = 5,
) -> list[SlotRef]:
    """Return downstream slot refs that are stale, in topological order, limited by max_depth."""
    downstream_keys = get_transitive_downstream(graph, upstream_ref)
    if max_depth < 0 or len(downstream_keys) > max_depth * 10:
        downstream_keys = set(list(downstream_keys)[: max_depth * 10])
    stale_keys = {
        k for k in downstream_keys
        if k in graph.statuses and is_stale(graph, graph.statuses[k].slot_ref)
    }
    if not stale_keys:
        return []
    return topological_sort(graph, stale_keys)


def rebuild_spec_from_commit(
    portal: Any,
    slot_ref: SlotRef,
    commit_id: str,
    cache_dir: Path,
) -> Optional[Any]:
    """
    Rebuild a GenerationSpec from the slot's commit context so the agent can regenerate.
    Returns None if slot/commit not found or portal state is missing. Caller must provide portal.
    """
    _ = cache_dir  # reserved for future file-context enrichment
    slot_id = slot_ref.slot_id
    slot = getattr(portal, "slots", {}).get(slot_id)
    if slot is None:
        for s in getattr(portal, "slots", {}).values():
            if getattr(s, "slot_id", None) == slot_id:
                slot = s
                break
    if slot is None:
        return None
    commit = slot.commits.get(commit_id) if getattr(slot, "commits", None) else None
    if commit is None:
        return None
    from semipy.types import Decision, GenerationSpec, SemiCallSite

    call_site_info = getattr(slot, "call_site_info", {}) or {}
    call_site = SemiCallSite(
        filename=call_site_info.get("filename", ""),
        lineno=call_site_info.get("lineno", 0),
        func_qualname=call_site_info.get("func_qualname", ""),
    )
    return GenerationSpec(
        prompt=commit.prompt_snapshot or "",
        call_site=call_site,
        expected_type=type(None),
        decision=Decision.ADAPT,
        parent_sources=[commit.generated_source],
        parent_commit_ids=[commit.commit_id],
        lineage_summary=None,
        slot_spec=None,
        scaffold_source=None,
        sibling_slot_ids=None,
        sample_input=None,
        source_file_imports=None,
        upstream_lineage=None,
        downstream_requirements=None,
        enclosing_function_source=None,
        user_source_code=None,
        session_input_observations=None,
        runtime_profile_scalar_only=False,
    )


def propagate_eager(
    graph: DependencyGraph,
    upstream_ref: SlotRef,
    portal_cache: dict[str, Any],
    cache_dir: Path,
    registry: Optional[Any] = None,
    emit_fn: Optional[Any] = None,
    max_cascade_depth: int = 5,
    impact_analysis_fn: Optional[Any] = None,
) -> list[SlotRef]:
    """
    After an upstream commit, mark downstream stale and optionally regenerate in topological order.
    If impact_analysis_fn is provided, call it (downstream_ref, upstream_old_source, upstream_new_source, downstream_source) -> bool;
    return True to skip regeneration. emit_fn(ReactiveEvent) can be used to emit SLOT_REGENERATED.
    Returns list of slot refs that were (or would be) regenerated. This implementation only returns
    the sorted stale refs; actual regeneration is left to the caller (semi_fn) to avoid circular
    imports and to keep control flow in one place.
    """
    refs = get_stale_downstream_refs(graph, upstream_ref, max_depth=max_cascade_depth)
    return refs
