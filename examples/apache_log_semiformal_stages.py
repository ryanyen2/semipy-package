"""
Apache error-log compiler -- semiformal staged demo.

Demonstrates two styles of semiformal specification side by side:

- ``classify_body`` uses ``@semiformal`` with a ``#>`` block (STATEMENT_BLOCK).
  The ``#>`` spec is **static** text; runtime variation flows through the ``body``
  parameter. The generated function receives ``(self, body)`` and returns
  ``{"family": str}``.

- ``infer_templates`` uses standalone ``semi()`` (EXPRESSION). The prompt is an
  f-string whose text changes when the set of families changes. A new family set
  produces a **different** ``spec_equivalence_key`` -> a **new slot** -> **GENERATE**.
  The same family set at the same call site -> **REUSE** + verify.

The formal parts (prefix regex, ``CompiledParser``, batch parsing, error reporting)
never invoke the agent. They are ordinary Python.

Run stages::

    cd examples
    uv run python apache_log_semiformal_stages.py --fresh --stage 1
    uv run python apache_log_semiformal_stages.py --stage 2
    uv run python apache_log_semiformal_stages.py --stage 3
    uv run python apache_log_semiformal_stages.py --stage 4
    uv run python apache_log_semiformal_stages.py --stage 5
"""
from __future__ import annotations

import argparse
import re
import shutil
from collections import Counter
from pathlib import Path
from typing import Any

from semipy import configure, semi, semiformal

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOG = _REPO_ROOT / "examples" / "data" / "Apache_2k.log"

APACHE_ERROR_PREFIX = re.compile(
    r"^\[(?P<ts>[^\]]+)\]\s+\[(?P<level>[^\]]+)\]\s+(?P<body>.*)$"
)

# ---------------------------------------------------------------------------
# Formal helpers (no LLM)
# ---------------------------------------------------------------------------


def load_lines(path: Path, *, limit: int | None = None) -> list[str]:
    """Read non-empty lines from a log file."""
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return lines[:limit] if limit is not None else lines


def parse_prefix(line: str) -> dict[str, str] | None:
    """Formal prefix parse: extract timestamp, level, and body."""
    m = APACHE_ERROR_PREFIX.match(line.strip())
    return m.groupdict() if m else None


def extract_bodies(lines: list[str]) -> list[str]:
    """Extract body strings from lines that match the formal prefix."""
    return [p["body"] for ln in lines if (p := parse_prefix(ln)) is not None]


def group_by_family(family_map: dict[str, str]) -> dict[str, list[str]]:
    """Group body -> family map into family -> [bodies]."""
    grouped: dict[str, list[str]] = {}
    for body, fam in family_map.items():
        grouped.setdefault(fam, []).append(body)
    return grouped


class CompiledParser:
    """Deterministic parser built from semiformal template rules."""

    def __init__(self, rules: list[tuple[str, re.Pattern[str], dict[str, str]]]):
        self._rules = rules

    @classmethod
    def from_templates(cls, templates: list[dict]) -> CompiledParser:
        rules = []
        for t in templates:
            pat = re.compile(t["pattern"])
            rules.append((t["family"], pat, t.get("fields", {})))
        return cls(rules)

    def parse_line(self, line: str) -> dict[str, Any]:
        prefix = parse_prefix(line)
        if prefix is None:
            return {"status": "PREFIX_FAIL", "raw": line.strip()}
        body = prefix["body"]
        result: dict[str, Any] = {
            "ts": prefix["ts"].strip(),
            "level": prefix["level"].strip(),
            "body": body,
        }
        hits = []
        for fam, pat, _fields in self._rules:
            m = pat.match(body)
            if m:
                hits.append((fam, m))
        if not hits:
            result["status"] = "UNSEEN_TEMPLATE"
            return result
        if len(hits) > 1:
            result["status"] = "AMBIGUOUS"
            result["candidates"] = [h[0] for h in hits]
            return result
        fam, m = hits[0]
        result["status"] = "OK"
        result["family"] = fam
        result["captures"] = {k: v for k, v in m.groupdict().items() if v is not None}
        return result

    def batch_parse(self, lines: list[str]) -> list[dict[str, Any]]:
        return [self.parse_line(line) for line in lines]

    @staticmethod
    def status_counts(events: list[dict[str, Any]]) -> dict[str, int]:
        return dict(Counter(ev.get("status", "?") for ev in events))


