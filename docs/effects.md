# The Effects Subsystem

*Reified, verifiable, version-controlled, provenance-tracked, revertable real-world effects.*

This document specifies the `semipy.effects` subsystem: the machinery that turns a
program's **real-world effect** — its writes to a database, file, data store, or
external API — into a first-class artifact that can be *verified*, *version-controlled*,
*provenance-tracked*, and *reverted*. The animating concern is adversarial: when an
LLM synthesizes the code that mutates your customer table, nothing should let it
silently "vibe-mutate" that artifact. The effect must become **data** that a trusted,
non-LLM handler inspects before anything touches the world.

> **The entire subsystem is opt-in.** `effects_enabled` defaults **off**, and so do
> `effect_staging`, `effect_gate`, `effect_smt`, and `effect_auto_apply`. The single
> default-*on* flag is `effect_require_approval_external`, which only matters once the
> rest are enabled. With defaults, **nothing in `effects/` runs** and a pure project
> sees no change to generation or runtime. See [§10 Configuration](#10-configuration).

---

## 1. Motivation — algebraic effects, reified

A conventional LLM-generated function that "updates the customer record" opens a
database connection and issues an `UPDATE`. By the time you can inspect what it did,
it is already done. There is no artifact to verify, no inverse to replay, no lineage
to walk.

The effects subsystem borrows the **algebraic-effects** idea: a computation does not
*perform* its effects; it *describes* them. The generated function for an effectful
slot never imports a driver and never opens a connection. Instead it receives an
object-capability named `fx` (an `EffectRecorder`, `capability.py`) and calls
`fx.create / read / update / delete / append / call(target, ...)`. Each call records
a reified `Effect` — pure data — into an `EffectScript`. The function therefore
*cannot* touch the world; it can only *emit intent*. As the module docstring of
`capability.py` puts it, this is the object-capability **confinement boundary**: the
model emits intent; semipy interprets it.

**Reification** is the load-bearing move. Once the effect is a list of typed `Effect`
values rather than an opaque side effect buried in control flow, it becomes an object
you can do mathematics and engineering on:

- **verify** it statically (is every mutation reversible and bounded?),
- **prove** properties of it for *all* inputs (does this `delete` ever hit more than
  one row?),
- **version** it (append it to a per-slot ledger keyed to the commit that produced it),
- **revert** it (replay materialized inverse effects),
- **trace** it (walk artifact → ledger event → commit → spec).

A slot is **effectful** iff its generated function declares a parameter named `fx`
(`inject.py:fn_is_effectful`). This is *inferred* — there is no new user syntax. The
generation prompt instructs the model to add `fx` only when the spec implies mutating
an external artifact; a pure slot's function never declares it and the whole subsystem
is bypassed for that slot.

---

## 2. The effect model

The op vocabulary is small, fixed, and data-agnostic (`models.py`):

$$
\textsf{op} \in \{\,\texttt{create},\ \texttt{read},\ \texttt{update},\ \texttt{delete},\ \texttt{append},\ \texttt{call}\,\}
$$

The backend interprets ops; semipy itself never branches on payload *contents* to
decide behavior. (`read` is the only non-mutating op, `READ_OPS`; `delete` is the only
destructive op, `DESTRUCTIVE_OPS`.) This mirrors the contract subsystem's fixed
invariant vocabulary — the safety machinery reasons over a closed alphabet, not over
arbitrary code.

An `Effect` (`models.py:Effect`) is:

| field | meaning |
|---|---|
| `op` | one of the six ops above |
| `target` | a `scheme://name` id, e.g. `db://customers` |
| `payload` | the data to write (data-agnostic; the backend interprets it) |
| `selector` | which records an `update`/`delete`/`read` applies to (a dict of field equalities) |
| `compensation` | the reified **inverse**, filled by the backend at staging time |
| `provenance` | `slot_id` / `origin_commit_id` / `invocation_id`, stamped by `fx` |
| `effect_id` | content address (see below) |

An `EffectScript` (`models.py:EffectScript`) is just an ordered `list[Effect]`. It is
`len()`-able and iterable over its effects (so an approval callback can `len(script)` /
iterate naturally), and exposes `targets()`, `mutating()`, `op_counts()`, and a human
`summary()`.

**Content addressing.** `models.py:compute_effect_id` hashes `(op, target, payload,
selector)` through an *order-independent canonical repr* (`_canonical_repr` sorts dict
keys), so two effects with the same meaning hash identically regardless of dict
ordering:

$$
\texttt{effect\_id} = H\big(\,\textsf{op} \,\|\, \textsf{target} \,\|\, \kappa(\textsf{payload}) \,\|\, \kappa(\textsf{selector})\,\big)
$$

where $\kappa$ is the canonical repr and $H$ is a truncated SHA-256. Ledger events are
addressed analogously by `compute_event_id` over `(slot_id, origin_commit_id,
invocation_id, seq)`.

What the caller gets back is an `EffectResult` (`models.py:EffectResult`): it carries
the `effect_script` (always), the function's own `value`, and — once applied — an
`applied` flag and the `event_id`. It exposes `.revert()` for in-hand undo. A
`LedgerEvent` (`models.py:LedgerEvent`) is one append-only ledger entry storing the
*materialized* applied effects and their compensations; details in [§7](#7-stage-4--the-ledgerprovenancerevert-spine).

---

## 3. The `fx` capability and shadow worlds

`fx` is an `EffectRecorder` (`capability.py:EffectRecorder`). Calling `fx.create(...)`,
`fx.update(...)`, etc. constructs an `Effect`, stamps the recorder's provenance onto
it, and appends it to `fx.script`. The interesting part is the optional bound
**shadow world**.

When the recorder is bound to a `ShadowWorld` (the staging case), each write op is
applied *to the shadow as it is recorded*. The order in `_record` matters and is
deliberate:

1. capture the inverse from the **pre-image** first (`world.compensation_for(eff)`),
   so a later revert is exact;
2. then mutate the shadow (`world.apply(eff)`), so a subsequent `fx.read` in the same
   script reflects this write.

This gives **read-your-writes** (Worlds semantics, Warth & Kay): inside one script, a
`read` after an `update` sees the updated value, against a *copy* of the artifact — the
function never sees real state and never mutates it. When no world is bound (pure
dry-run), the recorder only records intent and `read` returns `None`. Staging is
best-effort: a backend hiccup during staging degrades to record-only for that effect,
and the `reversible` gate will then catch the now-missing compensation by design.

A `ShadowWorld` (`shadow.py:ShadowWorld`) opens **one shadow per target**, lazily, via
the registered backend. It exposes a recorder-facing surface (`apply` / `read` /
`compensation_for`) and a handler-facing surface (`snapshot` / `diff` / `commit_all` /
`discard_all`). `commit_all` applies every shadow's staged state to the real artifacts;
`discard_all` drops them all.

`shadow.py:run_effectful_source` compiles a candidate's source, binds a fresh recorder
and world, and runs the function over one input **in-process** — safe without a
subprocess precisely because the function is confined to `fx` and has no handle to any
real artifact. It returns `(script, world, error)`.

### Backends as a Protocol

A backend (`backends/__init__.py:ArtifactBackend`, a `runtime_checkable` `Protocol`)
knows how to stage / snapshot / diff / compensate / commit / discard for one
`scheme://` family. The handler is artifact-agnostic: it speaks only the fixed op
vocabulary and this Protocol, never branching on a target's domain. Backends are
selected by scheme via `register_artifact_backend(scheme, backend)` /
`resolve_backend(target)` (with a `SEMIPY_EFFECT_BACKEND` env default for unscoped
targets).

Three backends ship:

- **`memory.py` (`mem://`)** — a per-backend `stores` registry of tables (dicts keyed
  by `key_field`, default `id`) or lists (append logs). The shadow is a deep copy;
  `commit` writes it back; `compensation_for` captures the pre-image. Fully shadowable
  and reversible, which makes it the cleanest backend for the demo. Its `schema()`
  reports `key_field` as the unique key for a table store.
- **`sqlite.py` (`db://`)** — targets name tables; the **shadow is a real
  transaction**. A dedicated connection in autocommit mode issues `BEGIN`; effects run
  as parameterized SQL; `commit` is `COMMIT` and `discard` is `ROLLBACK`. Critically,
  **one connection/transaction is shared per open scope** (`_begin` / `_end`), so a
  multi-table effect (customers + audit_log) is one atomic `COMMIT` and two tables
  never contend for SQLite's single write lock. Column/table identifiers are quoted
  (`_quote_ident`, rejecting embedded quotes); values are always bound parameters,
  never string-interpolated. `schema()` introspects the PK and UNIQUE indexes via
  `PRAGMA` into a set of unique keys. This proves the Protocol generalizes from memory
  to a real transactional DB with no semipy-side branching.
- **`external.py` (`ExternalArtifactBackend`)** — non-shadowable; covered in
  [§8](#8-stage-5--external--irreversible-effects).

---

## 4. Static verification — the two enforced invariants

`verify.py:verify_static` enforces exactly **two** invariants. These are the ones
decidable by structural analysis of the reified script plus the shadow-filled
compensations, with *zero per-slot declaration* required.

> **Scope note.** An earlier design contemplated a learned, per-slot effect *contract*
> (`EffectCase` / `SlotEffectContract` / `EffectInvariant`). That deferred mechanism
> has been **removed**. Only `reversible` and `bounded_blast_radius` are actually
> enforced. The substantive safety — reversibility, blast-radius bounding, the
> forall-inputs proof, and regression detection — runs without it.

**(1) `reversible`.** Every mutating effect must carry a `compensation`. The shadow
fills `compensation` at staging time from the pre-image; a mutation the backend cannot
invert with a single effect (e.g. a multi-record delete) leaves `compensation is None`
and fails the check *before anything is applied for real*. The error tells the model to
narrow it to an invertible, record-level change (e.g. a single-record update/delete by
key).

**(2) `bounded_blast_radius` (unbounded guard).** A mutating `update`/`delete` must
carry a `selector`. A selectorless mutation targets *every* record — the "wipe the
table" catastrophe — and is rejected:

$$
\big(\textsf{op} \in \{\texttt{update}, \texttt{delete}\}\big) \ \wedge\ \big(\textsf{selector} = \varnothing\big) \ \Longrightarrow \ \textbf{reject}.
$$

`is_external` (when supplied) exempts non-shadowable targets from the `reversible`
check — those are governed by approval + idempotency, not shadow-revert (see §8). Each
failure carries a descriptive `failure_kind` (`FAILURE_KINDS`) that the gate's
regeneration message consumes.

The static check is necessary but coarse: it catches *absolute* dangers (no selector at
all, no inverse). It does **not** catch a selector that *exists but is too broad*
(`WHERE tier='silver'` matches many rows). That requires the diff (§5) and the
forall-inputs proof (§6).

---

## 5. Stage 2 — artifact-state effect diff

`diff.py:compute_effect_state_diff` catches a *relative* danger the static check
misses: an ADAPT that silently **escalates the blast radius** — the parent updated one
row, the regenerated impl now deletes many. It runs the parent and the candidate over
the **same input** against **fresh shadows** and compares the resulting `StateDelta`s
(ground truth of what changed), reusing the backend's own `snapshot` / `diff` — there is
no parallel comparator to drift out of sync.

A `StateDelta` (`backends/__init__.py:StateDelta`) records `added` / `removed` /
`modified` record keys (opaque, backend-chosen) and an `affected_count()`. Let the
parent's delta on target $t$ be $(a_p, r_p)$ for *affected* and *removed* counts and the
candidate's be $(a_c, r_c)$. The regression rule is deliberately **conservative** (few
false positives on legitimate refinements):

$$
\textsf{regression}(t) \;=\;
\underbrace{\big(r_c > r_p\big)}_{\text{removes more (destructive escalation)}}
\ \vee\
\underbrace{\big(a_c > 2\,a_p\big) \wedge \big(a_c - a_p > B\big)}_{\text{affects materially more}}
$$

where $B$ is `effect_default_blast_radius` (default 1). A benign added history-append
the parent lacked does **not** trip this — it adds records but removes none, and a small
addition stays under both clauses. Stage 2 only compares on the triggering input for
now; once the ledger (§7) records previously-applied inputs, those feed this same
machinery to re-check regressions on inputs the parent handled.

This check is folded into the generate gate via `_assess`, behind
`effect_block_regressions` (default on) and only when a `parent_source` exists.

---

## 6. Stage 3 — the forall-inputs blast-radius theorem

This is the mathematical center of the subsystem, and a *deliberate, documented*
deviation from the original plan's Z3/CrossHair. **Stage 3 ships no SAT/SMT solver and
no concolic engine.** It is dependency-free schema and AST reasoning. Here is why that
is not a compromise but the *correct* realization.

### The selector vocabulary

A `selector` is an **AND-of-field-equalities**: a finite conjunction
$\bigwedge_{k \in K}\big(\textsf{col}_k = \textsf{val}_k\big)$ over a set of columns
$K$. The values $\textsf{val}_k$ are *inputs* — they vary per call. The question we want
to answer for safety is **cardinality**: across *all* inputs and *all* possible artifact
states, how many records can this selector match?

### The theorem

Let artifact $A$ have schema with a family of **unique key** column-sets
$\mathcal{U} = \{U_1, U_2, \dots\}$ (each $U_i$ is a set of columns declared unique — a
primary key or a UNIQUE index; `schema.py:ArtifactSchema`). For a selector over column
set $K$:

> **Theorem (bounded blast radius).** A mutating `update`/`delete` with selector over
> columns $K$ on artifact $A$ affects **at most one** record, *for all inputs and all
> artifact states*, **iff** $K$ contains a unique key of $A$:
> $$\exists\, U \in \mathcal{U}\ :\ U \subseteq K.$$
> For `create`/`append`, exactly one record is inserted unconditionally.

In short: bounded-blast-radius reduces to a **schema superkey lookup**. ($K$ is a
*superkey* exactly when it contains some unique key.) `schema.py:ArtifactSchema.has_unique_subset`
computes precisely $\exists U \in \mathcal{U}: U \subseteq K$.

**Proof (⇐, soundness).** Suppose $U \subseteq K$ for some unique key $U$. The selector
constrains every column of $K$, hence every column of $U$, to a fixed value. By the
uniqueness of $U$, at most one record in any legal state of $A$ can have that exact
combination of values on the columns of $U$. Therefore the selector matches at most one
record, for any input assignment and any artifact state. $\square$

**Proof (⇒, completeness / counterexample).** Suppose $K$ contains *no* unique key,
i.e. $K$ is not a superkey. Then by the definition of a (super)key there exists a legal
artifact state with two distinct records $\rho_1 \neq \rho_2$ that agree on every column
in $K$ (if no such state existed, $K$ would functionally determine the row and would be
a superkey — contradiction). Choose the input values to equal that shared assignment.
The selector then matches both $\rho_1$ and $\rho_2$: blast radius $\geq 2$. Hence
non-superkey selectors are *not* provably bounded — there is a concrete database state
witnessing the violation. $\square$

This is a genuine **forall-inputs** result: it quantifies over every input assignment
*and* every artifact state, with no execution and no sampling.

### Why Z3 adds nothing and CrossHair is the wrong tool

A general SMT solver would let you *encode* "for all inputs, the number of matching rows
is ≤ 1." But that encoding, for AND-of-equalities selectors, is *exactly* the superkey
condition above — Z3 would rediscover the theorem we already proved, at the cost of a
dependency, a solver process, and a translation layer. There is no residual hardness for
it to absorb: the decision procedure is set containment over the schema's unique keys.

CrossHair is doubly wrong: it is a **concolic** engine that reasons about a *function's
input space* to find inputs that violate an assertion. But blast radius is not a fact
about function inputs — it is a fact about **artifact cardinality** (how many rows can
exist with a given key assignment). The dangerous quantity lives in the *database
state*, which CrossHair does not model. It would happily conclude an `update WHERE
tier='silver'` is "fine" because no *input* makes the function misbehave; the harm is
that the table contains many silver rows.

`prove.py:prove_bounded_blast_radius` implements the theorem directly: it walks the
script's mutating effects, skips `create`/`append` (single insert) and `call` (opaque,
governed by approval), and for each `update`/`delete` checks
`schema.has_unique_subset(selector_keys)`. It returns `proved` when *all* are bounded,
else `unknown` (not `refuted`) listing the unprovable effects with an actionable hint —
"Use a selector that includes a unique key (id)."

### AST-structural theorems

Two further properties are sound **structural** theorems over the generated *source*
(`prove.py`):

- **`prove_append_only`** — if `fx.delete` never appears syntactically (and dispatch is
  not computed), then *no input* can delete: `proved` (append-only). A reachable
  `fx.delete` is `refuted`. Reaching the capability through `getattr(fx, op)` defeats the
  static read and yields `unknown`.
- **`prove_target_whitelist(source, whitelist)`** — if every literal target string is in
  the allowlist (and none is computed), then *no input* escapes the allowlist: `proved`.
  A literal target outside it is `refuted`; a computed (non-literal) target is `unknown`.

Both share `_fx_calls`, which AST-walks `fx.<method>(...)` calls and flags
`getattr(fx, ...)` as dynamic dispatch.

### The `unknown` → sample-fallback seam

A `ProofResult` (`prove.py:ProofResult`) has status `proved` / `refuted` / `unknown`,
and its `.ok` treats only a **clear refutation** as a hard failure — `unknown` *defers*
to the Stage-1 sample checks. **Proofs never weaken safety; they only strengthen it.**
When a target is computed or an op is dynamic, the prover abstains and the existing
sample-based static check still runs. The `ProofResult` shape deliberately leaves room
for a concolic backend to later refine an `unknown` into a concrete-input
counterexample, without shipping untested SAT code today.

---

## 7. Stage 4 — the ledger / provenance / revert spine

This is the version-control spine: the part that makes an applied effect a durable,
traceable, undoable record.

### The ledger

`Slot.ledger` is a persisted dict field (migrates with `{}`) holding an append-only
`EffectLedger` (`ledger.py:EffectLedger`) of `LedgerEvent`s. Each event is keyed by
`(slot_id, origin_commit_id, invocation_id)` and stores the **materialized** applied
effects plus their compensations and a snapshot ref. Materialization is the key
invariant: a revert replays the *exact* inverses captured at apply time and **never
re-derives** them from the (regenerable, possibly non-identical) implementation. This is
the defense against the **semantic-rollback hazard** — undoing something *different* from
what was done because the code changed underneath. (de)serialization is recursive
because an effect carries its compensation, which is itself an effect.

### `execute_effectful` — the single runtime path

`apply.py:execute_effectful` is the one place an effectful slot's function runs at call
time. It binds a `ShadowWorld` (so `fx.read` returns real pre-state and compensations are
captured), runs the function, then branches:

- **dry-run (the default, and whenever the apply preconditions are not met):** discard
  the shadow and return an `EffectResult` with `applied=False`.
- **auto-apply** — and *only* when
  $$\texttt{effect\_auto\_apply}\ \wedge\ \texttt{effect\_gate}\ \wedge\ \texttt{effect\_staging}$$
  are all on (plus a bound world, a non-`None` slot, and a non-empty script): the hard
  invariant **"never auto-apply an ungated effect."** It then **re-verifies the script at
  the real input** (`_verify_for_apply` — the gate only ever saw a *sample* input;
  `verify_static` again, plus the schema proof when `effect_smt` is on). If safe, it
  snapshots, `world.commit_all()` commits the shadow to the real artifact, and appends a
  `LedgerEvent` with the materialized compensations (`_record_event`), then persists the
  portal.

If the runtime re-verify fails, it **refuses loudly** by raising `EffectRefused`
(`models.py:EffectRefused`) — a dedicated exception distinct from `SemiCallError`: the
function ran *fine*, but its effect was rejected. The message states the reason plainly
and carries the planned-but-not-applied script, rather than framing it as a code failure.

### Revert

`compensate.py:revert(target)` takes an `EffectResult` or a `LedgerEvent` and replays
each effect's stored `compensation` **in reverse order** through the registered backends
(a Saga). It never re-derives inverses; an effect without a compensation is skipped (it
was judged irreversible at gate time and so should never have been applied).
`revert_ledger_event(slot, event_id)` is the **durable** form: it finds the event, replays
its compensations, flips the original event's status to `reverted`, and **appends a new
`reverted` event** — append-only audit trail, never a destructive edit.

### Provenance

`provenance.py:provenance_for(slot, event_id)` walks the full why/how/where chain:

$$
\text{artifact mutation} \ \to\ \underbrace{\text{LedgerEvent}}_{\text{when / what applied}} \ \to\ \underbrace{\text{origin commit}}_{\text{HOW: generated source}} \ \to\ \underbrace{\text{slot spec + change reason}}_{\text{WHY / WHAT}}
$$

It returns a `ProvenanceChain` carrying the event id and status, the touched `targets`,
the `origin_commit_id` and `decision`, the `spec_text` (the user's `#>` / `semi()`
intent), the change-record `reason`, and the `generated_source`. This is the link no
other system has, because semipy co-locates the generated code's lineage with the effect
it produced.

### The two gates in `slot_resolver.py`

Mirroring the contract gates:

- **`_run_generate_effect_gate`** — after generation/ADAPT and before the commit is
  finalized. It compiles the candidate; if pure (no `fx`) it passes through untouched.
  Otherwise `_assess` runs the script against a shadow, checks `verify_static`,
  optionally runs the forall-inputs proof (`effect_smt`), and optionally runs the
  Stage-2 regression diff (`effect_block_regressions` + a `parent_source`). On a
  violation it appends the reason to `verify_failure_context` and **regenerates** up to
  `effect_gate_max_retries`. The shadow is always discarded — the gate is dry-run.
- **`_run_reuse_effect_gate`** — a *reused* effectful impl can emit an
  invariant-violating script on a **new input shape** (e.g. a selector that is now
  empty). It re-stages and re-verifies over the current input; on violation it returns
  `(message, validation_result)` so the caller **forces ADAPT** (RoutingPolicy Case 1 —
  no RoutingPolicy change was needed). It never raises.

Both gates no-op immediately unless `effects_enabled` *and* `effect_gate` are on.

---

## 8. Stage 5 — external / irreversible effects

Some targets cannot be shadowed: there is no copy to stage against and no general way to
roll back a sent email or a charged card. `external.py:ExternalArtifactBackend` rides the
same Protocol but is **non-shadowable** (`shadowable=False`) and treats the "shadow" as a
*plan*:

- **`apply` PLANS** — it records the intent into `ExternalPlan.pending` and does **not**
  perform the action.
- **`commit` PERFORMS** — it calls the user-supplied `sender(effect)` for each pending
  effect, and is **idempotent**: it dedups by `payload['idempotency_key']` (falling back
  to `effect_id`), skipping any key already sent. This defends the semantic-rollback
  hazard from the other side: a re-run never duplicates an externalized action.
- **`discard`** drops the plan.

The `reversible` gate **exempts** external targets (`verify_static(..., is_external=...)`):
they are governed by approval + idempotency, not shadow-revert. The schema blast-radius
proof applies only to `update`/`delete`; a `call` is opaque.

### The approval gate

`execute_effectful` adds an **approval gate**. When a script touches any non-shadowable
target and `effect_require_approval_external` is on (default), it invokes
`config.effect_approval_callback(script)` with the **planned (un-sent)** effects — "here
is what I will do" — and the decision is **approve-all-or-apply-nothing** (atomic), so a
mixed DB+email script never commits the DB write without consent for the email. A
falsy/absent callback leaves the result **planned** (dry-run, `applied=False`), not an
error. A callback that *raises* surfaces loudly — that is a bug in the approval
mechanism, not a denial, and must not silently drop the effect.

---

## 9. Worked example — the CRM ETL

`examples/effects_db_transactions.py` exercises the whole subsystem end-to-end on real
SQLite. The setup creates a `customers` table (PK `id`) and an `audit_log` table
(autoincrement PK), seeds one customer, registers the backend, and **opts in**:

```python
register_artifact_backend("db", SqliteArtifactBackend(db))
configure(
    effects_enabled=True, effect_staging=True, effect_gate=True, effect_smt=True,
    effect_auto_apply=True, verbose=False, cache_dir=...,
)
```

A **pure** slot cleans a messy record (no `fx`, so it returns a plain dict and the whole
effects machinery is bypassed):

```python
def clean(raw: str) -> dict:
    return semi(
        f"Parse the raw customer record {raw!r} into a dict with keys: id (int ...), "
        f"name (str, Title Case), tier (str, lowercase), spend (float). Return only the dict.",
        expected_type=dict,
    )
```

An **effectful** slot upserts the customer and writes an audit row as one atomic
multi-table transaction. Its generated function declares `fx`, so it is effectful by
inference:

```python
def commit_customer(customer: dict) -> EffectResult:
    return semi(
        f"Upsert the customer {customer} into 'db://customers': read the row whose id "
        f"equals the customer's id; if it exists, update its name/tier/spend, otherwise "
        f"insert a new row. Then append an entry to 'db://audit_log' with columns "
        f"customer_id (the id) and action ('insert' or 'update')."
    )
```

### Trace

**GENERATE → gate.** On the first call the slot has no implementation, so semipy
GENERATES one. The generated function reads `db://customers` against the **shadow**
(read-your-writes returns the *real* pre-state — so the model learns whether the row
exists), then emits an `update`-by-`id` or a `create`, plus an `append` to
`db://audit_log`. `_run_generate_effect_gate` stages this script and runs `_assess`:
`verify_static` confirms each mutation has a selector and a compensation; with
`effect_smt` on, `prove_bounded_blast_radius` confirms the `update WHERE id=...` selector
contains the PK (a unique key) and is therefore provably ≤ 1 record. The candidate
passes and the commit is finalized. A representative emitted script:

```text
read db://customers where {'id': 1001};
update db://customers where {'id': 1001};
append db://audit_log
```

**REUSE.** The second and third records hit the *same* slot. The cached implementation
is REUSED — no LLM call — and branches update-vs-insert from the shadow read on its own.
`_run_reuse_effect_gate` re-verifies the emitted script on each new input; since the
selectors stay PK-scoped, it passes and no ADAPT is forced.

**Auto-apply → ledger.** Because `effect_auto_apply ∧ effect_gate ∧ effect_staging` all
hold, `execute_effectful` re-verifies at the real input, then `world.commit_all()` issues
a single atomic `COMMIT` over *both* tables (one shared transaction — see the SQLite
backend's per-scope connection), and appends a `LedgerEvent` with the materialized
compensations. `res.applied` is `True` and `res.event_id` is set.

**Provenance.** `provenance_for(slot)` on each effectful slot walks artifact → latest
event → origin commit → spec, printing the targets, the commit/decision, the `#>`/`semi`
intent, and the change reason.

**Revert.** `last_res.revert()` replays the last invocation's materialized compensations
in reverse — restoring the `customers` row to its pre-image *and* removing the appended
`audit_log` row — exactly, from the captured inverses, never from a re-derivation.

**Blocked sweeping delete.** Finally:

```python
semi("Delete every row in 'db://customers' that has tier 'silver'.")
```

The generated effect is `delete db://customers WHERE {'tier': 'silver'}`. The selector
column set is $K = \{\texttt{tier}\}$. The `customers` schema's unique keys are
$\mathcal{U} = \{\{\texttt{id}\}, \{\texttt{rowid}\}\}$. Since no unique key is a subset
of $K$, `prove_bounded_blast_radius` returns **not `proved`** — by the completeness
direction of the theorem, a state with two silver rows witnesses blast radius ≥ 2. The
gate refuses it (and at apply time `execute_effectful` would raise `EffectRefused`). The
table is untouched. This is the case Stage 1 (it *has* a selector) and Stage 2 (no
parent to diff against) both pass, and only the Stage-3 theorem catches.

---

## 10. Configuration

All effect flags live in `SemiConfig` (`agents/config.py`) and are read via
`getattr(config, ..., default)` so existing portals migrate with no rewrite.

| flag | default | stage | meaning |
|---|---|---|---|
| `effects_enabled` | **off** | 0 | master switch; teaches `fx`, enables effectful slots |
| `effect_staging` | **off** | 1 | open shadows; run effect verification before accept/apply |
| `effect_gate` | **off** | 1 | enforce invariants + block regressions (acceptance gate) |
| `effect_gate_max_retries` | `1` | 1 | regeneration budget to satisfy violated invariants |
| `effect_block_regressions` | on | 2 | an unintended artifact-state diff vs parent fails the gate |
| `effect_smt` | **off** | 3 | forall-inputs schema/AST proofs (dependency-free; no Z3/CrossHair) |
| `effect_auto_apply` | **off** | 4 | commit the verified shadow to the REAL artifact (requires gate) |
| `effect_require_approval_external` | **on** | 5 | externalized targets need approval before commit |
| `effect_approval_callback` | `None` | 5 | `callable(EffectScript) -> bool`; runtime-only, not persisted |
| `effect_default_blast_radius` | `1` | 2 | the bound $B$ in the regression rule |

With every default in force, `effects_enabled` is off and the entire subsystem is inert.
A researcher opts in incrementally: `effects_enabled` to teach `fx`; `effect_staging` +
`effect_gate` to verify and gate (still dry-run); `effect_smt` for the forall-inputs
theorem; and only `effect_auto_apply` to let a *gated, re-verified* effect actually touch
the world.

---

## 11. Public API

Exported from `semipy.__init__`: `Effect`, `EffectScript`, `EffectResult`,
`EffectRefused`, `EffectRecorder`, `ArtifactBackend`, `MemoryArtifactBackend`,
`SqliteArtifactBackend`, `ExternalArtifactBackend`, `register_artifact_backend`,
`resolve_backend`, `revert`, `provenance_for`. Users do not hand-construct effect types
or call the recorder directly in normal use — the runtime injects `fx`, wraps the script,
and owns staging/gating/apply. The effectful path is entered solely by writing a `semi()`
whose generated function the model chooses to give an `fx` parameter.

---

## Note on the generation model

Sketch/contract classification and the generation pipeline use the **OpenAI Responses
API** (`gpt-5.5`), not OpenRouter. The effects prompt block (rendered only when
`effects_enabled`) teaches the model the `fx` capability and tells it that when it adds
`fx` it returns `fx.script` and ignores any return-shape/output-name contract — it never
hand-constructs effect types; the runtime wraps the recorded script.
