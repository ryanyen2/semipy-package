# The Sketch Library and the INSTANTIATE Decision

This document specifies the **sketch library** — the pattern-learning subsystem of
`semipy` that lets a later slot be satisfied by *parameter substitution into a
previously learned code sketch* with **no LLM call**. This resolution outcome is the
`Decision.INSTANTIATE` route (`semipy/types.py`, `INSTANTIATE = "instantiate"`).

The subsystem lives under `semipy/library/` (modules `sketch.py`, `binding.py`,
`sketch_store.py`) and is driven from `semipy/slot_resolver.py` and
`semipy/routing.py`. It is the only "library" subsystem in the package: there is no
abstraction-compression / sleep-phase machinery. All LLM work in this subsystem
flows through `semipy/agents/llm_utils.py:classify_with_llm`, which calls the
**OpenAI Responses API** with the `openai_model` setting (default `gpt-5.5`).


## 1. Motivation

The agentic generation pipeline (`SemiAgent.generate`) is the expensive path: a
multi-tool LLM run that drafts a Python function, validates it, and commits it.
Once that work has produced a correct implementation for a spec like

```text
extract the 'domain' from url
```

we would like to *avoid repeating it* when a structurally identical spec arrives
that differs only in a literal — for example `extract the 'path' from url`. The two
specs have the same **sentence shape** and map to the same **control flow**; only one
token (`domain` vs `path`) varies, and it varies in lockstep with one fragment of the
generated code.

The sketch library captures exactly this: after a successful **GENERATE** or
**ADAPT**, it learns a *parametric template* aligning the spec to the code, with the
varying tokens marked as holes. A later slot whose spec text **unifies** with that
template is resolved by substituting the new token into the matching code hole. The
cost model is the point:

$$
\text{cost}(\text{INSTANTIATE}) \;\approx\; \underbrace{O(|\text{tokens}|)}_{\text{token alignment}} \;+\; \underbrace{O(1)}_{\text{string substitution}} \;+\; \text{(one local verify run)} \;\ll\; \text{cost}(\text{GENERATE})
$$

INSTANTIATE performs **zero** LLM inference at resolution time. (The *learning* of a
sketch costs one LLM extraction call, amortized over every future instantiation.)


## 2. Binding extraction (after GENERATE / ADAPT)

After a slot commits a new implementation, `slot_resolver.py` calls
`_schedule_sketch_binding_extraction`, gated on `config.sketch_library_learning`
(default `True`, `semipy/agents/config.py`). The work is done in
`_run_sketch_binding_extraction`, which calls
`semipy/library/binding.py:extract_binding_async`.

### 2.1 The extraction prompt

`extract_binding_async` sends the spec text and the generated source to the model
(via `classify_with_llm` → OpenAI Responses API, `gpt-5.5`) with the prompt built by
`binding.py:_extraction_prompt`. The model is asked to decompose the spec into
**phrases**, each tagged as **structural** (defines the operation pattern, fixed) or
**parametric** (`is_hole: true` — a value that could change without changing the
code's shape). The prompt is deliberately conservative: a substring is marked
parametric only when *another reasonable spec with the same sentence shape but a
different literal at that position would map to the SAME control flow and only differ
by substitution*. Open-ended / one-off logic is to return an empty `phrases` list and
a low `confidence`.

Each phrase becomes a `SpecPhrase` (`binding.py`):

```python
@dataclass(frozen=True)
class SpecPhrase:
    text: str                       # the spec substring, e.g. "'domain'"
    role: str                       # operation | param | operator | connective
    code_referent: str              # exact Python fragment, e.g. "'domain'" or 'df["col"]'
    hole_name: str | None           # set iff this phrase is a parametric hole
    safe_swap_set: tuple[str, ...] | None   # for operator holes: equivalent NL wordings
```

`extract_binding_async` parses the model's JSON (`_parse_binding_json`, tolerant of
code fences and surrounding prose) and assembles a `SemanticBinding` via
`build_semantic_binding`. That function derives three keyed views over the holes —
`hole_values` (hole → spec text), `hole_code_referents` (hole → code fragment), and
the sorted `hole_names` — plus two content hashes:

