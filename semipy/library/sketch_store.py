"""Persistence for SketchLibrary (parallel to portal JSON)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from semipy.library.binding import SemanticBinding, SpecPhrase
from semipy.library.sketch import CodeSketch, SketchLibrary, SketchParam


SKETCH_LIBRARY_FILENAME = "sketch_library.json"


def _phrase_to_dict(p: SpecPhrase) -> dict[str, Any]:
    return {
        "text": p.text,
        "role": p.role,
        "code_referent": p.code_referent,
        "hole_name": p.hole_name,
        "safe_swap_set": list(p.safe_swap_set) if p.safe_swap_set else None,
    }


def _phrase_from_dict(d: dict[str, Any]) -> SpecPhrase:
    swaps = d.get("safe_swap_set")
    st: tuple[str, ...] | None = None
    if isinstance(swaps, list):
        st = tuple(str(x) for x in swaps)
    hn = d.get("hole_name")
    return SpecPhrase(
        text=str(d.get("text", "")),
        role=str(d.get("role", "param")),
        code_referent=str(d.get("code_referent", "")),
        hole_name=str(hn) if hn else None,
        safe_swap_set=st,
    )


def _binding_to_dict(b: SemanticBinding) -> dict[str, Any]:
    return {
        "binding_id": b.binding_id,
        "spec_text": b.spec_text,
        "phrases": [_phrase_to_dict(p) for p in b.phrases],
        "structural_signature": b.structural_signature,
        "hole_names": list(b.hole_names),
        "hole_values": dict(b.hole_values),
        "hole_code_referents": dict(b.hole_code_referents),
    }


def _binding_from_dict(d: dict[str, Any]) -> SemanticBinding:
    phrases = tuple(_phrase_from_dict(x) for x in d.get("phrases", []) if isinstance(x, dict))
    return SemanticBinding(
        binding_id=str(d.get("binding_id", "")),
        spec_text=str(d.get("spec_text", "")),
        phrases=phrases,
        structural_signature=str(d.get("structural_signature", "")),
        hole_names=tuple(str(x) for x in d.get("hole_names", [])),
        hole_values=dict(d.get("hole_values", {})),
        hole_code_referents=dict(d.get("hole_code_referents", {})),
    )


def _param_to_dict(p: SketchParam) -> dict[str, Any]:
    return {
        "hole_name": p.hole_name,
        "spec_role": p.spec_role,
        "safe_swap_set": list(p.safe_swap_set) if p.safe_swap_set else None,
    }


def _param_from_dict(d: dict[str, Any]) -> SketchParam:
    swaps = d.get("safe_swap_set")
    st: tuple[str, ...] | None = None
    if isinstance(swaps, list):
        st = tuple(str(x) for x in swaps)
    return SketchParam(
        hole_name=str(d.get("hole_name", "")),
        spec_role=str(d.get("spec_role", "param")),
        safe_swap_set=st,
    )


def _sketch_to_dict(sk: CodeSketch) -> dict[str, Any]:
    return {
        "sketch_id": sk.sketch_id,
        "structural_signature": sk.structural_signature,
        "spec_template": sk.spec_template,
        "code_template": sk.code_template,
        "params": [_param_to_dict(p) for p in sk.params],
        "source_commit_ids": list(sk.source_commit_ids),
        "hole_values_original": dict(sk.hole_values_original),
        "hole_code_referents": dict(sk.hole_code_referents),
        "instantiation_count": sk.instantiation_count,
        "validated": sk.validated,
        "expected_category": sk.expected_category,
        "free_variable_names": list(sk.free_variable_names),
        "binding_id": sk.binding_id,
    }


def _sketch_from_dict(d: dict[str, Any]) -> CodeSketch:
    params = tuple(_param_from_dict(x) for x in d.get("params", []) if isinstance(x, dict))
    return CodeSketch(
        sketch_id=str(d.get("sketch_id", "")),
        structural_signature=str(d.get("structural_signature", "")),
        spec_template=str(d.get("spec_template", "")),
        code_template=str(d.get("code_template", "")),
        params=params,
        source_commit_ids=list(d.get("source_commit_ids", [])),
        hole_values_original=dict(d.get("hole_values_original", {})),
        hole_code_referents=dict(d.get("hole_code_referents", {})),
        instantiation_count=int(d.get("instantiation_count", 0)),
        validated=bool(d.get("validated", False)),
        expected_category=str(d.get("expected_category", "")),
        free_variable_names=tuple(str(x) for x in d.get("free_variable_names", [])),
        binding_id=str(d.get("binding_id", "")),
    )


def sketch_library_path(cache_dir: Path) -> Path:
    return cache_dir / SKETCH_LIBRARY_FILENAME


def load_sketch_library(cache_dir: Path) -> SketchLibrary:
    path = sketch_library_path(cache_dir)
    if not path.exists():
        return SketchLibrary()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return SketchLibrary()
    if not isinstance(data, dict):
        return SketchLibrary()
    sketches: dict[str, CodeSketch] = {}
    for sid, sd in data.get("sketches", {}).items():
        if isinstance(sd, dict):
            sketches[str(sid)] = _sketch_from_dict(sd)
    bindings: dict[str, SemanticBinding] = {}
    for bid, bd in data.get("bindings", {}).items():
        if isinstance(bd, dict):
            try:
                bindings[str(bid)] = _binding_from_dict(bd)
            except Exception:
                continue
    structural_index: dict[str, list[str]] = {}
    for sig, ids in data.get("structural_index", {}).items():
        if isinstance(ids, list):
            structural_index[str(sig)] = [str(x) for x in ids]
    version = int(data.get("version", 0))
    lib = SketchLibrary(
        sketches=sketches,
        structural_index=structural_index,
        bindings=bindings,
        version=version,
    )
    if not lib.structural_index and lib.sketches:
        for sid, sk in lib.sketches.items():
            lib.structural_index.setdefault(sk.structural_signature, []).append(sid)
    return lib


def save_sketch_library(cache_dir: Path, library: SketchLibrary) -> None:
    path = sketch_library_path(cache_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    structural_index: dict[str, list[str]] = {}
    for sid, sk in library.sketches.items():
        structural_index.setdefault(sk.structural_signature, []).append(sid)
    payload = {
        "version": library.version,
        "sketches": {k: _sketch_to_dict(v) for k, v in library.sketches.items()},
        "bindings": {k: _binding_to_dict(v) for k, v in library.bindings.items()},
        "structural_index": structural_index,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
