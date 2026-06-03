# semipy documentation

`semipy` is a runtime semiformal system: the `@semiformal` decorator and `semi()`
let you express underspecified logic (natural-language conditions, extraction
rules). On first use an LLM generates a real Python function via an agentic
pipeline; the function is validated, version-controlled, and cached, so later
calls reuse it with no LLM invocation.

## Start here

- **[semi-formal-programming.md](semi-formal-programming.md)** — the idea. What
  semi-formal programming is and why a program might leave part of its
  specification informal until first use.

## Architecture & subsystems

Each document is grounded in the source and uses math where it clarifies, with a
concrete worked example.

| Document | What it covers |
|---|---|
| **[architecture.md](architecture.md)** | The runtime spine: call-site identity vs slot identity, the spec-equivalence key (the reuse fingerprint), the REUSE / ADAPT / GENERATE / INSTANTIATE decision procedure, the single-tool OpenAI Responses generation pipeline, and the Portal⊃Slot⊃Commit DAG cache. |
| **[behavioral-contract.md](behavioral-contract.md)** | The contract subsystem: content-addressed cases, the digit-normalized structural input fingerprint, the executable contract runner, intended-vs-unintended effect diffing, the two acceptance gates, and spec-change retirement. |
| **[effects.md](effects.md)** | Reified real-world effects: the `fx` capability and `EffectScript`, shadow worlds and pluggable backends, static verification, the for-all-inputs blast-radius theorem (schema superkey), and the ledger / provenance / revert spine. |
| **[sketch-library.md](sketch-library.md)** | Pattern learning: how a generated implementation becomes a parametric `CodeSketch`, and how a later slot is satisfied by substitution (the INSTANTIATE decision) with no LLM call. |

## Reference

- **[effect design.md](effect%20design.md)** — historical design rationale for the
  effects subsystem (superseded by `effects.md` for the shipped behavior).

## Conventions across the docs

- The LLM pipeline uses the **OpenAI Responses API** (via `pydantic_ai`), keyed on
  `OPENAI_API_KEY`; the default model is `gpt-5.5`.
- The contract and effects subsystems are largely **opt-in**: `contract_enabled`
  is on (it records contracts and change provenance), but the contract *gate*,
  the LLM maintainer, and the **entire** effects subsystem default off. Each
  document states what runs by default versus what you enable.
