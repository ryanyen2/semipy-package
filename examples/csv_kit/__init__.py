"""Malleable CSV library: NL specs as partial code.

SemiTable wraps tabular data and exposes .show(), .sort(), .select(), .where()
with semantic (natural-language) arguments. The library uses semi() and
semi.<name>() only where behavior cannot be fully prebuilt: column selection
by meaning, sort order by intent, row conditions by description, semantic
regex, formatted display, and value parsing.
"""
from __future__ import annotations

from csv_kit.table import SemiTable, open_table

__all__ = ["SemiTable", "open_table"]
