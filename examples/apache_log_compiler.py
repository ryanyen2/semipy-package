"""
Apache error-log compiler demo: formal prefix pipeline + one compile-time ``semi()`` slot
that infers reusable regex templates for message *bodies*, then frozen deterministic parsing
for large batches (no LLM per line).

Uses ``examples/data/Apache_2k.log``. Bootstrap uses an early slice (mostly mod_jk / jk2 /
workerEnv); later lines such as ``Directory index forbidden`` should surface as
``UNSEEN_TEMPLATE`` until you widen bootstrap or recompile with richer context.

Run from repo root::

  uv run python examples/apache_log_compiler.py
  uv run python examples/apache_log_compiler.py --fresh
"""
from __future__ import annotations

import argparse
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, cast

from semipy import configure, semiformal, semi
from semipy.agents.config import get_config
from semipy.session_anchor import resolve_portal_anchor
from semipy.types import session_id_from_filename, session_module_name_from_filename

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_LOG = _REPO_ROOT / "examples" / "data" / "Apache_2k.log"
SESSION_SOURCE = str(Path(__file__).resolve())

# Formal: common Apache error_log prefix — timestamp and level are preserved verbatim.
APACHE_ERROR_PREFIX = re.compile(
    r"""
    ^\[
      (?P<ts>[^\]]+)
    \]\s+
    \[
      (?P<level>[^\]]+)
    \]\s+
    (?P<body>.*)$
    """,
    re.VERBOSE,
)


@dataclass
class BodyTemplateRule:
    """One body-level template: full-body regex with named groups + coercion hints."""

    id: str
    pattern: str
    fields: dict[str, str]


@dataclass
class ApacheBodyGrammarSpec:
    """Deterministic bundle inferred at compile time (via ``semi()``), executed formally below."""

    templates: list[BodyTemplateRule]
    fallback: str


def _coerce_capture(raw: str, kind: str) -> Any:
    k = (kind or "str").strip().lower()
    if k == "int":
        return int(raw)
    if k in ("path", "str"):
        return raw
    return raw


def _split_prefix_lines(lines: Iterable[str]) -> tuple[list[dict[str, str]], list[str]]:
    """Formal stage: prefix match only; returns matched dict rows + raw lines that failed."""
    parsed: list[dict[str, str]] = []
    bad: list[str] = []
    for line in lines:
        s = line.strip()
        if not s:
            continue
        m = APACHE_ERROR_PREFIX.match(s)
        if not m:
            bad.append(s)
            continue
        parsed.append(m.groupdict())
    return parsed, bad


def _unique_bodies_for_prompt(bodies: list[str], *, max_lines: int) -> str:
    uniq = sorted(set(bodies))
    shown = uniq[:max_lines]
    lines = "\n".join(f"  - {b}" for b in shown)
    if len(uniq) > max_lines:
        lines += f"\n  ... ({len(uniq) - max_lines} more distinct bodies omitted)"
    return lines


def _bootstrap_bodies_exhaustively_matched(
    compiled: ApacheCompiledParser,
    bodies: list[str],
) -> tuple[bool, list[str]]:
    """Formal check: every bootstrap body must match exactly one template (no ambiguity)."""
    misses: list[str] = []
    for body in sorted(set(bodies)):
        hits = 0
        for _tid, cre, _fields in compiled._rules:
            if cre.match(body):
                hits += 1
        if hits != 1:
            misses.append(body)
    return (not misses, misses)


def _grammar_from_slot_payload(raw: Any) -> ApacheBodyGrammarSpec:
    """
    Normalize slot output: generation often returns plain dicts/lists even when prompts ask
    for dataclass-shaped trees; keep parsing strict in formal code.
    """
    if isinstance(raw, ApacheBodyGrammarSpec):
        return raw
    if not isinstance(raw, Mapping):
        raise TypeError(f"grammar spec must be mapping or ApacheBodyGrammarSpec, got {type(raw)!r}")
    data = cast(Mapping[str, Any], raw)
    fb = str(data.get("fallback") or "UNSEEN_TEMPLATE").strip()
    templates_in = data.get("templates")
    if not isinstance(templates_in, list):
        raise TypeError("grammar.templates must be a list")
    rules: list[BodyTemplateRule] = []
    for i, item in enumerate(templates_in):
        if isinstance(item, BodyTemplateRule):
            rules.append(item)
            continue
        if not isinstance(item, Mapping):
            raise TypeError(f"templates[{i}] must be mapping or BodyTemplateRule")
        m = cast(Mapping[str, Any], item)
        tid = str(m.get("id") or f"template_{i}")
        pat = str(m.get("pattern") or "")
        fields_raw = m.get("fields")
        fields: dict[str, str] = {}
        if isinstance(fields_raw, Mapping):
            for k, v in cast(Mapping[str, Any], fields_raw).items():
                fields[str(k)] = str(v)
        rules.append(BodyTemplateRule(id=tid, pattern=pat, fields=fields))
    return ApacheBodyGrammarSpec(templates=rules, fallback=fb)


