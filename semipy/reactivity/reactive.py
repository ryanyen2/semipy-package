"""
Reactive dependency graph for cross-slot invalidation and backward validation.

Tracks dependencies between semiformal slots so that when an upstream slot
produces a new commit, downstream slots can be marked stale and regenerated
on next access.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


@dataclass(frozen=True)
class SlotRef:
    """Global slot identifier: (session_id, slot_id) across portals."""

    session_id: str
    slot_id: str

    def key(self) -> str:
        return f"{self.session_id}:{self.slot_id}"


@dataclass
class DepEdge:
    """Directed dependency edge from upstream to downstream slot."""

    upstream: SlotRef
    downstream: SlotRef
    edge_type: str = "data"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SlotStatus:
    """Per-slot reactive state: current commit, staleness, downstream requirements."""

    slot_ref: SlotRef
    current_commit_id: str = ""
    stale: bool = False
    stale_reason: str = ""
    downstream_requirements: dict[str, Any] = field(default_factory=dict)
    stale_usage_ids: set[str] = field(default_factory=set)
    # upstream_key -> producing_commit_id this slot last resolved against. Drives the
    # pull-based input-staleness check (see stale_against_inputs): a slot is stale when
    # an upstream it actually consumed now presents a different commit.
    consumed: dict[str, str] = field(default_factory=dict)


@dataclass
class DependencyGraph:
    """Full dependency graph: edges, statuses, forward/backward adjacency."""

    edges: list[DepEdge] = field(default_factory=list)
    statuses: dict[str, SlotStatus] = field(default_factory=dict)
    forward_adj: dict[str, set[str]] = field(default_factory=dict)
    backward_adj: dict[str, set[str]] = field(default_factory=dict)


def _ensure_status(graph: DependencyGraph, slot_ref: SlotRef) -> SlotStatus:
    key = slot_ref.key()
    if key not in graph.statuses:
        graph.statuses[key] = SlotStatus(slot_ref=slot_ref)
    return graph.statuses[key]


def _reachable_from(graph: DependencyGraph, start_key: str, follow_forward: bool) -> set[str]:
    adj = graph.forward_adj if follow_forward else graph.backward_adj
    seen: set[str] = set()
    q: deque[str] = deque([start_key])
    while q:
        k = q.popleft()
        if k in seen:
            continue
        seen.add(k)
        for n in adj.get(k, ()):
            if n not in seen:
                q.append(n)
    return seen


def add_dependency(
    graph: DependencyGraph,
    upstream: SlotRef,
    downstream: SlotRef,
    edge_type: str = "data",
    metadata: Optional[dict[str, Any]] = None,
) -> bool:
    """Insert directed edge upstream -> downstream with cycle detection. Returns True if added."""
    uk, dk = upstream.key(), downstream.key()
    if uk == dk:
        return False
    downstream_reachable = _reachable_from(graph, dk, follow_forward=True)
    if uk in downstream_reachable:
        return False
    edge = DepEdge(upstream=upstream, downstream=downstream, edge_type=edge_type, metadata=metadata or {})
    graph.edges.append(edge)
    graph.forward_adj.setdefault(uk, set()).add(dk)
    graph.backward_adj.setdefault(dk, set()).add(uk)
    _ensure_status(graph, upstream)
    _ensure_status(graph, downstream)
    return True


def _remove_edge_keys(graph: DependencyGraph, uk: str, dk: str) -> bool:
    before = len(graph.edges)
    graph.edges = [
        e for e in graph.edges
        if not (e.upstream.key() == uk and e.downstream.key() == dk)
    ]
    if uk in graph.forward_adj:
        graph.forward_adj[uk].discard(dk)
    if dk in graph.backward_adj:
        graph.backward_adj[dk].discard(uk)
    return len(graph.edges) < before


def remove_dependency(graph: DependencyGraph, upstream: SlotRef, downstream: SlotRef) -> bool:
    """Remove the upstream -> downstream edge if present. Returns True if removed."""
    return _remove_edge_keys(graph, upstream.key(), downstream.key())


def set_incoming_edges(graph: DependencyGraph, downstream: SlotRef, upstreams: Any) -> None:
    """Make ``downstream``'s incoming edges exactly the observed ``upstreams``: prune
    edges from producers no longer feeding this slot, add newly observed ones. This
    keeps the graph an accurate record of the *current* data flow, so a dependency
    removed in user code stops triggering invalidation (no ghost edges)."""
    dk = downstream.key()
    want = {u.key() for u in upstreams}
    for uk in list(graph.backward_adj.get(dk, set())):
        if uk not in want:
            _remove_edge_keys(graph, uk, dk)
    for u in upstreams:
        add_dependency(graph, upstream=u, downstream=downstream)


def record_consumed(graph: DependencyGraph, downstream: SlotRef, observed: dict[str, str]) -> None:
    """Record the producing commit of each upstream ``downstream`` resolved against
    this call, so a later call can detect whether a consumed upstream changed."""
    st = _ensure_status(graph, downstream)
    st.consumed = dict(observed)


def stale_against_inputs(graph: DependencyGraph, downstream: SlotRef, observed: dict[str, str]) -> bool:
    """Pull-based staleness: True if an upstream this slot *previously consumed* now
    presents a different producing commit. Only currently-observed upstreams are
    compared, so a dropped dependency never triggers staleness (fixes
    over-invalidation), and a mutual dependency is detected via the input's commit id
    without needing a graph cycle (fixes under-invalidation), and it settles once the
    slot regenerates against the new commit (no churn)."""
    st = graph.statuses.get(downstream.key())
    if st is None:
        return False
    consumed = getattr(st, "consumed", None) or {}
    return any(uk in consumed and observed.get(uk) != consumed.get(uk) for uk in observed)


def get_transitive_downstream(graph: DependencyGraph, slot_ref: SlotRef) -> set[str]:
    keys = _reachable_from(graph, slot_ref.key(), follow_forward=True)
    keys.discard(slot_ref.key())
    return keys


def get_transitive_upstream(graph: DependencyGraph, slot_ref: SlotRef) -> set[str]:
    keys = _reachable_from(graph, slot_ref.key(), follow_forward=False)
    keys.discard(slot_ref.key())
    return keys


def mark_downstream_stale(
    graph: DependencyGraph,
    upstream: SlotRef,
    reason: str,
    affected_usage_ids: Optional[set[str]] = None,
) -> int:
    keys = get_transitive_downstream(graph, upstream)
    for k in keys:
        st = graph.statuses.get(k)
        if st is not None:
            st.stale = True
            st.stale_reason = reason
            if affected_usage_ids is not None:
                st.stale_usage_ids = st.stale_usage_ids | affected_usage_ids
    return len(keys)


def is_stale(graph: DependencyGraph, slot_ref: SlotRef, usage_id: Optional[str] = None) -> bool:
    st = graph.statuses.get(slot_ref.key())
    if st is None:
        return False
    if usage_id is not None and st.stale_usage_ids:
        return usage_id in st.stale_usage_ids
    return st.stale


def clear_stale(
    graph: DependencyGraph,
    slot_ref: SlotRef,
    usage_id: Optional[str] = None,
) -> None:
    st = graph.statuses.get(slot_ref.key())
    if st is not None:
        if usage_id is not None and st.stale_usage_ids:
            st.stale_usage_ids.discard(usage_id)
            if not st.stale_usage_ids:
                st.stale = False
                st.stale_reason = ""
        else:
            st.stale = False
            st.stale_reason = ""
            st.stale_usage_ids.clear()


def add_downstream_requirement(
    graph: DependencyGraph,
    slot_ref: SlotRef,
    key: str,
    value: Any,
) -> None:
    st = _ensure_status(graph, slot_ref)
    st.downstream_requirements[key] = value


def get_downstream_requirements(graph: DependencyGraph, slot_ref: SlotRef) -> dict[str, Any]:
    st = graph.statuses.get(slot_ref.key())
    if st is None:
        return {}
    return dict(st.downstream_requirements)


def update_slot_commit(
    graph: DependencyGraph,
    slot_ref: SlotRef,
    commit_id: str,
    *,
    stale_reason_template: str = "upstream commit changed",
) -> str:
    """Record ``commit_id`` for ``slot_ref``; when it differs from the prior value,
    mark all transitive downstream slots stale and return the previous commit id.

    Returns the prior ``current_commit_id`` value (empty string when unseen).
    The propagation happens here so every call site that creates or resolves a
    commit automatically triggers reactive invalidation without duplicating
    logic across GENERATE/ADAPT/INSTANTIATE/REUSE paths.
    """
    st = _ensure_status(graph, slot_ref)
    prior = st.current_commit_id or ""
    st.current_commit_id = commit_id
    if prior and commit_id and prior != commit_id:
        mark_downstream_stale(
            graph,
            slot_ref,
            f"{stale_reason_template} ({prior[:8]}->{commit_id[:8]})",
        )
    return prior


DEPENDENCY_GRAPH_FILENAME = "dependency_graph.json"


def _graph_to_serializable(graph: DependencyGraph) -> dict[str, Any]:
    edges_data = [
        {
            "upstream": {"session_id": e.upstream.session_id, "slot_id": e.upstream.slot_id},
            "downstream": {"session_id": e.downstream.session_id, "slot_id": e.downstream.slot_id},
            "edge_type": e.edge_type,
            "metadata": e.metadata,
        }
        for e in graph.edges
    ]
    statuses_data = {
        k: {
            "slot_ref": {"session_id": st.slot_ref.session_id, "slot_id": st.slot_ref.slot_id},
            "current_commit_id": st.current_commit_id,
            "stale": st.stale,
            "stale_reason": st.stale_reason,
            "downstream_requirements": st.downstream_requirements,
            "stale_usage_ids": list(getattr(st, "stale_usage_ids", set()) or set()),
            "consumed": dict(getattr(st, "consumed", {}) or {}),
        }
        for k, st in graph.statuses.items()
    }
    return {
        "edges": edges_data,
        "statuses": statuses_data,
        "forward_adj": {k: list(v) for k, v in graph.forward_adj.items()},
        "backward_adj": {k: list(v) for k, v in graph.backward_adj.items()},
    }


def _graph_from_serializable(data: dict[str, Any]) -> DependencyGraph:
    graph = DependencyGraph()
    for e in data.get("edges", []):
        up = e.get("upstream", {})
        down = e.get("downstream", {})
        upstream = SlotRef(session_id=up.get("session_id", ""), slot_id=up.get("slot_id", ""))
        downstream = SlotRef(session_id=down.get("session_id", ""), slot_id=down.get("slot_id", ""))
        graph.edges.append(
            DepEdge(upstream=upstream, downstream=downstream, edge_type=e.get("edge_type", "data"), metadata=e.get("metadata") or {})
        )
        graph.forward_adj.setdefault(upstream.key(), set()).add(downstream.key())
        graph.backward_adj.setdefault(downstream.key(), set()).add(upstream.key())
    for k, st_data in data.get("statuses", {}).items():
        ref_data = st_data.get("slot_ref", {})
        slot_ref = SlotRef(
            session_id=ref_data.get("session_id", ""),
            slot_id=ref_data.get("slot_id", ""),
        )
        graph.statuses[k] = SlotStatus(
            slot_ref=slot_ref,
            current_commit_id=st_data.get("current_commit_id", ""),
            stale=st_data.get("stale", False),
            stale_reason=st_data.get("stale_reason", ""),
            downstream_requirements=dict(st_data.get("downstream_requirements", {})),
            stale_usage_ids=set(st_data.get("stale_usage_ids", [])),
            consumed=dict(st_data.get("consumed", {})),
        )
    graph.forward_adj.update({k: set(v) for k, v in data.get("forward_adj", {}).items()})
    graph.backward_adj.update({k: set(v) for k, v in data.get("backward_adj", {}).items()})
    return graph


_dep_graph_cache: dict[str, DependencyGraph] = {}


def load_dependency_graph(cache_dir: Path) -> DependencyGraph:
    path = cache_dir / DEPENDENCY_GRAPH_FILENAME
    if not path.exists():
        return DependencyGraph()
    try:
        import json
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return _graph_from_serializable(data)
    except Exception:
        return DependencyGraph()


def save_dependency_graph(cache_dir: Path, graph: DependencyGraph) -> None:
    import json
    path = cache_dir / DEPENDENCY_GRAPH_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(_graph_to_serializable(graph), f, indent=2)


def _get_dep_graph(cache_dir: Path) -> DependencyGraph:
    key = str(cache_dir.resolve())
    if key not in _dep_graph_cache:
        _dep_graph_cache[key] = load_dependency_graph(cache_dir)
    return _dep_graph_cache[key]