def print_status_report(events: list[dict[str, Any]], *, label: str = "") -> None:
    """Print status counts and any unseen patterns."""
    counts = CompiledParser.status_counts(events)
    header = f"[{label}] " if label else ""
    print(f"\n{header}Parse results ({len(events)} lines):")
    for status, count in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"  {status}: {count}")
    unseen = sorted({ev.get("body", "") for ev in events if ev.get("status") == "UNSEEN_TEMPLATE"})
    if unseen:
        print(f"  ({len(unseen)} distinct unseen patterns)")


# ---------------------------------------------------------------------------
# Semiformal pipeline class
# ---------------------------------------------------------------------------


class ApacheLogPipeline:
    """
    Two inferential specs:
    - ``classify_body``: @semiformal #> block (STATEMENT_BLOCK)
    - ``infer_templates``: standalone semi() call (EXPRESSION)
    """

    @semiformal
    def classify_body(self, body: str) -> str:
        text = "" if body is None else str(body).strip()
        lower = text.lower()
        
        #> Classify this Apache error log body into a short snake_case event family name.
        if not lower:
            return "unknown"
        #> other conditions for other families like worker_init, jk_error, scoreboard_found, etc.
        
        return family  # type: ignore[name-defined]

    def infer_templates(self, families_and_bodies: dict[str, list[str]]) -> list[dict]:
        families_text = ""
        for fam, fam_bodies in sorted(families_and_bodies.items()):
            samples = "\n".join(f"    - {b}" for b in sorted(set(fam_bodies))[:15])
            families_text += f"\n  {fam}:\n{samples}"

        return semi(f"""
            For each event family, create a Python regex that matches the entire body string.
            Use named capture groups for variable parts only (keep stable tokens literal).
            Return a list of dicts with keys: family (str), pattern (str),
            fields (dict mapping group name to type like 'int' or 'str').
            
            Families and example bodies:{families_text}
            """,
            expected_type=list,
        )


# ---------------------------------------------------------------------------
# Stage runners
# ---------------------------------------------------------------------------

def _clear_cache() -> None:
    cache = Path(".semiformal")
    if cache.exists():
        shutil.rmtree(cache)
        print(f"Cleared {cache}")


def _default_edge_lines() -> list[str]:
    return [
        "[Wed Dec 07 12:00:00 2005] [error] [client 192.168.1.100] File does not exist: /var/www/html/favicon.ico",
        "[Wed Dec 07 12:01:00 2005] [crit] (98)Address already in use: make_sock: could not bind to address 0.0.0.0:80",
        "[Wed Dec 07 12:02:00 2005] [warn] mod_ssl: SSL handshake failed for client 10.0.0.5",
        "[Wed Dec 07 12:03:00 2005] [error] [client 172.16.0.1] Invalid URI in request GET /../../etc/passwd HTTP/1.0",
        "[Wed Dec 07 12:04:00 2005] [notice] Apache/2.0.54 configured -- resuming normal operations",
    ]


def run_stage_1(*, log_path: Path = DEFAULT_LOG, bootstrap_n: int = 120) -> dict[str, str]:
    """
    STAGE 1 -- Classify bootstrap bodies.

    First body triggers GENERATE. Remaining bodies REUSE + verify.
    """
    bodies = extract_bodies(load_lines(log_path, limit=bootstrap_n))
    unique = sorted(set(bodies))
    pipeline = ApacheLogPipeline()
    family_map: dict[str, str] = {}

    print(f"[STAGE 1] Classifying {len(unique)} unique bodies (1x generate, rest reuse)...\n")
    for body in unique:
        family_map[body] = pipeline.classify_body(body)

    families = sorted(set(family_map.values()))
    print(f"\n[STAGE 1] {len(families)} families: {families}")
    return family_map


def run_stage_2(family_map: dict[str, str]) -> list[dict]:
    """
    STAGE 2 -- Generate regex templates.

    First call to ``infer_templates`` triggers GENERATE.
    """
    grouped = group_by_family(family_map)
    pipeline = ApacheLogPipeline()

    print(f"[STAGE 2] infer_templates for {len(grouped)} families...\n")
    templates = pipeline.infer_templates(grouped)
    print(f"\n[STAGE 2] {len(templates)} templates generated.")
    for t in templates:
        print(f"  {t.get('family', '?')}: {t.get('pattern', '?')[:60]}...")
    return templates


def run_stage_3(templates: list[dict], *, log_path: Path = DEFAULT_LOG) -> list[dict[str, Any]]:
    """
    STAGE 3 -- Formal parse with narrow parser (no LLM calls).
    """
    compiled = CompiledParser.from_templates(templates)
    events = compiled.batch_parse(load_lines(log_path))
    print_status_report(events, label="STAGE 3")
    return events


