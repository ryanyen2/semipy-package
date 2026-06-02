# Semipy

Editor support for [semipy](https://github.com/ryanyen2/semipy-package) — a runtime semiformal system that generates and caches Python functions from natural-language specs using an agentic LLM pipeline.

## What this extension does

semipy generates, verifies, gates, and applies your code as you author. This
extension makes that loop legible — so you can **know** what it did, **work
with** it, and **steer** it — using one quiet visual language: *opacity =
durability* (inferred / dry-run material is dim; durable / applied / yours is
full-weight), one accent per concept (teal = spec/contract, soft-green =
intended, amber = effect/caution, red = regression), and a minimum-set rule:
every indicator appears only when it carries information.

### Know what happened

- **CodeLens health sentence** above each `@semiformal` function: `◐ ADAPT · ✓3 hold · ⚡ db://customers applied` — decision, guarantees, and real-world effect in one line. Click it to open the Slot Inspector.
- **Explanation Card** on hover: *why* the slot last changed, *what* changed (before→after, with unintended regressions flagged), *what it guarantees*, and *what it touched* — with inline actions.
- **Gutter health glyph** + overview-ruler tick: a per-slot dot you can scan — clean / touches-the-world / needs-attention / regression.
- **Regressions in the Problems panel** — an unintended regression raises a persistent Warning on the slot line; a brief status message marks every regeneration.

### Work with it

- **Inspect → tree reveal** — `Inspect` focuses the slot in the slot-history tree (the persistent, native inspector). Each slot expands into **Guarantees** (grouped by assertion, with reasons and an example input on hover; inline **Relax** to quarantine one) and **Effects** (ledger events with inline **Revert**).
- **Split-view dispatch** — open the generated `.semi.py` beside your source, with linked highlighting. The generated file is dimmed as machine-authored; lines you edit return to full opacity.
- **Version control** — switch / lock / unlock a slot's implementation; the source `#>` / `#<` is rewound to match.

### Steer it

- **Pin as contract** — promote an inferred `#<` note to a `#>` contract line from a lightbulb or hover (zone-tinted: provenance vs effect). The next run honours it.
- **Steering modes** — a status-bar control that explains each gate (contract gate, effect staging / gate / auto-apply, approval, pattern learning) and scaffolds the matching `configure(...)` call.
- **Diagnostics and code actions** — quick-fix to regenerate a spec from a pipeline error.

### Syntax

- `#>` spec lines (teal) and `#<` reasoning lines (dimmed green), painted reliably even when Pylance owns comment tokenization; semantic phrase highlighting from pattern-learning bindings.

## Requirements

- Python 3.11 or later
- `semipy` installed in the active Python environment (`pip install semipy`)
- `OPENAI_API_KEY` set in the environment or in a `.env` file at the project root

## Installation

1. Install the Python package: `pip install semipy`
2. Install this extension from the VS Code Marketplace
3. Open a project that contains `@semiformal` code

## Configuration

| Setting | Default | Description |
|---|---|---|
| `semipy.sessionSource` | `""` | Absolute path matching `configure(session_source=...)`. Use `${workspaceFolder}` when the portal is keyed to the opened folder (e.g. Jupyter notebooks). |
| `semipy.pythonPath` | `""` | Path to the Python interpreter for `python -m semipy` CLI commands. When empty, uses `.venv`/`venv` in the workspace, then the Python extension interpreter. |
| `semipy.enableCodeLens` | `true` | Show commit/version CodeLens above `@semiformal` functions. |
| `semipy.enableInlayHints` | `true` | Show last resolution (decision, commit id) as inlay hints on spec lines. |
| `semipy.enableGutterHealth` | `true` | Show the per-slot health glyph in the gutter and overview ruler. |
| `semipy.enableInsightHover` | `true` | Show the Explanation Card on hover (why / guarantees / effects). |
| `semipy.notifyOnResolution` | `true` | Surface a brief message when semipy regenerates a slot, and a Problems-panel warning on regression. |
| `semipy.dimGeneratedCode` | `true` | Dim machine-authored lines in the generated dispatch `.semi.py`; lines you edit return to full opacity. |
| `semipy.enableSpecLineSyntax` | `true` | Paint `#>` / `#<` marker and body colors via editor decorations. |
| `semipy.debounceMs` | `200` | Debounce interval for reloading portal artifacts after file changes. |
| `semipy.linkedHighlightFadeMs` | `1500` | Duration before split-view linked highlights fade. |
| `semipy.signFlipOnSkeletonEdit` | `false` | Auto-promote `#<` to `#>` when you edit the line. |
| `semipy.tracePhraseDecorations` | `false` | Log phrase/binding decoration details to the Semipy Output channel for debugging. |

## Commands

| Command | Description |
|---|---|
| `Semipy: Inspect slot` | Open the Slot Inspector (why / change-diff / guarantees / effects + revert) |
| `Semipy: Steering modes…` | Choose gates to enable; scaffolds the matching `configure(...)` call |
| `Semipy: Open dispatch split view` | Open the generated `.semi.py` file alongside the source file |
| `Semipy: Refresh slot history` | Reload the portal and refresh the slot history tree |
| `Semipy: View generated code for commit` | Show the generated source for a selected commit |
| `Semipy: Regenerate this spec (CLI)` | Run `semipy regenerate` for the spec at the cursor |
| `Semipy: Show output log` | Open the Semipy output channel |

Slot-scoped actions (Inspect, View active implementation, Switch version, Lock /
Unlock, Revert effect, Pin reasoning as contract) are reachable from the
CodeLens, the Explanation Card hover, the `#<` lightbulb, and the slot tree —
they take a slot/event argument and so are hidden from the command palette.

## How it works

When you run a `@semiformal` function for the first time, semipy generates a Python function and writes it to a `.semiformal/runtime/<module>.semi.py` dispatch file. A portal JSON file (`.semiformal/<session_id>.portal.json`) records every commit, branch, and decision. This extension watches those files and reflects the pipeline state in the editor in real time.

## Troubleshooting

- **No slot history shown**: make sure the workspace contains a `.semiformal/` directory with at least one `.portal.json` file, or that `semipy.sessionSource` matches the path your code passes to `configure(session_source=...)`.
- **CLI commands fail**: check that `semipy.pythonPath` points to the interpreter where semipy is installed, or that a `.venv` directory exists at the workspace root.
