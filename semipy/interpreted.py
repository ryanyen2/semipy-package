"""Interpret-until-shape-stable: per-row LLM interpretation + promotion to code.

The gap surfaced by the E1-E5 stress tests: a `@semiformal` slot residualizes its
meaning to LLM-free code on the first call, which is wrong for an irreducibly-
semantic operator (summarize, judge) -- it freezes a non-semantic stub. DocETL /
Palimpzest take the opposite stance: keep the model in the hot path on every row,
forever.

This module implements the position neither occupies: an operator that *starts
interpreted* (per-row LLM, memoized) and *promotes itself to residual code* once a
synthesized function reproduces HELD-OUT examples. Shape-stable operators promote
and go LLM-free; irreducibly-semantic operators never reproduce held-out outputs
and stay interpreted. Same surface, two regimes -- the model is paid only for as
long as the meaning genuinely requires it.

The slot-integrated path lives in `slot_resolver._execute_interpreted_slot`, which
opts in via `semi(..., interpreted=True)` (and `@semiformal(interpreted=True)`),
persists examples in the portal, and promotes to a normal cached commit. The
module-level helpers here are shared by that path and by the standalone
`InterpretedOp` (handy for experiments and tests).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Optional, Sequence

from semipy.agents.config import get_config
from semipy.agents.llm_utils import _openai_responses_text
from semipy.runtime_fingerprint import compute_runtime_input_fingerprint

MAX_INTERPRETED_EXAMPLES = 80


def _strip_code_fences(text: str) -> str:
    m = re.search(r"```(?:python)?\s*(.*?)```", text, re.DOTALL)
    return (m.group(1) if m else text).strip()


def _norm(s: Any) -> str:
    return re.sub(r"\s+", " ", str(s)).strip()


def _fill_template(instruction: str, runtime_values: dict[str, Any]) -> str:
    """Substitute runtime values into an f-string-style template (best effort)."""
    body = instruction
    for k, v in runtime_values.items():
        body = body.replace("{" + str(k) + "}", str(v))
    return body


def extract_label_set(expected_type: Any) -> Optional[list[str]]:
    """Return the fixed label set if ``expected_type`` is a ``Literal[...]`` of
    strings or an ``Enum`` subclass, else None. A constrained set makes per-row
    interpretation deterministic enough that a residual can reproduce it, so
    classification slots can promote (free-form labels never stabilize)."""
    if expected_type is None:
        return None
    import typing

    if typing.get_origin(expected_type) is typing.Literal:
        vals = list(typing.get_args(expected_type))
        return [str(v) for v in vals] if vals else None
    try:
        import enum

        if isinstance(expected_type, type) and issubclass(expected_type, enum.Enum):
            return [
                str(m.value) if isinstance(m.value, str) else m.name
                for m in expected_type
            ]
    except Exception:
        pass
    return None


def _snap_to_labels(text: str, labels: Sequence[str]) -> str:
    """Map a free-text answer onto the closest allowed label (exact -> contained
    -> token overlap -> first), so the output is always one of ``labels``."""
    t = _norm(text).lower()
    lowered = {lbl.lower(): lbl for lbl in labels}
    if t in lowered:
        return lowered[t]
    for low, lbl in lowered.items():
        if low and (low in t or t in low):
            return lbl
    t_tokens = set(re.findall(r"[a-z0-9]+", t))
    best, best_score = labels[0], -1
    for lbl in labels:
        score = len(t_tokens & set(re.findall(r"[a-z0-9]+", lbl.lower())))
        if score > best_score:
            best, best_score = lbl, score
    return best


# --------------------------------------------------------------------------
# Per-row interpretation (LLM in the hot path)
# --------------------------------------------------------------------------
def interpret_call(
    instruction: str,
    runtime_values: dict[str, Any],
    *,
    expected_type: Any = None,
    output_names: Optional[Sequence[str]] = None,
    labels: Optional[Sequence[str]] = None,
    max_output_tokens: int = 500,
) -> Any:
    """Run the operation on one input via the LLM.

    - multi-output slot (``len(output_names) > 1``): returns a dict keyed by
      ``output_names`` (the dict-shaped payload statement-block scaffolds expect).
    - constrained classification (``labels`` set): returns exactly one label.
    - otherwise: returns a string, or, when ``expected_type`` is a concrete
      non-str type, the JSON answer coerced to it.
    """
    cfg = get_config()
    if not cfg.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY required for interpreted-mode execution.")
    body = _fill_template(instruction, runtime_values)
    if body == instruction and runtime_values:
        # template placeholders did not match the free-var keys; supply inputs explicitly
        inputs = "\n".join(f"{k} = {v!r}" for k, v in runtime_values.items())
        body = f"{instruction}\n\nInputs:\n{inputs}"

    names = list(output_names or [])
    multi = len(names) > 1
    if multi:
        suffix = (
            f"\n\nReturn ONLY a JSON object with exactly these keys: "
            f"{', '.join(names)}. No preamble."
        )
        raw = _openai_responses_text(
            api_key=cfg.openai_api_key, model_id=cfg.openai_model,
            prompt=f"{body}{suffix}", max_output_tokens=max_output_tokens,
        )
        try:
            import json

            payload = json.loads(_strip_code_fences(raw or "") or "{}")
            if isinstance(payload, dict):
                return {k: payload.get(k) for k in names}
        except Exception:
            pass
        return {k: "" for k in names}

    label_set = list(labels or [])
    if label_set:
        suffix = (
            "\n\nAnswer with EXACTLY one of these labels and nothing else: "
            f"{', '.join(label_set)}."
        )
        raw = _openai_responses_text(
            api_key=cfg.openai_api_key, model_id=cfg.openai_model,
            prompt=f"{body}{suffix}", max_output_tokens=max_output_tokens,
        )
        return _snap_to_labels(raw or "", label_set)

    want_json = expected_type not in (None, str, type(None))
    suffix = (
        "\n\nReturn ONLY a JSON value matching the requested type, no preamble."
        if want_json
        else "\n\nReturn ONLY the answer, with no preamble or explanation."
    )
    raw = _openai_responses_text(
        api_key=cfg.openai_api_key,
        model_id=cfg.openai_model,
        prompt=f"{body}{suffix}",
        max_output_tokens=max_output_tokens,
    )
    text = (raw or "").strip()
    if not want_json:
        return _norm(text)
    try:
        import json

        from semipy.type_adapter import type_adapter_for

        payload = json.loads(_strip_code_fences(text) or text)
        return type_adapter_for(expected_type).validate_python(payload)
    except Exception:
        return _norm(text)


# --------------------------------------------------------------------------
# Promotion: synthesize a residual + validate on held-out examples
# --------------------------------------------------------------------------
def synthesize_residual_source(
    instruction: str,
    free_variables: Sequence[str],
    examples: Sequence[tuple[Sequence[Any], Any]],
    *,
    output_names: Optional[Sequence[str]] = None,
    labels: Optional[Sequence[str]] = None,
    max_output_tokens: int = 1300,
) -> Optional[str]:
    """Ask the model to compile the operation into `def solve(<free_vars>): ...`,
    given input->output examples. Returns source or None. The function must use
    only stdlib (imports inside the body) and must NOT hardcode a lookup table.

    For multi-output statement-block slots (``len(output_names) > 1``) the function
    must return a dict keyed by ``output_names``; for constrained classification
    (``labels`` set) it must return exactly one of the labels."""
    cfg = get_config()
    if not cfg.openai_api_key:
        return None
    params = ", ".join(free_variables) or "x"
    block = "\n".join(
        f"INPUT {list(args)!r}\nOUTPUT {out!r}" for args, out in examples[:MAX_INTERPRETED_EXAMPLES]
    )
    names = list(output_names or [])
    label_set = list(labels or [])
    if len(names) > 1:
        ret_clause = f"The function MUST return a dict with exactly the keys {names}."
    elif label_set:
        ret_clause = f"The function MUST return exactly one of these labels: {label_set}."
    else:
        ret_clause = "The function returns the single output value."
    prompt = (
        "You are compiling a natural-language operation into a deterministic Python "
        "function. The operation is:\n"
        f"  {instruction}\n\n"
        "Here are input->output examples it produced (positional args match the "
        "function parameters in order):\n"
        f"{block}\n\n"
        f"Write a pure, deterministic function `def solve({params}):` that reproduces "
        f"this mapping AND generalizes the underlying rule to unseen inputs. {ret_clause} "
        "Use only the Python standard library, with any imports INSIDE the function "
        "body. Do NOT hardcode a lookup table of these specific inputs; infer the "
        "general rule. Define exactly one top-level function and no other top-level "
        "code. Return ONLY the function source."
    )
    raw = _openai_responses_text(
        api_key=cfg.openai_api_key,
        model_id=cfg.openai_model,
        prompt=prompt,
        max_output_tokens=max_output_tokens,
    )
    if not raw:
        return None
    src = _strip_code_fences(raw)
    return src if "def solve" in src else None


def _compile_solve(src: str) -> Optional[Callable[..., Any]]:
    ns: dict[str, Any] = {}
    try:
        exec(compile(src, "<residual>", "exec"), ns)  # noqa: S102 (our own synthesized code)
    except Exception:
        return None
    fn = ns.get("solve")
    return fn if callable(fn) else None


def _match(expected: Any, got: Any) -> bool:
    """Equality used for held-out validation. Multi-output (dict) compares per key;
    everything else compares whitespace-normalized string forms."""
    if isinstance(expected, dict) and isinstance(got, dict):
        if set(expected) != set(got):
            return False
        return all(_norm(expected[k]) == _norm(got.get(k)) for k in expected)
    return _norm(expected) == _norm(got)


def _compose_validation_gist(
    src: str,
    holdout: Sequence[tuple[Sequence[Any], Any]],
) -> str:
    """Build a standalone program that defines the residual, runs it on each held-out
    input, and prints a JSON verdict line for the GistExecutor to capture."""
    import json

    cases = json.dumps([list(args) for args, _ in holdout])
    return (
        src.rstrip()
        + "\n\nimport json as _json\n"
        + f"_cases = _json.loads({cases!r})\n"
        + "_out = []\n"
        + "for _a in _cases:\n"
        + "    try:\n"
        + "        _out.append({'ok': True, 'val': solve(*_a)})\n"
        + "    except Exception as _e:\n"
        + "        _out.append({'ok': False, 'err': str(_e)})\n"
        + "print('__GIST_RESULT__' + _json.dumps(_out, default=str))\n"
    )


def validate_residual(
    src: str,
    holdout: Sequence[tuple[Sequence[Any], Any]],
    *,
    timeout: int = 30,
    e2b_api_key: Optional[str] = None,
) -> tuple[bool, float]:
    """Run the residual on held-out (args -> expected) pairs it was NOT built from,
    in the sandboxed GistExecutor (subprocess or E2B). Returns
    (all_match, match_fraction). Empty holdout or execution failure => (False, 0.0)."""
    if not src or not holdout:
        return (False, 0.0)
    from semipy.agents.executor import GistExecutor

    gist = _compose_validation_gist(src, holdout)
    res = GistExecutor(timeout=timeout, e2b_api_key=e2b_api_key).execute_sync(gist)
    if not res.success or not res.result_repr:
        return (False, 0.0)
    try:
        import json

        verdicts = json.loads(res.result_repr)
    except Exception:
        return (False, 0.0)
    ok = 0
    for (_, expected), got in zip(holdout, verdicts):
        if isinstance(got, dict) and got.get("ok") and _match(expected, got.get("val")):
            ok += 1
    frac = ok / len(holdout)
    return (frac >= 1.0, frac)


def attempt_promotion(
    instruction: str,
    free_variables: Sequence[str],
    examples: Sequence[tuple[Sequence[Any], Any]],
    *,
    output_names: Optional[Sequence[str]] = None,
    labels: Optional[Sequence[str]] = None,
    timeout: int = 30,
    e2b_api_key: Optional[str] = None,
    samples: int = 2,
) -> tuple[Optional[str], float]:
    """Try to compile a residual that reproduces HELD-OUT examples. Draws up to
    ``samples`` codegen attempts (LLM is non-deterministic; one bad draw should not
    block a genuinely promotable slot) and returns the first source that passes,
    else (None, best_holdout_fraction_seen)."""
    train, holdout = split_holdout(examples)
    best = 0.0
    for _ in range(max(1, samples)):
        src = synthesize_residual_source(
            instruction, free_variables, train,
            output_names=output_names, labels=labels,
        )
        if not src:
            continue
        ok, frac = validate_residual(src, holdout, timeout=timeout, e2b_api_key=e2b_api_key)
        best = max(best, frac)
        if ok:
            return src, frac
    return None, best


def split_holdout(
    examples: Sequence[tuple[Sequence[Any], Any]],
    holdout_frac: float = 0.34,
) -> tuple[list, list]:
    """Deterministic train/holdout split (every k-th example held out)."""
    ex = list(examples)
    n_hold = max(1, int(len(ex) * holdout_frac))
    step = max(2, len(ex) // n_hold)
    holdout = [ex[i] for i in range(len(ex)) if i % step == 0][:n_hold]
    train = [e for e in ex if e not in holdout] or ex
    return train, holdout


# --------------------------------------------------------------------------
# Standalone operator (experiments / tests; not wired to the portal)
# --------------------------------------------------------------------------
@dataclass
class OpStats:
    state: str = "interpreted"
    calls: int = 0
    llm_calls: int = 0
    memo_hits: int = 0
    residual_hits: int = 0
    promotion_attempts: int = 0
    examples: int = 0
    last_holdout_match: float = 0.0

    def summary(self) -> str:
        return (f"state={self.state} calls={self.calls} llm={self.llm_calls} "
                f"memo_hits={self.memo_hits} residual_hits={self.residual_hits} "
                f"promote_attempts={self.promotion_attempts} examples={self.examples} "
                f"last_holdout_match={self.last_holdout_match:.2f}")


class InterpretedOp:
    """Standalone interpret-until-shape-stable operator over a single string input."""

    def __init__(
        self,
        instruction: str,
        *,
        param: str = "text",
        promote_after: int = 6,
        labels: Optional[Sequence[str]] = None,
        verbose: bool = True,
        name: Optional[str] = None,
    ) -> None:
        self.instruction = instruction.strip()
        self.param = param
        self.labels = list(labels) if labels else None
        self.promote_after = promote_after
        self.verbose = verbose
        self.name = name or "op"
        self._memo: dict[str, str] = {}
        self._examples: list[tuple[tuple[Any, ...], Any]] = []
        self._residual: Optional[Callable[..., Any]] = None
        self._residual_src = ""
        self._next_attempt = promote_after
        self.stats = OpStats()

    def __call__(self, value: str) -> Any:
        self.stats.calls += 1
        if self._residual is not None:
            self.stats.residual_hits += 1
            return self._residual(value)
        fp = compute_runtime_input_fingerprint({self.param: value})
        if fp in self._memo:
            self.stats.memo_hits += 1
            return self._memo[fp]
        out = interpret_call(self.instruction, {self.param: value}, labels=self.labels)
        self.stats.llm_calls += 1
        self._memo[fp] = out
        self._examples.append(((value,), out))
        self.stats.examples = len(self._examples)
        if self.stats.examples >= self._next_attempt:
            self._try_promote()
            self._next_attempt = self.stats.examples + self.promote_after
        return out

    @property
    def residual_source(self) -> str:
        return self._residual_src

    def _try_promote(self) -> None:
        self.stats.promotion_attempts += 1
        cfg = get_config()
        src, frac = attempt_promotion(
            self.instruction, [self.param], self._examples,
            labels=self.labels, timeout=cfg.gist_timeout, e2b_api_key=cfg.e2b_api_key,
        )
        self.stats.last_holdout_match = frac
        if src:
            self._residual = _compile_solve(src)
            self._residual_src = src
            self.stats.state = "promoted"
            if self.verbose:
                print(f"  [{self.name}] PROMOTED (held-out match {frac:.2f})")
        elif self.verbose:
            print(f"  [{self.name}] attempt {self.stats.promotion_attempts}: "
                  f"held-out {frac:.2f} < 1.00; staying interpreted")


def interpreted(instruction: str, **kwargs: Any) -> InterpretedOp:
    return InterpretedOp(instruction, **kwargs)
