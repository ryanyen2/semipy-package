"""
Backward compatibility: data flow is the canonical concept (see semipy.flow).

This module re-exports flow types under legacy "lineage" names. New code should
use semipy.flow and DataFlow. Observation and subscription are handled inside
semipy; no user setup required.
"""
from __future__ import annotations

from semipy.flow import (
    FLOW_ATTR as LINEAGE_ATTR,
    DataFlow as DataLineage,
    create_flow as create_lineage,
    extract_flow as extract_lineage,
    profile_output,
)

__all__ = ["DataLineage", "create_lineage", "extract_lineage", "profile_output", "LINEAGE_ATTR"]
