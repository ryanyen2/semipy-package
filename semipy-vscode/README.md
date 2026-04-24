# Semipy

Editor support for [semipy](https://github.com/ryanyen2/semipy-package) — a runtime semiformal system that generates and caches Python functions from natural-language specs using an agentic LLM pipeline.

## What this extension does

- **Syntax highlighting** for `#>` spec lines (teal) and `#<` reasoning lines (green) in Python files
- **Slot history tree** in the Explorer panel showing every commit, branch, and decision (GENERATE / REUSE / ADAPT) for each slot in the active file
- **Split-view dispatch** — open the generated `.semi.py` file side-by-side with your source, with linked highlighting that shows which generated function corresponds to the selected spec
- **Inlay hints** showing the last resolution decision and commit id on each spec line
- **CodeLens** above `@semiformal` functions with commit id and decision
- **Sign-flip** — editing a `#<` reasoning line automatically promotes it to a `#>` spec line
- **Diagnostics and code actions** — load `diagnostics.json` written by the pipeline and offer quick-fix to regenerate a spec via the CLI
- **CLI bridge** — run `semipy lock`, `unlock`, `rollback`, and `regenerate` commands from the command palette

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
| `semipy.enableSpecLineSyntax` | `true` | Paint `#>` / `#<` marker and body colors via editor decorations. |
| `semipy.debounceMs` | `200` | Debounce interval for reloading portal artifacts after file changes. |
| `semipy.linkedHighlightFadeMs` | `1500` | Duration before split-view linked highlights fade. |
| `semipy.signFlipOnSkeletonEdit` | `false` | Auto-promote `#<` to `#>` when you edit the line. |
| `semipy.tracePhraseDecorations` | `false` | Log phrase/binding decoration details to the Semipy Output channel for debugging. |

## Commands

| Command | Description |
|---|---|
| `Semipy: Open dispatch split view` | Open the generated `.semi.py` file alongside the source file |
| `Semipy: Refresh slot history` | Reload the portal and refresh the slot history tree |
| `Semipy: View generated code for commit` | Show the generated source for a selected commit |
| `Semipy: Regenerate this spec (CLI)` | Run `semipy regenerate` for the spec at the cursor |
| `Semipy: Show output log` | Open the Semipy output channel |

## How it works

When you run a `@semiformal` function for the first time, semipy generates a Python function and writes it to a `.semiformal/runtime/<module>.semi.py` dispatch file. A portal JSON file (`.semiformal/<session_id>.portal.json`) records every commit, branch, and decision. This extension watches those files and reflects the pipeline state in the editor in real time.

## Troubleshooting

- **No slot history shown**: make sure the workspace contains a `.semiformal/` directory with at least one `.portal.json` file, or that `semipy.sessionSource` matches the path your code passes to `configure(session_source=...)`.
- **CLI commands fail**: check that `semipy.pythonPath` points to the interpreter where semipy is installed, or that a `.venv` directory exists at the workspace root.
