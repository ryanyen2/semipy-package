# Changelog

## [0.3.2] - 2026-06-03

Aligns with the semipy `0.3.0` runtime, which scopes a portal **per project** (the
folder rooted at the nearest `.semiformal/`) instead of per source-file.

- Slot-history tree actions: **Reset slot (regenerate fresh)** on a slot and
  **Delete this version** on a commit, both with a confirmation, wired to the new
  `semipy reset-slot` / `reset-version` CLI.
- Add `sessionIdForProject` / `moduleNameForProject` (byte-matching the runtime's
  per-project identity) as a direct-hit candidate in portal discovery. The existing
  full-scan fallback already located per-project portals, so this is an optimization.

## [0.3.1] - 2026-06-03

Maintenance release aligning the extension with the semipy `0.2.2` runtime cleanup.

- Fix the `#<` steering vocabulary drift: the runtime writes `intent / given / by /
  unless` (provenance) and `yields / verified` (effect), but the extension still
  recognised the older `goal / because / alt / commits` keys — so zone tinting and
  the hover glossary silently failed for `intent`, `by`, and `unless`. The key sets,
  glossary (`KEY_HELP`), and `SteeringJson` type now mirror `semipy.models.SteeringBlock`.
- Remove dead code: the unused `data/fileWatcher.ts` module, the unused `semipy.noop`
  command, and a stale "webview" comment (the extension has been native-surfaces-only).

## [0.3.0] - 2026-06-02

The authoring-experience release: make everything semipy does under the hood
legible, workable, and steerable — without a wall of commands. One visual
language throughout: opacity = durability (inferred/dry-run is dim; durable/
applied/yours is full-weight); one accent per concept (teal = spec/contract,
soft-green = intended, amber = effect/caution, red = regression/destructive);
and a minimum-set rule — every chip/glyph appears only when it carries
information, mirroring semipy's own `_should_skip_key`.

### Added

- **CodeLens health sentence.** The lens above each `@semiformal` function is now a one-line status — `◐ ADAPT · ✓3 hold · ⚡ db://customers applied` — with decision glyph, contract count, regression flag, and effect target. Action lenses (Versions / Lock / Revert effect) appear only when actionable.
- **Explanation Card (hover).** Hovering a slot shows *why* it last changed (`change_record.reason`), *what* changed (`+N changed · M unintended`, with before→after diffs), *what it guarantees* (grouped contract cases + the reason each exists), *what it checks against* (the formal constraint: return type, output names, control context), and *what it touched* (effect targets, reversibility, applied/reverted counts) — with inline Inspect / View code / Switch version / Revert links.
- **Grouped guarantees.** Contract invariants are seeded *per observed input pattern*, so a slot that saw 7 input formats accumulates ~21 cases. The UI now collapses them by assertion — `non_empty (across 7 patterns)` — and the CodeLens counts distinct guarantees (`✓3 hold`), each with a plain-language meaning.
- **Gutter health glyph + overview ruler.** One ambient marker per slot: clean (teal dot), touches-the-world (amber ring), needs-attention (amber dot), regression/blocked (red triangle).
- **Versions + checkout on the CodeLens.** Each slot shows `v2/3` (active version / total; `· pinned` when checked out). Clicking opens a version picker (v1…vN with decision, time, `running`/`pinned`); selecting one **checks it out** so that exact version runs. Checkout is built on the package's **lock** primitive — verified rigorous: `RoutingPolicy` precedence #2 short-circuits a locked slot to `REUSE(locked commit)` and the dispatch module emits it, so the chosen version runs unchanged (unlike `rollback`, which only moves one branch head and is not authoritative when another branch is newer). "Use latest (unlock)" returns to following the newest version. Version numbers are stable: commits are append-only and ordered by `(timestamp, commit_id)`, so existing versions never renumber. The slot-history tree labels each commit `v1 · GENERATE`, `v2 · ADAPT · running` to match.
- **Inspect → tree reveal.** `Inspect` focuses and expands the slot in the slot-history tree (the persistent, native inspector). Each slot expands into **Guarantees** (grouped, with status icons, reasons, and example input on hover; inline **Relax** to quarantine) and **Effects** (ledger events with inline **Revert**). No custom webview — the editor's native surfaces carry it.
- **Regressions → Problems panel.** An unintended regression now raises a persistent Warning diagnostic (squiggle + Problems entry) on the slot line, not just a transient toast.
- **Opacity = authorship in the dispatch file.** The generated `.semi.py` is dimmed as machine-authored; any line you edit returns to full opacity (computed by diffing the buffer against the committed source — no edit-range tracking needed).
- **Interactive `#<` steering.** Inferred reasoning notes are zone-tinted (provenance: goal/because/alt/given vs effect: commits/verified/yields) and carry a discoverable lightbulb + hover action **Pin as contract (#>)** (and Dismiss).
- **Steering modes control.** A `$(settings) Semipy` status-bar item (`Semipy: Steering modes…`) explains each gate and scaffolds the matching `configure(...)` call.
- New CLI subcommands: `semipy revert-effect …` (replays stored compensations, appends a `reverted` event) and `semipy quarantine-cases …` (relaxes contract cases — backs the Relax action).
- The CLI generation receipt now points to the editor (e.g. "Generated. Hover the spec in your editor for why, guarantees, and effects"), reinforcing the split: the CLI narrates the transient process, the editor owns the persistent record.
- New settings: `semipy.enableGutterHealth`, `semipy.enableInsightHover`, `semipy.notifyOnResolution`, `semipy.dimGeneratedCode`.

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
