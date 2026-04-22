# Changelog

## [0.1.0] - 2026-04-22

### Added

- `#>` spec line and `#<` reasoning line syntax highlighting via TextMate injection grammar and editor decorations (teal for specs, green for reasoning)
- Slot history tree view in the Explorer panel: lists every commit with decision label (GENERATE / REUSE / ADAPT / INSTANTIATE), timestamp, and message
- Split-view dispatch: `Semipy: Open dispatch split view` opens the generated `.semi.py` file side-by-side with the source file, with linked selection highlighting
- Inlay hints on spec lines showing last resolution decision and commit id
- CodeLens above `@semiformal` functions showing commit id and decision type
- Sign-flip: editing a `#<` reasoning line automatically rewrites the prefix to `#>` (promotes to user-owned spec)
- Semantic phrase highlighting: colors operation, parameter, operator, and connective phrases in spec lines based on pattern-learning bindings
- Diagnostics integration: loads `diagnostics.json` written by the pipeline and shows errors with quick-fix code actions
- CLI bridge: `semipy lock`, `unlock`, `rollback`, and `regenerate` commands with smart Python interpreter resolution (configured path → workspace `.venv` → Python extension → fallback)
- Configurable debounce for portal artifact reloads (`semipy.debounceMs`)
- Configurable highlight fade duration for split-view linked highlighting (`semipy.linkedHighlightFadeMs`)
- `Semipy: Show output log` command to open the Output channel
