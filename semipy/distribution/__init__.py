"""Consumer-facing package distribution (U6): ``semipy build`` distills a
portal into package data (``_semiformal/``: manifest, per-slot artifacts and
floor-filtered contracts) that ships next to a library's modules, and
``semipy.distribution.runtime`` resolves calls against it -- in-scope calls
never import LLM machinery, and no cache dir or API key is needed."""
from __future__ import annotations
