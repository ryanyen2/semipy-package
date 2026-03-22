"""
Apache error-log compiler: formal prefix pipeline + semiformal body parsing.

Demonstrates the semiformal approach to log parsing:
- Formal prefix regex (timestamp, level, body separation)
- Semiformal body classification (event family discovery via ``semi()``)
- Semiformal template generation (regex per family via ``semi()``)
- Formal compiled parser for deterministic batch execution

Uses ``examples/data/Apache_2k.log``.

Run from repo root::

    uv run python examples/apache_log_compiler.py
    uv run python examples/apache_log_compiler.py --fresh
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any

from semipy import configure, semi

_REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOG = _REPO_ROOT / "examples" / "data" / "Apache_2k.log"

APACHE_ERROR_PREFIX = re.compile(
    r"^\[(?P<ts>[^\]]+)\]\s+\[(?P<level>[^\]]+)\]\s+(?P<body>.*)$"
)


def load_lines(path: Path, *, limit: int | None = None) -> list[str]:
    """Load non-empty lines from a log file."""
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return lines[:limit] if limit is not None else lines


def parse_prefix(line: str) -> dict[str, str] | None:
    """Formal prefix parsing: extract timestamp, level, and body."""
    m = APACHE_ERROR_PREFIX.match(line.strip())
    return m.groupdict() if m else None


def extract_bodies(lines: list[str]) -> list[str]:
    """Extract body strings from lines that match the formal prefix."""
    return [p["body"] for ln in lines if (p := parse_prefix(ln)) is not None]


def classify_body(body: str) -> str:
    """Semiformal: classify a single body message into an event family."""
    return semi(
        f"Classify this Apache error log body into a short snake_case event family name: {body}"
    )


def infer_templates(families_and_bodies: dict[str, list[str]]) -> list[dict]:
    """Semiformal: generate regex templates for all families in one pass."""
    families_text = ""
    for fam, fam_bodies in sorted(families_and_bodies.items()):
        samples = "\n".join(f"    - {b}" for b in sorted(set(fam_bodies))[:15])
        families_text += f"\n  {fam}:\n{samples}"

    return semi(
        f"For each event family, create a Python regex that matches the entire body string. "
        f"Use named capture groups for variable parts only (keep stable tokens literal). "
        f"Return a list of dicts with keys: family (str), pattern (str), "
        f"fields (dict mapping group name to type like 'int' or 'str').\n"
        f"Families and example bodies:{families_text}",
        expected_type=list,
    )


class CompiledParser:
    """Deterministic parser built from semiformal template rules."""

    def __init__(self, rules: list[tuple[str, re.Pattern[str], dict[str, str]]]):
        self._rules = rules

    @classmethod
    def from_templates(cls, templates: list[dict]) -> CompiledParser:
        rules = []
        for t in templates:
            pattern = re.compile(t["pattern"])
            fields = t.get("fields", {})
            rules.append((t["family"], pattern, fields))
        return cls(rules)

    def parse_line(self, line: str, **ctx: Any) -> dict[str, Any]:
        prefix = parse_prefix(line)
        if prefix is None:
            return {"status": "PREFIX_FAIL", "raw": line.strip(), **ctx}
        body = prefix["body"]
        result: dict[str, Any] = {
            "ts": prefix["ts"].strip(),
            "level": prefix["level"].strip(),
            "body": body,
            **ctx,
        }
        hits = []
        for fam, pat, fields in self._rules:
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

    def batch_parse(self, lines: list[str], **ctx: Any) -> list[dict[str, Any]]:
        return [self.parse_line(line, **ctx) for line in lines]

    def status_counts(self, events: list[dict[str, Any]]) -> dict[str, int]:
        return dict(Counter(ev.get("status", "?") for ev in events))


def build_parser(bootstrap_lines: list[str]) -> CompiledParser:
    """Full pipeline: classify bodies, group into families, generate templates, compile."""
    bodies = extract_bodies(bootstrap_lines)
    unique_bodies = sorted(set(bodies))

    family_map = {}
    for body in unique_bodies:
        family_map[body] = classify_body(body)

    grouped: dict[str, list[str]] = {}
    for body, fam in family_map.items():
        grouped.setdefault(fam, []).append(body)

    templates = infer_templates(grouped)
    return CompiledParser.from_templates(templates)


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Apache log semiformal compiler demo.")
    parser.add_argument("--fresh", action="store_true", help="Clear cache before running")
    args = parser.parse_args()

    configure(verbose=True)

    if args.fresh:
        import shutil
        cache = Path(".semiformal")
        if cache.exists():
            shutil.rmtree(cache)
            print(f"Cleared {cache}")

    all_lines = load_lines(DEFAULT_LOG)
    bootstrap = all_lines[:120]

    print(f"Log: {DEFAULT_LOG}")
    print(f"Total lines: {len(all_lines)}, Bootstrap: {len(bootstrap)}")

    compiled = build_parser(bootstrap)
    events = compiled.batch_parse(all_lines)
    counts = compiled.status_counts(events)

    print(f"\nParsed {len(all_lines)} lines:")
    for status, count in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"  {status}: {count}")

    unseen = sorted(set(
        ev.get("body", "") for ev in events if ev.get("status") == "UNSEEN_TEMPLATE"
    ))
    if unseen:
        print(f"\nUnseen patterns ({len(unseen)} distinct):")
        for b in unseen[:10]:
            print(f"  {b!r}")


if __name__ == "__main__":
    main()