- `compute_structural_signature(phrases)` — SHA-256 (16 hex) of the **non-hole**
  phrases (`role:normalized_text`). This is the *pattern identity*: two specs that
  share all structural anchors share a signature.
- `compute_binding_id(spec_text, phrases)` — SHA-256 (16 hex) of the full canonical
  form (spec text + every phrase's role/text/referent/hole). This is the *exact*
  identity used as the dictionary key for the binding.

### 2.2 build_spec_template

`binding.py:build_spec_template(spec_text, binding)` produces the **parametric spec
template** by replacing each hole phrase's `text` in the spec with a `{hole_name}`
placeholder. Replacements are applied **longest-text-first** so a short hole literal
cannot pre-empt a longer one that contains it, and each is a single replacement
(`.replace(..., 1)`). For our example,

```text
spec_text     = extract the 'domain' from url
hole          = {param: text="'domain'", code_referent="'domain'"}
spec_template = extract the {param} from url
```

### 2.3 The clarity gate

Not every binding is worth storing. `binding.py:evaluate_binding_clarity` is the gate
that decides whether a binding is *reusable enough*. It returns `(is_clear, reason)`
and **rejects** when any of the following hold:

| Condition | Reason a sketch would be useless |
|---|---|
| `not binding.phrases` | nothing was extracted |
| `not binding.hole_names` | no parametric hole — the pattern would memorize a one-off copy and never INSTANTIATE |
| no non-hole phrases | no structural anchor — the template would match *any* spec |
| a hole has an empty `code_referent` | the hole cannot be located in the code, so substitution is impossible |
| `0 < confidence < min_confidence` | the model itself reported low alignment confidence |

`min_confidence` defaults to `0.6` (`config.sketch_library_min_confidence`, read in
`_run_sketch_binding_extraction`). Only a binding that passes this gate is converted
to a sketch and merged into the library; otherwise the run logs
`Pattern learning: skipped (no confident reusable pattern).` and returns. The whole
extraction is wrapped so failures are swallowed (pattern learning is best-effort and
must never break a successful generation).


## 3. The CodeSketch

A passing binding is turned into a `CodeSketch` by
`sketch.py:build_code_sketch_from_commit`. The sketch is the durable, LLM-free
artifact that later resolutions consult:

```python
@dataclass
class CodeSketch:
    sketch_id: str                       # content hash (see below)
    structural_signature: str            # carried from the binding
    spec_template: str                   # "extract the {param} from url"
    code_template: str                   # generated code with {param} holes
    params: tuple[SketchParam, ...]      # per-hole role + safe_swap_set
    source_commit_ids: list[str]         # commits this sketch was learned from
    hole_values_original: dict[str, str] # hole -> original spec value
    hole_code_referents: dict[str, str]  # hole -> original code fragment
    instantiation_count: int = 0
    validated: bool = False
    expected_category: str = ""          # must match the new slot's category
    free_variable_names: tuple[str, ...] = ()  # must match the new slot's free vars
    binding_id: str = ""
```

The **code template** is built by `sketch.py:build_code_template`: for each hole, the
hole's `code_referent` is replaced (first occurrence) by `{hole_name}` in the
generated source. Replacements are again longest-referent-first so a short token (e.g.
`==`) cannot fragment a larger one. `SketchParam` records, per hole, the `spec_role`
(`param` vs `operator`) and — for operator holes — the `safe_swap_set` of NL wordings
that map to the same operator; matching uses this to reject an unsafe operator swap.

The sketch's identity, `compute_sketch_id`, is the SHA-256 (16 hex) of
`structural_signature ∥ spec_template ∥ code_template ∥ free_variable_names`, so
identical learned patterns coalesce.

### 3.1 Persistence

`sketch.py:merge_sketch_into_library` upserts the sketch and its binding into the
in-memory `SketchLibrary` (and appends to the structural index, keyed by
`structural_signature`). On a duplicate `sketch_id` it merges `source_commit_ids`
rather than overwriting.

`semipy/library/sketch_store.py` serializes the whole library to
`sketch_library.json` under `config.cache_dir` (default `.semiformal/`).
`save_sketch_library` writes the JSON (sketches, bindings, structural index,
version); `load_sketch_library` reads it back, tolerating a missing or malformed file
by returning an empty `SketchLibrary`, and rebuilds the structural index if absent.


## 4. Matching (find_sketch_match)

When a new slot has **no commit** for the incoming spec (or a stored equivalence-key
mismatch), `routing.py` consults the library. `RoutingPolicy.decide`
(`semipy/routing.py`) calls `_try_sketch_instantiation`, which calls
`sketch.py:find_sketch_match(slot_spec, library)`.

Matching is **deterministic** (no LLM). For each candidate sketch:

1. **Compatibility filter** (`_sketch_matches_slot_spec`): the sketch's
   `free_variable_names` must equal the slot's `free_variables` (when recorded), and
   the sketch's `expected_category` must equal the slot's category. A sketch learned
   for one signature/category will not be instantiated for another.

2. **Token unification** (`match_spec_to_sketch`): the new spec text is tokenized
   (`tokenize_spec_text` — whitespace split, but quoted segments stay one token), and
   the template is flattened into an alternating pattern of literal tokens and holes
   (`template_token_pattern`). The token sequences must have **equal length**; then,
   position by position:
   - a **literal** template token must match the spec token under case-folded,
     quote-stripped equality (`_norm_lit`);
   - a **hole** captures the spec token (quotes stripped). If the hole is an
     `operator` with a `safe_swap_set`, the captured value must lie in that set.

Formally, matching computes a substitution. Let the template flatten to the sequence

$$
P = \big[\, p_1, p_2, \ldots, p_m \,\big], \qquad
p_i \in \{\, \texttt{lit}(t) \;:\; \text{literal token } t \,\} \,\cup\, \{\, \texttt{hole}(h) \,\},
$$

and let the new spec tokenize to $S = [s_1, \ldots, s_n]$. A match exists iff $m = n$
and there is a substitution

$$
\sigma : \text{holes}(P) \to \text{values}, \qquad
\sigma(h_i) = \mathrm{strip}(s_i),
$$

such that for every position $i$,

$$
p_i = \texttt{lit}(t) \;\Rightarrow\; \mathrm{norm}(s_i) = \mathrm{norm}(t),
\qquad\quad
p_i = \texttt{hole}(h) \wedge \mathrm{role}(h)=\texttt{operator}
   \;\Rightarrow\; \sigma(h) \in \mathrm{safeSwap}(h).
$$

When it succeeds, `match_spec_to_sketch` returns the hole-value map $\sigma$;
otherwise `None`. `find_sketch_match` scores every matching sketch by
`(validated, instantiation_count)`, prefers a previously-validated, more-used sketch,
and returns `(best_sketch, σ)`. That tuple becomes a `ResolutionResult` with
`decision = Decision.INSTANTIATE`, carrying `sketch_id` and `sketch_hole_values = σ`.

In `RoutingPolicy.decide`, INSTANTIATE is reached at **Case 3** (no local commits, no
cross-slot donor, sketch found) and at **Case 5** (slot has commits but the
equivalence key mismatches — a new spec shape at the same call site); in both, a
failed match falls through to GENERATE / ADAPT respectively.


## 5. Instantiation

The INSTANTIATE branch of `slot_resolver.py:execute_slot` realizes the decision. It
never trusts the substituted source blindly — it validates and verifies *before* a
commit:

1. **Substitute** — `sketch.py:instantiate_sketch_code(sketch, σ)` walks each
   `{hole_name}` token in the code template and replaces it with a code fragment
   derived from the *original* referent and the new value:
   - `operator` holes keep their original referent (the safe-swap guarantee means the
     operator is unchanged);
   - `param` holes go through `_instantiate_param_code`, which understands the common
     referent shapes — `df['col']` rewrites to `df['<new>']`, a bare quoted literal
     `'old'` rewrites to `'<new>'`, otherwise it falls back to a literal string
     substitution (`_adapt_code_fragment`, trying raw, `repr`, and double-quoted
     forms, with a quote-stripped retry).

2. **Syntax-validate** — `validate_instantiated_source` (`ast.parse`). A syntax
   failure aborts instantiation.

3. **Compile + runtime-verify** — the source is compiled (`_compile_source`) and run
   through `verify_runtime_execution` over each sample from
   `_reuse_verify_sample_inputs` (type check, execution, empty/identity guards). Any
   failed sample aborts.

4. **Call + commit** — only if all verification passed does `execute_slot` invoke the
   instantiated function on the real runtime arguments (`_call_generated_fn`); a
   `SemiCallError` there also aborts. On success it mints a commit with
   `Decision.INSTANTIATE` (parented to the sketch's source commit), bumps the
   sketch's `instantiation_count`, sets `validated = True`, persists the library,
   portal, and dispatch module, and returns the result. The console logs
   `Reusing learned pattern with parameter substitution; no generation needed.`

5. **Fall through on failure** — if any of validation, verification, or the call
   fails, `instant_ok` stays `False`. `execute_slot` packages the offending template
   and instantiated source into `_sketch_context` and **re-routes via
   `RoutingPolicy.decide(..., force_regenerate=True)`**, which becomes an ADAPT (or
   GENERATE) that *sees* the failed sketch as prior context. INSTANTIATE is therefore
   a safe fast-path: a bad substitution costs a verify run, never a wrong answer.


## 6. Synchronous vs asynchronous learning

`_schedule_sketch_binding_extraction` runs the (LLM-bearing) extraction either inline
or on a daemon thread, controlled by two config flags
(`semipy/agents/config.py`):

| Flag | Default | Behavior |
|---|---|---|
| `sketch_library_learning` | `True` | master switch; `False` disables learning entirely |
| `sketch_library_learning_async` | `False` | `False` ⇒ extract **synchronously** (block until the sketch is persisted) before `execute_slot` returns; `True` ⇒ extract on a background thread |

The default (`async = False`) is deliberate: it persists the sketch **before**
`execute_slot` returns, so a *second slot in the same process* (e.g. the next notebook
cell) can immediately match it and INSTANTIATE. This is what makes the worked example
below fire in a single session. Setting `async = True` lowers the latency of the
generating call (extraction happens off the hot path), at the cost that sketches may
**lag** — an immediately-following slot may miss the just-learned pattern and
GENERATE instead. Users never import sketch APIs; learning is internal to
`execute_slot`.


## 7. Worked example

Two notebook cells. The session uses the synchronous default
(`sketch_library_learning_async = False`), so cell 1's sketch is on disk before cell
2 runs.

### Cell 1 — GENERATE, then learn a sketch

```python
url = "https://docs.example.com/guides/intro"
semi(f"extract the {'domain'} from {url}")
```

The f-string interpolates the quoted literal `'domain'` and the value of `url`. There
is no commit for this call site, no donor, and an empty library, so
`RoutingPolicy.decide` returns **GENERATE**. The agentic pipeline produces, say:

```python
def slot_fn(url):
    from urllib.parse import urlparse
    return urlparse(url).netloc
```

Wait — that ignores `'domain'`. In practice the spec literal *names which part to
extract*, so the generated code branches on it, e.g.:

```python
def slot_fn(url):
    from urllib.parse import urlparse
    parts = urlparse(url)
    return getattr(parts, 'domain' if 'domain' == 'domain' else '', '') or parts.netloc
```

After the commit, `_run_sketch_binding_extraction` calls the model. It returns a
binding marking `'domain'` parametric:

```json
{
  "confidence": 0.88,
  "clarity_reason": "only the quoted part name varies; same urlparse control flow",
  "phrases": [
    {"text": "extract the", "role": "operation", "code_referent": "urlparse", "is_hole": false},
    {"text": "'domain'", "role": "param", "code_referent": "'domain'", "is_hole": true, "hole_name": "param"},
    {"text": "from",       "role": "connective", "code_referent": "", "is_hole": false},
    {"text": "url",        "role": "param", "code_referent": "url", "is_hole": false}
  ]
}
```

`evaluate_binding_clarity` passes (one hole, structural anchors present, the hole has
a `code_referent`, confidence `0.88 ≥ 0.6`). `build_spec_template` and
`build_code_template` yield the stored sketch:

```text
spec_template:  extract the {param} from url
```

```python
# code_template (param hole shown as {param})
def slot_fn(url):
    from urllib.parse import urlparse
    parts = urlparse(url)
    return getattr(parts, {param} if {param} == {param} else '', '') or parts.netloc
```

with `hole_values_original = {"param": "'domain'"}` and
`hole_code_referents = {"param": "'domain'"}`. The sketch is merged and written to
`.semiformal/sketch_library.json` **synchronously**.

### Cell 2 — INSTANTIATE, no LLM call

```python
url = "https://docs.example.com/guides/intro"
semi(f"extract the {'path'} from {url}")
```

This call site has no commit of its own. `RoutingPolicy.decide` reaches the sketch
route and calls `find_sketch_match`. Tokenizing the new spec and the template:

```text
spec tokens :  [ extract, the, 'path', from, url ]
template    :  [ extract, the, {param}, from, url ]
```

Lengths match (5 = 5). Literals `extract`, `the`, `from`, `url` align under
case-folded comparison; the hole `{param}` captures `path` (quotes stripped). The
substitution is

$$
\sigma \;=\; \{\, \texttt{param} \mapsto \texttt{path} \,\}.
$$

`find_sketch_match` returns `(sketch, σ)`, and the policy emits
`Decision.INSTANTIATE`. In `execute_slot`, `instantiate_sketch_code` applies $\sigma$
to the code holes — the `param` hole's original referent `'domain'` is a quoted
literal, so `_instantiate_param_code` rewrites it to `'path'`:

```python
def slot_fn(url):
    from urllib.parse import urlparse
    parts = urlparse(url)
    return getattr(parts, 'path' if 'path' == 'path' else '', '') or parts.netloc
```

`validate_instantiated_source` passes (`ast.parse` succeeds);
`verify_runtime_execution` runs the function on the sample `url` and the type/empty/
identity guards pass; `_call_generated_fn` runs it on the real argument. A commit with
`Decision.INSTANTIATE` is recorded, the sketch's `instantiation_count` increments and
`validated` is set, and the console prints

```text
Reusing learned pattern with parameter substitution; no generation needed.
```

**No LLM inference occurred at resolution time** for cell 2. Had any step failed —
say `'path'` were not a real `urlparse` attribute and the verify guard rejected an
empty return — `instant_ok` would be `False`, and `execute_slot` would re-route with
`force_regenerate=True`, carrying the failed sketch in `_sketch_context` into a normal
ADAPT/GENERATE. The fast path is always safe.


## Code map

| Concern | Location |
|---|---|
| Binding extraction prompt + parse | `library/binding.py:_extraction_prompt`, `extract_binding_async` |
| Phrase / binding types + hashes | `library/binding.py:SpecPhrase`, `SemanticBinding`, `compute_structural_signature`, `compute_binding_id` |
| Spec template construction | `library/binding.py:build_spec_template` |
| Clarity gate | `library/binding.py:evaluate_binding_clarity` |
| Sketch type + build | `library/sketch.py:CodeSketch`, `SketchParam`, `build_code_sketch_from_commit`, `build_code_template` |
| Deterministic matching | `library/sketch.py:tokenize_spec_text`, `template_token_pattern`, `match_spec_to_sketch`, `find_sketch_match` |
| Substitution / instantiation | `library/sketch.py:instantiate_sketch_code`, `validate_instantiated_source`, `merge_sketch_into_library` |
| Persistence | `library/sketch_store.py:load_sketch_library`, `save_sketch_library` |
| Routing → INSTANTIATE | `routing.py:_try_sketch_instantiation`, `RoutingPolicy.decide` (Cases 3, 5) |
| INSTANTIATE execution + fallthrough | `slot_resolver.py:execute_slot` (INSTANTIATE branch), `_run_sketch_binding_extraction`, `_schedule_sketch_binding_extraction` |
| Config | `agents/config.py:sketch_library_learning`, `sketch_library_learning_async`, `sketch_library_min_confidence`, `openai_model`, `cache_dir` |
| LLM transport | `agents/llm_utils.py:classify_with_llm` (OpenAI Responses API, `gpt-5.5`) |
