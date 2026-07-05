"""The frontier kernel: the typed hardness tree and its combinator lowering.

See docs/plans/2026-07-04-001-refactor-frontier-kernel-plan.md (Part III) for the
full method. This package is built up phase by phase; Phase 1 (this module's
initial content) is the tree schema, the opaque-fallback lowering, and a general
AST-shape recognizer for the combinator core (map/filter/fold/branch/compose).
Nothing here changes how a slot actually executes yet -- that begins Phase 2.
"""
from __future__ import annotations
