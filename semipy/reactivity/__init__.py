"""Reactive dependency graph and data-flow tracking.

``reactive`` holds the slot dependency graph (staleness, upstream/downstream);
``flow`` attaches a ``DataFlow`` to a producer's output so a downstream slot can
infer the upstream shape. Only these two modules are part of the runtime.
"""
from __future__ import annotations

from semipy.reactivity.reactive import (
    DependencyGraph,
    SlotRef,
    add_dependency,
    add_downstream_requirement,
    clear_stale,
    get_downstream_requirements,
    get_transitive_downstream,
    get_transitive_upstream,
    is_stale,
    mark_downstream_stale,
    record_consumed,
    remove_dependency,
    save_dependency_graph,
    set_incoming_edges,
    stale_against_inputs,
    update_slot_commit,
    load_dependency_graph,
    _get_dep_graph,
)
from semipy.reactivity.flow import (
    DataFlow,
    FLOW_ATTR,
    attach_producer_flow,
    create_flow,
    extract_flow,
    profile_output,
    _flow_from_inputs,
)

__all__ = [
    "DependencyGraph",
    "SlotRef",
    "DataFlow",
    "FLOW_ATTR",
    "attach_producer_flow",
    "add_dependency",
    "add_downstream_requirement",
    "clear_stale",
    "get_transitive_downstream",
    "get_transitive_upstream",
    "create_flow",
    "extract_flow",
    "get_downstream_requirements",
    "is_stale",
    "mark_downstream_stale",
    "profile_output",
    "record_consumed",
    "remove_dependency",
    "save_dependency_graph",
    "set_incoming_edges",
    "stale_against_inputs",
    "update_slot_commit",
    "load_dependency_graph",
    "_get_dep_graph",
    "_flow_from_inputs",
]