class ApacheCompiledParser:
    """Runtime artifact: try templates in order; conservative ambiguity and unseen handling."""

    def __init__(self, spec: ApacheBodyGrammarSpec) -> None:
        spec = _grammar_from_slot_payload(spec)
        self._fallback = (spec.fallback or "UNSEEN_TEMPLATE").strip()
        self._rules: list[tuple[str, re.Pattern[str], dict[str, str]]] = []
        for t in spec.templates:
            try:
                cre = re.compile(t.pattern)
            except re.error as e:
                raise ValueError(
                    f"Invalid regex for template {t.id!r}: {e}\n"
                    f"  pattern={t.pattern!r}"
                ) from e
            self._rules.append((t.id, cre, dict(t.fields)))

    def parse(self, line: str, **runtime_context: Any) -> dict[str, Any]:
        """
        Parse one raw log line. ``runtime_context`` (e.g. host=...) is copied into the
        event and does not affect compile-time ``semi()`` — same parser, different deployment labels.
        """
        s = line.strip()
        m = APACHE_ERROR_PREFIX.match(s)
        if not m:
            out: dict[str, Any] = {
                "status": "PREFIX_PARSE_FAIL",
                "raw": s,
            }
            out.update(runtime_context)
            return out
        ts = m.group("ts").strip()
        level = m.group("level").strip()
        body = m.group("body")
        hits: list[tuple[str, re.Match[str]]] = []
        for tid, cre, fields in self._rules:
            mm = cre.match(body)
            if mm:
                hits.append((tid, mm))
        base: dict[str, Any] = {
            "ts": ts,
            "level": level,
            "body": body,
        }
        base.update(runtime_context)
        if len(hits) > 1:
            base["status"] = "AMBIGUOUS_PARSE"
            base["candidates"] = [h[0] for h in hits]
            return base
        if not hits:
            base["status"] = self._fallback
            return base
        tid, mm = hits[0]
        base["status"] = "OK"
        base["template_id"] = tid
        caps = mm.groupdict()
        typed: dict[str, Any] = {}
        rule_fields: dict[str, str] = {}
        for rid, _cre, fields in self._rules:
            if rid == tid:
                rule_fields = fields
                break
        for name, raw_val in caps.items():
            if raw_val is None:
                continue
            kind = rule_fields.get(name, "str")
            typed[name] = _coerce_capture(raw_val, kind)
        base["captures"] = typed
        return base



def compile_apache_event_parser(
    sample_lines: list[str],
    module_profile: tuple[str, ...],
) -> ApacheCompiledParser:
    """
    Formal ingest + prefix split, then a single ``semi()`` call to infer body grammar from
    bootstrap bodies only (not from the live stream line-by-line).
    """
    parsed, prefix_failures = _split_prefix_lines(sample_lines)
    if prefix_failures:
        # Keep compile-time prompt honest: model sees only lines that matched the formal prefix.
        pass
    bodies = [row["body"] for row in parsed]
    bodies_preview = _unique_bodies_for_prompt(bodies, max_lines=100)
    modules_txt = ", ".join(module_profile) if module_profile else "(unspecified)"

    grammar = semi(
        f"""
        Infer deterministic parsing rules for Apache error-log *message bodies* only.
        Timestamp and severity are parsed separately by formal code; do not duplicate them.

        Deployment module context (compile-time): {modules_txt}

        Observed distinct message bodies from a local bootstrap slice (one line per body):
        {bodies_preview}

        Invariant:
        - Group bodies that describe the same operational event into the same template.
        - Keep stable operational tokens as literal regex text.
        - Use named capture groups only for true variable spans. Map each capture name to a
          coercion label in fields: "int", "path", or "str".
        - Each template pattern must match the *entire* body string (anchor appropriately).
        - Prefer specific templates over catch-alls.
        - Order templates from most specific to more general where ambiguity could occur.
        - Coverage: every distinct body string shown in the bootstrap list must be matched by
          exactly one template (100%% coverage on that set). Do not drop families when new lines
          appear; extend templates, do not replace working shapes with looser guesses.
        - fallback must be exactly the string UNSEEN_TEMPLATE (used when no template matches).

        Return value (Python shape):
        - A dict with keys "templates" and "fallback".
        - "templates" is a list of dicts, each with keys: "id" (str), "pattern" (str),
          "fields" (dict[str, str] mapping capture name to "int"|"path"|"str").
        - "fallback" is the string UNSEEN_TEMPLATE.
        No prose, no markdown fences — executable data only.
        """,
        # Plain dict validates reliably in the agent pipeline; normalize to dataclasses above.
        expected_type=dict,
    )
    return ApacheCompiledParser(_grammar_from_slot_payload(grammar))


def _clear_session_cache() -> None:
    cache_dir = get_config().cache_dir
    anchor = resolve_portal_anchor(SESSION_SOURCE)
    sid = session_id_from_filename(anchor)
    mod = session_module_name_from_filename(anchor)
    portal = cache_dir / f"{sid}.portal.json"
    dispatch = cache_dir / "runtime" / f"{mod}.semi.py"
    for p in (portal, dispatch):
        if p.is_file():
            p.unlink()


