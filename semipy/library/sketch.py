"""Code sketches: parameterized templates from spec-to-code alignment; deterministic matching."""
from __future__ import annotations

import ast
import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

from semipy.library.binding import SemanticBinding, SpecPhrase, build_spec_template
from semipy.types import SlotSpec


def _strip_quotes(s: str) -> str:
    t = s.strip()
    if len(t) >= 2 and t[0] == t[-1] and t[0] in "\"'":
        return t[1:-1]
    return t


def tokenize_spec_text(text: str) -> list[str]:
    """Split on whitespace; keep double/single-quoted segments as single tokens."""
    text = text.strip()
    if not text:
        return []
    tokens: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        while i < n and text[i].isspace():
            i += 1
        if i >= n:
            break
        if text[i] in "\"'":
            q = text[i]
            j = i + 1
            while j < n:
                if text[j] == q and text[j - 1] != "\\":
                    j += 1
                    break
                j += 1
            tokens.append(text[i:j])
            i = j
        else:
            j = i
            while j < n and not text[j].isspace():
                j += 1
            tokens.append(text[i:j])
            i = j
    return tokens


def _template_parts(spec_template: str) -> list[tuple[str | None, str | None]]:
    """Alternating literal segments and holes: (text, None) or (None, hole_name)."""
    out: list[tuple[str | None, str | None]] = []
    pos = 0
    for m in re.finditer(r"\{(\w+)\}", spec_template):
        if m.start() > pos:
            out.append((spec_template[pos:m.start()], None))
        out.append((None, m.group(1)))
        pos = m.end()
    if pos < len(spec_template):
        out.append((spec_template[pos:], None))
    return out


def _literal_tokens_from_segment(segment: str) -> list[str]:
    seg = segment.strip()
    if not seg:
        return []
    return tokenize_spec_text(seg)


def template_token_pattern(spec_template: str) -> list[tuple[str | None, str | None]]:
    """Flat list of (literal_token, None) or (None, hole_name) for alignment with spec tokens."""
    parts = _template_parts(spec_template)
    flat: list[tuple[str | None, str | None]] = []
    for seg, hole in parts:
        if hole is not None:
            flat.append((None, hole))
        else:
            for t in _literal_tokens_from_segment(seg or ""):
                flat.append((t, None))
    return flat


def _norm_lit(a: str, b: str) -> bool:
    return _strip_quotes(a).casefold() == _strip_quotes(b).casefold()


def match_spec_to_sketch(
    spec_text: str,
    spec_template: str,
    params_by_hole: dict[str, SketchParam],
) -> dict[str, str] | None:
    """
    Deterministic alignment: spec tokens must match template pattern; holes capture tokens.
    Operator holes validate against safe_swap_set when present.
    """
    spec_tokens = tokenize_spec_text(spec_text)
    pattern = template_token_pattern(spec_template)
    if len(spec_tokens) != len(pattern):
        return None
    hole_values: dict[str, str] = {}
    for st, (lit, hole) in zip(spec_tokens, pattern):
        if hole is None:
            if lit is None:
                return None
            if not _norm_lit(st, lit):
                return None
        else:
            sp = params_by_hole.get(hole)
            raw = _strip_quotes(st)
            if sp is not None and sp.spec_role == "operator" and sp.safe_swap_set:
                allowed = {x.casefold() for x in sp.safe_swap_set}
                if raw.casefold() not in allowed:
                    return None
            hole_values[hole] = raw
    return hole_values


@dataclass(frozen=True)
class SketchParam:
    hole_name: str
    spec_role: str
    safe_swap_set: tuple[str, ...] | None


@dataclass
class CodeSketch:
    sketch_id: str
    structural_signature: str
    spec_template: str
    code_template: str
    params: tuple[SketchParam, ...]
    source_commit_ids: list[str]
    hole_values_original: dict[str, str]
    hole_code_referents: dict[str, str]
    instantiation_count: int = 0
    validated: bool = False
    expected_category: str = ""
    free_variable_names: tuple[str, ...] = ()
    binding_id: str = ""
    # Licensed for cross-slot matching (frontier-kernel Phase 6): set by
    # kernel.operators.license_sketch once the pattern has recurred, generalized
    # to an independently generated occurrence, and compresses. A brand-new
    # sketch starts unlicensed -- find_sketch_match will not surface it -- so a
    # pattern seen once is never promoted on an LLM's self-reported confidence
    # alone.
    licensed: bool = False


