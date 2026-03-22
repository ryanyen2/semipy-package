"""
Contract obligation and risk-style demo (contrasts with the sponsorship canonicalizer).

**Sponsorship example** (``use_sponsorship_canonicalizer.py``): many small cached
extractors per agreement and formal assembly into one schema.

**This script**: clause graph from one ``#>`` slot, then per-clause ``semi()`` for
labels and risk (many LLM calls per document). Use it to show the difference
between "compiled family-level functions" and "interactive clause loop".

PDF paths on ``self`` are materialized to text inside semipy before slots run.

Run from repo root::

  uv run python examples/use_contract_intelligence.py
  uv run python examples/use_contract_intelligence.py --backend llama_cloud
"""
from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from semipy import configure, semiformal, semi
from semipy.dataclass_utils import coerce_dataclass_list

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


def _normalize_category_label(picked: str) -> str:
    normalized = picked.strip().casefold().replace(" ", "_").replace("-", "_")
    for a in ALLOWED_CATEGORIES:
        if normalized == a.casefold():
            return a
    return "miscellaneous"


def classify_category(c: ClauseRecord) -> str:
    """No deterministic keyword router: category always comes from ``semi()``."""
    picked = semi(
        f"Pick exactly one label from this list (copy spelling): {list(ALLOWED_CATEGORIES)}. "
        f"Heading: {c.heading!r}. Body excerpt: {c.body[:450]!r}.",
        expected_type=str,
    )
    return _normalize_category_label(picked)


def parse_category_filter(csv: str) -> Optional[frozenset[str]]:
    csv = csv.strip()
    if not csv:
        return None
    want = {x.strip().casefold().replace(" ", "_") for x in csv.split(",") if x.strip()}
    chosen = {a for a in ALLOWED_CATEGORIES if a.casefold() in want}
    return frozenset(chosen) if chosen else None


@semiformal
class ContractRun:
    def __init__(self, document_path: Path, jurisdiction: str) -> None:
        self.document_path = document_path.resolve()
        self.jurisdiction = jurisdiction
        #> Split the agreement text into ClauseRecord rows: clause_id (stable c0001-style),
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
    ) -> list[RiskRow]:
        clauses = coerce_dataclass_list(list(self.clauses), ClauseRecord)
        rows: list[RiskRow] = []
        for c in clauses:
            cat = classify_category(c)
            row = self.score(c, cat)
            if row.risk_score >= min_risk:
                rows.append(row)
        rows.sort(key=lambda r: (-r.risk_score, r.clause_id))
        return rows


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Clause loop + semi() classification (contrast with sponsorship canonicalizer).",
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
        "--min-risk",
        type=float,
        default=None,
        help="Override semi()-chosen threshold",
    )

    args = parser.parse_args()
    doc_path = Path(args.document).resolve()

    configure(session_source=SESSION_SOURCE)

    jurisdiction = (args.jurisdiction or "Delaware").strip()

    min_risk = (
        float(args.min_risk)
        if args.min_risk is not None
        else semi(
            f"Minimum risk_score in [1.0, 5.0] for this executive summary "
            f"(jurisdiction context {jurisdiction!r}). Return float.",
            expected_type=float,
        )
    )

    run = ContractRun(doc_path, jurisdiction)
    rows = run.matrix(min_risk)

    print(f"Document: {doc_path}")
    print(
        f"Jurisdiction={jurisdiction!r} min_risk={min_risk} "
        f"clauses={len(run.clauses)} shown={len(rows)}"
    )
    for r in rows[:25]:
        print(f"  [{r.risk_score}] {r.category} | {r.clause_id} | {r.summary}")
    if len(rows) > 25:
        print(f"  ... ({len(rows) - 25} more)")


if __name__ == "__main__":
    main()
    # example command:
    # uv run python examples/use_contract_intelligence.py --jurisdiction "Delaware" --min-risk 3.0
