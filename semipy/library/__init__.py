"""Sketch library: learn parametric NL->code patterns and INSTANTIATE them.

After a GENERATE/ADAPT, binding extraction (``binding.py``) records a
``CodeSketch`` keyed by a semantic template; a later slot whose spec matches
the template (``sketch.find_sketch_match``) can be satisfied by substitution
instead of a fresh LLM generation.

Submodules are imported directly (``semipy.library.sketch`` /
``.sketch_store`` / ``.binding``); this package exposes no eager symbols.
"""
from __future__ import annotations