@dataclass
class SketchLibrary:
    sketches: dict[str, CodeSketch] = field(default_factory=dict)
    structural_index: dict[str, list[str]] = field(default_factory=dict)
    bindings: dict[str, Any] = field(default_factory=dict)
    version: int = 0


def compute_sketch_id(
    structural_signature: str,
    spec_template: str,
    code_template: str,
    free_variable_names: tuple[str, ...],
) -> str:
    raw = f"{structural_signature}\0{spec_template}\0{code_template}\0{','.join(free_variable_names)}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _params_from_binding(binding: SemanticBinding) -> tuple[SketchParam, ...]:
    by_hole: dict[str, SpecPhrase] = {}
    for p in binding.phrases:
        if p.hole_name:
            by_hole[p.hole_name] = p
    out: list[SketchParam] = []
    for name in binding.hole_names:
        ph = by_hole.get(name)
        role = ph.role if ph else "param"
        swaps = ph.safe_swap_set if ph else None
        out.append(SketchParam(hole_name=name, spec_role=role, safe_swap_set=swaps))
    return tuple(out)


def build_code_template(generated_source: str, binding: SemanticBinding) -> str:
    """Replace each hole code_referent with {hole_name} placeholder (first occurrence each).

    Longest referents first so short tokens (e.g. ==) do not split larger fragments first.
    """
    out = generated_source
    pairs: list[tuple[str, str]] = []
    for name in binding.hole_names:
        ref = binding.hole_code_referents.get(name, "")
        if ref:
            pairs.append((name, ref))
    pairs.sort(key=lambda t: -len(t[1]))
    for name, ref in pairs:
        token = "{" + name + "}"
        if ref in out:
            out = out.replace(ref, token, 1)
    return out


def build_code_sketch_from_commit(
    binding: SemanticBinding,
    generated_source: str,
    commit_id: str,
    expected_category: str,
    free_variable_names: tuple[str, ...],
) -> CodeSketch:
    spec_template = build_spec_template(binding.spec_text, binding)
    code_template = build_code_template(generated_source, binding)
    params = _params_from_binding(binding)
    sid = compute_sketch_id(
        binding.structural_signature,
        spec_template,
        code_template,
        free_variable_names,
    )
    return CodeSketch(
        sketch_id=sid,
        structural_signature=binding.structural_signature,
        spec_template=spec_template,
        code_template=code_template,
        params=params,
        source_commit_ids=[commit_id],
        hole_values_original=dict(binding.hole_values),
        hole_code_referents=dict(binding.hole_code_referents),
        expected_category=expected_category,
        free_variable_names=free_variable_names,
        binding_id=binding.binding_id,
    )


def _adapt_code_fragment(old_fragment: str, old_spec_val: str, new_spec_val: str) -> str:
    if old_spec_val and old_spec_val in old_fragment:
        return old_fragment.replace(old_spec_val, new_spec_val, 1)
    oq = repr(old_spec_val) if old_spec_val else ""
    nq = repr(new_spec_val) if new_spec_val else ""
    if oq and oq in old_fragment:
        return old_fragment.replace(oq, nq, 1)
    q1 = f'"{old_spec_val}"'
    q2 = f'"{new_spec_val}"'
    if q1 in old_fragment:
        return old_fragment.replace(q1, q2, 1)
    return old_fragment


def _instantiate_param_code(old_ref: str, old_v: str, new_v: str) -> str:
    """Fill df['col'] and quoted-literal referents using inner tokens from the new spec."""
    inner_new = _strip_quotes(new_v)
    inner_old = _strip_quotes(old_v)
    mdf = re.match(r"^df\[(['\"])([^'\"]*)(\1)\]$", old_ref)
    if mdf:
        q = mdf.group(1)
        return f"df[{q}{inner_new}{q}]"
    mq = re.match(r"^(['\"])([^'\"]*)(\1)$", old_ref.strip())
    if mq:
        q = mq.group(1)
        return f"{q}{inner_new}{q}"
    r = _adapt_code_fragment(old_ref, old_v, new_v)
    if r != old_ref:
        return r
    return _adapt_code_fragment(old_ref, inner_old, inner_new)