def run_stage_4(*, log_path: Path = DEFAULT_LOG, bootstrap_n: int = 120) -> None:
    """
    STAGE 4 -- Extension: bootstrap first, then widen with edge cases.

    Same ``classify_body`` and ``infer_templates``. The second ``infer_templates``
    call has a **different prompt** (more families -> different f-string text ->
    new ``spec_equivalence_key`` -> GENERATE for a new slot).
    """
    edges = _default_edge_lines()
    all_lines = load_lines(log_path)
    pipeline = ApacheLogPipeline()

    print("[STAGE 4a] Narrow bootstrap...\n")
    bodies_boot = extract_bodies(all_lines[:bootstrap_n])
    fm_narrow: dict[str, str] = {}
    for body in sorted(set(bodies_boot)):
        fm_narrow[body] = pipeline.classify_body(body)
    templates_narrow = pipeline.infer_templates(group_by_family(fm_narrow))
    narrow_events = CompiledParser.from_templates(templates_narrow).batch_parse(all_lines)
    print_status_report(narrow_events, label="STAGE 4a narrow")

    print("\n[STAGE 4b] Extended: adding edge cases + full corpus...\n")
    extended_lines = all_lines + edges
    bodies_ext = extract_bodies(extended_lines)
    fm_ext: dict[str, str] = {}
    for body in sorted(set(bodies_ext)):
        fm_ext[body] = pipeline.classify_body(body)
    grouped_ext = group_by_family(fm_ext)

    print(f"[STAGE 4b] infer_templates with {len(grouped_ext)} families...\n")
    templates_ext = pipeline.infer_templates(grouped_ext)

    ext_parser = CompiledParser.from_templates(templates_ext)
    ext_events = ext_parser.batch_parse(extended_lines)
    print_status_report(ext_events, label="STAGE 4b extended")


def run_stage_5(*, log_path: Path = DEFAULT_LOG, bootstrap_n: int = 120) -> None:
    """
    STAGE 5 -- Error reporting: user passes malformed lines.

    The classifier does NOT crash -- it returns some family string. The formal
    parser then reports UNSEEN_TEMPLATE or PREFIX_FAIL. This is the correct
    semiformal contract: the LLM slot does not validate downstream parsing;
    the formal layer reports the mismatch to the user.
    """
    bad_lines = [
        "not a log line at all",
        "random garbage 12345",
        "[Broken timestamp [error] something weird",
        "[Wed Dec 07 12:00:00 2005] [error] ",
    ]
    all_lines = load_lines(log_path)
    pipeline = ApacheLogPipeline()

    bodies_boot = extract_bodies(all_lines[:bootstrap_n])
    fm: dict[str, str] = {}
    for body in sorted(set(bodies_boot)):
        fm[body] = pipeline.classify_body(body)
    templates = pipeline.infer_templates(group_by_family(fm))
    compiled = CompiledParser.from_templates(templates)

    test_lines = all_lines + bad_lines
    events = compiled.batch_parse(test_lines)

    print("\n[STAGE 5] Error lines through formal parser:")
    for line in bad_lines:
        ev = compiled.parse_line(line)
        print(f"  {ev['status']:17s} | {line[:70]}")

    print_status_report(events, label="STAGE 5 full")
    print(
        "\n[STAGE 5] Note: the classifier is NOT re-invoked for error lines."
        "\nThe formal parser handles them deterministically (PREFIX_FAIL / UNSEEN_TEMPLATE)."
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Apache log semiformal staged demo.")
    parser.add_argument("--stage", type=int, choices=(1, 2, 3, 4, 5), default=1)
    parser.add_argument("--fresh", action="store_true", help="Clear .semiformal before running.")
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG)
    args = parser.parse_args()

    configure(verbose=False)
    if args.fresh:
        _clear_cache()

    if args.stage == 1:
        run_stage_1(log_path=args.log)
    elif args.stage == 2:
        fm = run_stage_1(log_path=args.log)
        run_stage_2(fm)
    elif args.stage == 3:
        fm = run_stage_1(log_path=args.log)
        templates = run_stage_2(fm)
        run_stage_3(templates, log_path=args.log)
    elif args.stage == 4:
        run_stage_4(log_path=args.log)
    elif args.stage == 5:
        run_stage_5(log_path=args.log)


if __name__ == "__main__":
    main()
