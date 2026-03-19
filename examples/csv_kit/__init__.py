"""Malleable CSV library: NL specs as partial code.

SemiTable wraps tabular data and exposes .show(), .sort(), .select(), .where()
with semantic (natural-language) arguments. Open regions use semi(\"...\", expected_type=...)
inside @semiformal (and optional #> blocks in CovidReportBuilder) where behavior
cannot be fully prebuilt: column selection by meaning, sort order by intent, row
filters, semantic merge, and hybrid report lines.
"""
from __future__ import annotations

from csv_kit.table import CovidReportBuilder, SemiTable, open_table

__all__ = ["CovidReportBuilder", "SemiTable", "open_table"]
