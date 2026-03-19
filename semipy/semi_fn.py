from __future__ import annotations

import hashlib
import inspect
from pathlib import Path
from typing import Any, Optional

from semipy.agents.config import get_config
from semipy.slot_resolver import execute_slot
from semipy.types import SlotCategory, SlotSpec, SemiCallSite


def _sha16(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()[:16]


def _normalize_filename(path: str) -> str:
    if not path or path == "<unknown>":
        return path
    try:
        import os

        return str(os.path.abspath(path))
    except Exception:
        return path


def _identify_call_site(depth: int = 2) -> SemiCallSite:
    """
    Identify the call site by walking back 'depth' frames.
    Intended for standalone semi() diagnostics + slot identity.
    """
    frame = inspect.currentframe()
    try:
        if frame is None:
            return SemiCallSite(filename="<unknown>", lineno=0, func_qualname="")
        f = frame
        for _ in range(depth):
            if f is None or f.f_back is None:
                break
            f = f.f_back
        if f is None:
            return SemiCallSite(filename="<unknown>", lineno=0, func_qualname="")
        filename = _normalize_filename(f.f_code.co_filename or "<unknown>")
        lineno = f.f_lineno or 0
        # If inside a method, prefer the runtime self class for a stable qualname.
        qualname = f.f_code.co_name or ""
        self_obj = f.f_locals.get("self")
        if self_obj is not None:
            qualname = f"{self_obj.__class__.__name__}.{qualname}"
        return SemiCallSite(filename=filename, lineno=lineno, func_qualname=qualname)
    finally:
        del frame


def _semi_standalone(prompt: str, *, expected_type: Any = None) -> Any:
    call_site = _identify_call_site(depth=3)
    spec_text = prompt
    start_abs = call_site.lineno
    filename = call_site.filename
    func_qualname = call_site.func_qualname

    expected = expected_type if expected_type is not None else type(None)
    spec_hash = _sha16(spec_text)
    slot_id = _sha16(f"{filename}:{func_qualname}:{start_abs}:{spec_text}")

    control_context = "method" if "." in (func_qualname or "") else "top_level"

    slot_spec = SlotSpec(
        slot_id=slot_id,
        source_span=(filename, start_abs, start_abs),
        spec_text=spec_text,
        spec_hash=spec_hash,
        free_variables=[],
        control_context=control_context,
        expected_category=SlotCategory.EXPRESSION_STANDALONE,
        expected_type=expected,
        output_names=[],
        formal_constraints=[],
        usage_hints=[],
        enclosing_function_source="",
        enclosing_function_qualname=func_qualname,
    )

    config = get_config()
    cache_dir = Path(config.cache_dir)
    return execute_slot(
        slot_spec=slot_spec,
        runtime_values={},
        source_file=filename,
        cache_dir=cache_dir,
    )


class SemiProxy:
    """
    Standalone semi() entry point.
    Inline semi(...) inside @semiformal is handled by the scaffold produced at decoration time.
    """

    def __call__(
        self,
        prompt: str,
        *,
        expected_type: Optional[Any] = None,
        require_tools: bool = False,
        **_kwargs: Any,
    ) -> Any:
        _ = require_tools  # require_tools is a future knob; standalone implementation does not branch.
        return _semi_standalone(prompt, expected_type=expected_type)


semi = SemiProxy()