def instantiate_sketch_code(sketch: CodeSketch, new_hole_values: dict[str, str]) -> str:
    """Substitute placeholders with code fragments derived from original referents."""
    code = sketch.code_template
    for name in sketch.hole_code_referents:
        token = "{" + name + "}"
        if token not in code:
            continue
        old_ref = sketch.hole_code_referents[name]
        old_v = sketch.hole_values_original.get(name, "")
        new_v = new_hole_values.get(name, old_v)
        sp = next((p for p in sketch.params if p.hole_name == name), None)
        if sp is not None and sp.spec_role == "operator":
            new_ref = old_ref
        elif sp is not None and sp.spec_role == "param":
            new_ref = _instantiate_param_code(old_ref, old_v, new_v)
        else:
            new_ref = _adapt_code_fragment(old_ref, old_v, new_v)
            if new_ref == old_ref:
                new_ref = _adapt_code_fragment(
                    old_ref, _strip_quotes(old_v), _strip_quotes(new_v)
                )
        code = code.replace(token, new_ref, 1)
    return code


def validate_instantiated_source(source: str) -> bool:
    try:
        ast.parse(source)
        return True
    except SyntaxError:
        return False


def _sketch_matches_slot_spec(sketch: CodeSketch, slot_spec: SlotSpec) -> bool:
    if sketch.free_variable_names:
        if tuple(slot_spec.free_variables) != sketch.free_variable_names:
            return False
    if sketch.expected_category:
        if sketch.expected_category != slot_spec.expected_category.value:
            return False
    return True


def find_sketch_match(
    slot_spec: SlotSpec,
    library: SketchLibrary,
) -> tuple[CodeSketch, dict[str, str]] | None:
    """Return best matching sketch with hole values (deterministic token alignment)."""
    candidates = list(library.sketches.values())
    scored: list[tuple[int, float, CodeSketch]] = []
    for sk in candidates:
        if not sk.licensed:
            continue
        if not _sketch_matches_slot_spec(sk, slot_spec):
            continue
        params_map = {p.hole_name: p for p in sk.params}
        hv = match_spec_to_sketch(slot_spec.spec_text, sk.spec_template, params_map)
        if hv is None:
            continue
        pref = (1 if sk.validated else 0, float(sk.instantiation_count))
        scored.append((pref[0], pref[1], sk))
    if not scored:
        return None
    scored.sort(key=lambda t: (-t[0], -t[1]))
    best = scored[0][2]
    params_map = {p.hole_name: p for p in best.params}
    hv = match_spec_to_sketch(slot_spec.spec_text, best.spec_template, params_map)
    if hv is None:
        return None
    return (best, hv)


def merge_sketch_into_library(library: SketchLibrary, sketch: CodeSketch, binding: SemanticBinding) -> CodeSketch:
    """Upsert sketch and binding; merge structural index.

    Recurrence identity is the *structural* pattern (signature + spec_template),
    not the exact ``sketch_id``: two independent generations of the same
    conceptual pattern almost never produce byte-identical ``code_template``s
    (naming, formatting), so matching on ``sketch_id`` alone would leave every
    sketch permanently at recurrence 1 -- recurrence-based licensing (see
    ``kernel.operators.license_sketch``) needs occurrences to actually
    accumulate onto one sketch. An exact ``sketch_id`` match is checked first
    since it is strictly more specific (same code too, not just same template).

    Returns the sketch that now holds this occurrence's evidence -- the caller
    needs it to run licensing.
    """
    library.bindings[binding.binding_id] = binding
    existing = library.sketches.get(sketch.sketch_id)
    if existing is None:
        for sid in library.structural_index.get(sketch.structural_signature, []):
            candidate = library.sketches.get(sid)
            if candidate is not None and candidate.spec_template == sketch.spec_template:
                existing = candidate
                break
    if existing is not None:
        for cid in sketch.source_commit_ids:
            if cid not in existing.source_commit_ids:
                existing.source_commit_ids.append(cid)
        return existing
    library.sketches[sketch.sketch_id] = sketch
    library.structural_index.setdefault(sketch.structural_signature, []).append(sketch.sketch_id)
    library.version += 1
    return sketch
