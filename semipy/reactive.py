"""
Reactive dependency graph for cross-slot invalidation and backward validation.

Tracks dependencies between semiformal slots so that when an upstream slot
produces a new commit, downstream slots can be marked stale and regenerated
on next access. No hardcoded patterns; logic is driven by data flow and
requirements.
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
    """BFS from start_key along forward edges (downstream) or backward edges (upstream)."""
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
    """
    Insert a directed edge upstream -> downstream with cycle detection.
    Returns True if edge was added, False if it would create a cycle (edge rejected).
    """
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


def get_transitive_downstream(graph: DependencyGraph, slot_ref: SlotRef) -> set[str]:
    """BFS forward from slot_ref; returns set of downstream slot keys (excluding slot_ref)."""
    keys = _reachable_from(graph, slot_ref.key(), follow_forward=True)
    keys.discard(slot_ref.key())
    return keys


def get_transitive_upstream(graph: DependencyGraph, slot_ref: SlotRef) -> set[str]:
    """BFS backward from slot_ref; returns set of upstream slot keys (excluding slot_ref)."""
    keys = _reachable_from(graph, slot_ref.key(), follow_forward=False)
    keys.discard(slot_ref.key())
    return keys


def mark_downstream_stale(
    graph: DependencyGraph,
    upstream: SlotRef,
    reason: str,
) -> int:
    """
    Mark all transitive downstream slots as stale (excluding upstream).
    Returns count of slots marked.
    """
    keys = get_transitive_downstream(graph, upstream)
    for k in keys:
        st = graph.statuses.get(k)
        if st is not None:
            st.stale = True
            st.stale_reason = reason
    return len(keys)


def is_stale(graph: DependencyGraph, slot_ref: SlotRef) -> bool:
    st = graph.statuses.get(slot_ref.key())
    return st.stale if st is not None else False


def clear_stale(graph: DependencyGraph, slot_ref: SlotRef) -> None:
    st = graph.statuses.get(slot_ref.key())
    if st is not None:
        st.stale = False
        st.stale_reason = ""


def add_downstream_requirement(
    graph: DependencyGraph,
    slot_ref: SlotRef,
    key: str,
    value: Any,
) -> None:
    """Register what downstream needs from this slot (e.g. required_columns)."""
    st = _ensure_status(graph, slot_ref)
    st.downstream_requirements[key] = value


def get_downstream_requirements(graph: DependencyGraph, slot_ref: SlotRef) -> dict[str, Any]:
    st = graph.statuses.get(slot_ref.key())
    if st is None:
        return {}
    return dict(st.downstream_requirements)


def update_slot_commit(graph: DependencyGraph, slot_ref: SlotRef, commit_id: str) -> None:
    st = _ensure_status(graph, slot_ref)
    st.current_commit_id = commit_id


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

_dep_graph_cache: dict[str, DependencyGraph] = {}

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
    statuses_data = {}
    for k, st in graph.statuses.items():
        statuses_data[k] = {
            "slot_ref": {"session_id": st.slot_ref.session_id, "slot_id": st.slot_ref.slot_id},
            "current_commit_id": st.current_commit_id,
            "stale": st.stale,
            "stale_reason": st.stale_reason,
            "downstream_requirements": st.downstream_requirements,
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
        )
    graph.forward_adj.update({k: set(v) for k, v in data.get("forward_adj", {}).items()})
    graph.backward_adj.update({k: set(v) for k, v in data.get("backward_adj", {}).items()})
    return graph


def load_dependency_graph(cache_dir: Path) -> DependencyGraph:
    """Load graph from cache_dir/.semiformal/dependency_graph.json or return empty graph."""
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
    """Serialize graph to cache_dir/dependency_graph.json."""
    import json
    path = cache_dir / DEPENDENCY_GRAPH_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _graph_to_serializable(graph)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _get_dep_graph(cache_dir: Path) -> DependencyGraph:
    """Cached loader keyed by cache_dir to avoid repeated I/O."""
    key = str(cache_dir.resolve())
    if key not in _dep_graph_cache:
        _dep_graph_cache[key] = load_dependency_graph(cache_dir)
    return _dep_graph_cache[key]
