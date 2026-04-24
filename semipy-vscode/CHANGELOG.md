# Changelog

## [0.2.0] - 2026-04-24

### Fixed

- CodeLens and inlay hints on files containing multiple `@semiformal` functions: each slot now anchors on its own enclosing `@semiformal`, not the topmost one in the file. The anchor resolver prefers `slot_spec.enclosing_function_span[1] - 1` and, when falling back to a backward scan, breaks on the first (nearest) `@semiformal` instead of overwriting with older matches.
- Phantom slot entries (0 commits) no longer stack extra CodeLens actions, inlay hints, or tree-view rows on top of live slots.

### Added

- Spec + surface rewind on version switch: `pickSlotVersion`, `lockSlotVersion`, and `unlockSlotVersion` now run the new `semipy rewind-spec` CLI after the branch/lock change, rewriting the slot's `#>` spec block and `#<` surface in the source file so the editor reflects the chosen commit. Legacy commits without a source snapshot keep the prior behavior.
- `test/slotLineResolve.test.js` + `npm run test:slot-anchor` node harness verifying two `@semiformal` methods in one file resolve to distinct CodeLens anchors.

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
