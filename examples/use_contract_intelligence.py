"""
Legal contract processing demo: mostly formal Python (fixed schema, allowed labels,
control flow), with semipy handling PDF text via ``load_document_text`` and runtime
inference only where the program cannot be closed over specs (``#>`` slots and
standalone ``semi()`` calls).

This is intentionally unlike ``use_visualization_builder.py``: informal regions are
narrow, typed, and cached as functions — not whole-method vibes.

Run from repo root::

  uv run python examples/use_contract_intelligence.py
  uv run python examples/use_contract_intelligence.py --backend llama_cloud
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from semipy import configure, semiformal, semi
from semipy.documents import load_document_text

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_PDF = (
    _REPO_ROOT
    / "tests"
    / "pdf"
    / "contracts"
    / "FreezeTagInc_20180411_8-K_EX-10.1_11139603_EX-10.1_Sponsorship Agreement.pdf"
)

SESSION_SOURCE = str(Path(__file__).resolve())

ALLOWED_CATEGORIES: tuple[str, ...] = (
    "confidentiality",
    "governing_law",
    "indemnity",
    "liability",
    "termination",
    "payment",
    "intellectual_property",
    "representations",
    "miscellaneous",
)


@dataclass(frozen=True)
class ClauseRecord:
    clause_id: str
    heading: str
    body: str
    section_ref: str


@dataclass
class RiskRow:
    clause_id: str
    category: str
    risk_score: float
    summary: str
    jurisdiction_note: Optional[str]


def _formal_category(c: ClauseRecord) -> Optional[str]:
    blob = f"{c.heading}\n{c.body}".casefold()
    if "governing law" in blob or "choice of law" in blob:
        return "governing_law"
    if "confidential" in blob or "confidentiality" in blob:
        return "confidentiality"
    if "indemnif" in blob:
        return "indemnity"
    if "limitation of liability" in blob or "consequential" in blob:
        return "liability"
    if "termination" in blob or "term and termination" in blob:
        return "termination"
    if "fee" in blob or "payment" in blob or "compensation" in blob:
        return "payment"
    return None


def classify_category(c: ClauseRecord) -> str:
    """Formal rules first; otherwise a single expression slot with a closed label set."""
    hit = _formal_category(c)
    if hit is not None:
        return hit
    picked = semi(
        f"Pick exactly one label from this list (copy spelling): {list(ALLOWED_CATEGORIES)}. "
        f"Heading: {c.heading!r}. Body excerpt: {c.body[:450]!r}.",
        expected_type=str,
    )
    normalized = picked.strip().casefold().replace(" ", "_").replace("-", "_")
    for a in ALLOWED_CATEGORIES:
        if normalized == a.casefold():
            return a
    return "miscellaneous"


def parse_category_filter(csv: str) -> Optional[frozenset[str]]:
    csv = csv.strip()
    if not csv:
        return None
    want = {x.strip().casefold().replace(" ", "_") for x in csv.split(",") if x.strip()}
    chosen = {a for a in ALLOWED_CATEGORIES if a.casefold() in want}
    return frozenset(chosen) if chosen else None


@semiformal
class ContractRun:
    def __init__(self, raw_text: str, jurisdiction: str) -> None:
        self.jurisdiction = jurisdiction
        #> Split raw_text into ClauseRecord rows: clause_id (stable c0001-style),
        #> heading (title line), body (operative text), section_ref (best-effort section label).
        self.clauses = clauses

    def score(self, clause: ClauseRecord, category: str) -> RiskRow:
        if category == "governing_law":
            return RiskRow(
                clause_id=clause.clause_id,
                category=category,
                risk_score=1.0,
                summary="Governing law — informational for register.",
                jurisdiction_note=None,
            )
        #> clause body {clause.body!r}, category {category!r}, jurisdiction {self.jurisdiction!r}:
        #> risk_score in [1.0,5.0] step 0.5; one-sentence summary; jurisdiction_note or None.
        assert 1.0 <= risk_score <= 5.0
        return RiskRow(
            clause_id=clause.clause_id,
            category=category,
            risk_score=risk_score,
            summary=summary,
            jurisdiction_note=jurisdiction_note,
        )

    def matrix(
        self,
        min_risk: float,
        category_filter: Optional[frozenset[str]],
    ) -> list[RiskRow]:
        rows: list[RiskRow] = []
        for c in self.clauses:
            cat = classify_category(c)
            if category_filter is not None and cat not in category_filter:
                continue
            row = self.score(c, cat)
            if row.risk_score >= min_risk:
                rows.append(row)
        rows.sort(key=lambda r: (-r.risk_score, r.clause_id))
        return rows


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Formal contract pipeline with semipy PDF I/O and selective inference",
    )
    parser.add_argument(
        "document",
        nargs="?",
        default=str(_DEFAULT_PDF),
        type=str,
        help="Path to .pdf or text file",
    )
    parser.add_argument("--jurisdiction", default="Delaware")
    parser.add_argument(
        "--layout-heavy",
        action="store_true",
        help="Prefer LlamaCloud agentic parsing for PDF (figures / scans / layout)",
    )
    parser.add_argument(
        "--backend",
        choices=("auto", "liteparse", "llama_cloud"),
        default="auto",
        help="PDF backend (see semipy.documents.load_document_text)",
    )
    parser.add_argument(
        "--min-risk",
        type=float,
        default=None,
        help="Skip semi() threshold; use this value directly",
    )
    parser.add_argument(
        "--skip-semi",
        action="store_true",
        help="Skip all standalone semi() calls (fixed defaults for demos without API)",
    )
    args = parser.parse_args()
    doc_path = Path(args.document).resolve()

    configure(
        session_source=SESSION_SOURCE,
        resolution_async_verify=False,
        stream=True,
        console_verbosity="normal",
        console_timeline=True,
        console_show_elapsed=True,
    )

    jurisdiction = (args.jurisdiction or "Delaware").strip()

    if args.skip_semi:
        min_risk = 3.0 if args.min_risk is None else float(args.min_risk)
        raw_filter = ""
        headline = "Contract risk run (semi disabled)"
    else:
        min_risk = (
            float(args.min_risk)
            if args.min_risk is not None
            else semi(
                f"Minimum risk_score in [1.0, 5.0] for this executive summary "
                f"(jurisdiction context {jurisdiction!r}). Return float.",
                expected_type=float,
            )
        )
        raw_filter = semi(
            f"Comma-separated subset of {list(ALLOWED_CATEGORIES)} to include, or empty for all. "
            f"Jurisdiction {jurisdiction!r}.",
            expected_type=str,
        )
        headline = semi(
            f"One line (max 100 chars) describing this run for {doc_path.name!r}.",
            expected_type=str,
        )

    filt = parse_category_filter(raw_filter) if not args.skip_semi else None
    if args.skip_semi:
        filt = None

    text = load_document_text(
        doc_path,
        backend=args.backend,
        layout_heavy=args.layout_heavy,
    )
    run = ContractRun(text, jurisdiction)
    rows = run.matrix(min_risk, filt)

    print(headline if not args.skip_semi else "Contract risk run")
    print(f"Document: {doc_path}")
    print(f"Jurisdiction={jurisdiction!r} min_risk={min_risk} clauses={len(run.clauses)} shown={len(rows)}")
    for r in rows[:25]:
        print(f"  [{r.risk_score}] {r.category} | {r.clause_id} | {r.summary}")
    if len(rows) > 25:
        print(f"  ... ({len(rows) - 25} more)")


if __name__ == "__main__":
    main()