def _load_lines(path: Path, *, limit: Optional[int]) -> list[str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if limit is not None:
        return lines[:limit]
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(description="Apache log semiformal compiler demo.")
    parser.add_argument(
        "--log",
        type=Path,
        default=_DEFAULT_LOG,
        help="Path to Apache error log",
    )
    parser.add_argument(
        "--bootstrap",
        type=int,
        default=120,
        help="First N lines used only for compile-time grammar inference",
    )
    parser.add_argument(
        "--parse-limit",
        type=int,
        default=2000,
        help="Max lines to run through the compiled deterministic parser",
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Remove this example's portal + dispatch module before running",
    )
    parser.add_argument(
        "--recompile-wide",
        action="store_true",
        help=(
            "Second semi() compile with a wider bootstrap (includes more body shapes). "
            "Useful to inspect ADAPT/GENERATE; validate metrics yourself — LLM output varies."
        ),
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Turn off semipy stream UI (same cache behavior)",
    )
    args = parser.parse_args()

    configure(session_source=SESSION_SOURCE, verbose=not args.quiet)

    if args.fresh:
        _clear_session_cache()
        print(f"Cleared session cache for {SESSION_SOURCE!r}")

    log_path = args.log.resolve()
    all_lines = _load_lines(log_path, limit=None)
    bootstrap = _load_lines(log_path, limit=args.bootstrap)

    print(f"Log file: {log_path}")
    print(f"Total non-empty lines: {len(all_lines)}")
    print(f"Bootstrap lines for semi(): {len(bootstrap)}")

    profile = ("mod_jk", "workerEnv", "jk2")
    print(f"compile_apache_event_parser(...)  module_profile={profile}")
    compiled = compile_apache_event_parser(bootstrap, profile)
    _parsed_boot, _ = _split_prefix_lines(bootstrap)
    boot_bodies = [r["body"] for r in _parsed_boot]
    ok_cov, miss = _bootstrap_bodies_exhaustively_matched(compiled, boot_bodies)
    if not ok_cov:
        print(
            "WARNING: formal bootstrap coverage check failed (0 or 2+ template hits for a body). "
            "Sample misses:",
        )
        for b in miss[:5]:
            print(f"  {b!r}")
        print("Try --fresh to regenerate, or adjust bootstrap size / deployment context.")

    to_parse = all_lines[: args.parse_limit]
    c_host_a = Counter()
    c_host_b = Counter()
    samples_unseen: list[str] = []

    for ln in to_parse:
        ev = compiled.parse(ln, host="web-01")
        c_host_a[ev.get("status", "?")] += 1
        if ev.get("status") == "UNSEEN_TEMPLATE" and len(samples_unseen) < 5:
            samples_unseen.append(ev.get("body", "")[:200])

    for ln in to_parse:
        ev = compiled.parse(ln, host="web-02")
        c_host_b[ev.get("status", "?")] += 1

    print("\n--- Deterministic batch (same compiled parser; host label varies) ---")
    print(f"host=web-01 status counts: {dict(c_host_a)}")
    print(f"host=web-02 status counts: {dict(c_host_b)}")
    print(f"Compiled parser template rules: {len(compiled._rules)}")
    if samples_unseen:
        print("\nSample bodies marked UNSEEN_TEMPLATE (new families vs narrow bootstrap):")
        for s in samples_unseen:
            print(f"  {s!r}")

    if args.recompile_wide:
        profile_b = ("mod_jk", "workerEnv", "jk2", "mod_ssl")
        wide_bootstrap = all_lines[: min(250, len(all_lines))]
        print(
            f"\nOptional recompile: wider bootstrap ({len(wide_bootstrap)} lines) + "
            f"module_profile={profile_b}"
        )
        compiled_b = compile_apache_event_parser(wide_bootstrap, profile_b)
        print(f"Wide parser template rules: {len(compiled_b._rules)}")
        wbodies = [r["body"] for r in _split_prefix_lines(wide_bootstrap)[0]]
        wok, wmiss = _bootstrap_bodies_exhaustively_matched(compiled_b, wbodies)
        print(f"Wide bootstrap formal coverage ok={wok} miss_count={len(wmiss)}")
        c_after = Counter()
        for ln in to_parse:
            c_after[compiled_b.parse(ln, host="web-01").get("status", "?")] += 1
        print(f"Full-batch status counts with wide parser: {dict(c_after)}")

    # Optional: show where dispatch lives for inspection
    anchor = resolve_portal_anchor(SESSION_SOURCE)
    sid = session_id_from_filename(anchor)
    mod = session_module_name_from_filename(anchor)
    dispatch = get_config().cache_dir / "runtime" / f"{mod}.semi.py"
    print(f"\nDispatch module (generated implementations): {dispatch}")
    if dispatch.is_file():
        print(f"Dispatch file size: {dispatch.stat().st_size} bytes")
        tail = dispatch.read_text(encoding="utf-8", errors="replace").splitlines()[-15:]
        print("Last lines of dispatch module:")
        for tln in tail:
            print(f"  {tln}")


if __name__ == "__main__":
    main()
