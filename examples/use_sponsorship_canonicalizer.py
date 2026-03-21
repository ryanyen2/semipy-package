"""
Sponsorship agreement canonicalizer: IR compilation + formal analytics + one ``semi()`` hook.

- ``@semiformal`` slots emit **dict / list[dict]** (easier to generate reliably); formal methods
  validate into dataclasses with Pydantic ``TypeAdapter``.
- **Formal Python** builds matrices, timelines, gap checks, and optional plots.
- PDF paths on ``self`` are materialized to text inside semipy before slots run.
- A **small** ``semi()`` call runs only when comparing multiple PDFs (see ``comparison_focus_line``).

Run (repo root)::

  uv run python examples/use_sponsorship_canonicalizer.py --max 2
  uv run python examples/use_sponsorship_canonicalizer.py --max 2 --skip-semi
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import pandas as pd
from pydantic import TypeAdapter, ValidationError

from semipy import configure, semiformal, semi

_REPO = Path(__file__).resolve().parents[1]
_DEFAULT_GLOB = str(_REPO / "tests" / "pdf" / "contracts" / "*.pdf")
SESSION_SOURCE = str(Path(__file__).resolve())


@dataclass
class FeeTerm:
    kind: Literal["fixed", "installment", "in_kind", "refund", "reduction", "contingent"]
    amount_text: str
    trigger: str
    due_text: str


@dataclass
class RightGrant:
    kind: Literal[
        "signage",
        "digital",
        "hospitality",
        "naming",
        "booth",
        "social",
        "license",
        "concessions",
    ]
    description: str
    exclusivity_category: str
    clause_anchor: str


@dataclass
class Restriction:
    kind: Literal["approval", "ip_use", "territory", "category_exclusivity", "insurance"]
    description: str
    clause_anchor: str


@dataclass
class Remedy:
    kind: Literal["cure", "termination", "refund", "proration", "suspension"]
    trigger: str
    window_text: str
    effect: str
    clause_anchor: str


@dataclass
class SponsorshipIR:
    parties: list[str]
    counterparty_roles: list[str]
    term_start: str
    term_end: str
    governing_law: str
    dispute_mode: str
    fee_terms: list[FeeTerm]
    rights: list[RightGrant]
    restrictions: list[Restriction]
    remedies: list[Remedy]


@dataclass
class Obligation:
    party: Literal["sponsor", "organizer", "both"]
    action_type: Literal[
        "pay",
        "deliver_right",
        "submit_material",
        "approve",
        "maintain_insurance",
        "cease_use",
    ]
    trigger: str
    due_text: str
    cure_text: str
    consequence: str
    clause_anchor: str


_SPONSORSHIP_IR_TA = TypeAdapter(SponsorshipIR)
_OBL_LIST_TA = TypeAdapter(list[Obligation])

_IR_LIST_KEYS = frozenset(
    {"parties", "counterparty_roles", "fee_terms", "rights", "restrictions", "remedies"}
)


_FEE_KINDS = frozenset(
    {"fixed", "installment", "in_kind", "refund", "reduction", "contingent"}
)
_RIGHT_KINDS = frozenset(
    {
        "signage",
        "digital",
        "hospitality",
        "naming",
        "booth",
        "social",
        "license",
        "concessions",
    }
)
_REST_KINDS = frozenset(
    {"approval", "ip_use", "territory", "category_exclusivity", "insurance"}
)
_REM_KINDS = frozenset({"cure", "termination", "refund", "proration", "suspension"})


def _normalize_ir_payload(raw: dict[str, Any]) -> dict[str, Any]:
    """
    Formal coercion: generated dicts sometimes use {} where lists are required.
    """
    out = dict(raw)
    for k in _IR_LIST_KEYS:
        v = out.get(k)
        if v is None:
            out[k] = []
        elif isinstance(v, list):
            out[k] = v
        elif isinstance(v, dict):
            out[k] = list(v.values()) if v else []
        else:
            out[k] = [v]
    return out


def _keep_rows_with_valid_kind(rows: list[Any], allowed: frozenset[str]) -> list[Any]:
    """Drop list elements whose ``kind`` is missing or not in the closed enum."""
    out: list[Any] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        k = row.get("kind")
        if isinstance(k, str) and k in allowed:
            out.append(row)
    return out


def _sanitize_ir_enums(raw: dict[str, Any]) -> dict[str, Any]:
    """Formal: filter slot noise (empty enum strings) before Pydantic."""
    out = dict(raw)
    out["fee_terms"] = _keep_rows_with_valid_kind(list(out.get("fee_terms") or []), _FEE_KINDS)
    out["rights"] = _keep_rows_with_valid_kind(list(out.get("rights") or []), _RIGHT_KINDS)
    out["restrictions"] = _keep_rows_with_valid_kind(list(out.get("restrictions") or []), _REST_KINDS)
    out["remedies"] = _keep_rows_with_valid_kind(list(out.get("remedies") or []), _REM_KINDS)
    return out


@semiformal
class SponsorshipAgreement:
    """
    Holds ``agreement_text`` initialized with a ``Path`` to a PDF; semipy replaces that
    attribute value with the extracted document **text** before any slot runs. Slots must
    treat ``self.agreement_text`` as a ``str`` body, not as a path.
    """

    def __init__(self, pdf_path: Path, label: str) -> None:
        self.agreement_text: Path | str = pdf_path
        self.label = label

    def rights_kind_counts(self, ir: SponsorshipIR) -> dict[str, int]:
        """Formal: histogram of right kinds."""
        out: dict[str, int] = {}
        for r in ir.rights:
            out[r.kind] = out.get(r.kind, 0) + 1
        return out

    def exclusivity_surface(self, ir: SponsorshipIR) -> float:
        """Formal: share of rights with a non-empty exclusivity category."""
        if not ir.rights:
            return 0.0
        tagged = sum(1 for r in ir.rights if (r.exclusivity_category or "").strip())
        return tagged / len(ir.rights)

    def raw_ir_as_dict(self) -> dict[str, Any]:
        #> Read the agreement from self.agreement_text (a Python str: full PDF body, not a file path).
        #> Assign to ``payload`` a dict with keys: parties, counterparty_roles, term_start, term_end,
        #> governing_law, dispute_mode, fee_terms, rights, restrictions, remedies.
        #> Each of fee_terms, rights, restrictions, remedies must be a list of dicts whose fields
        #> match the dataclasses FeeTerm, RightGrant, Restriction, Remedy. Use only document facts;
        #> use [] or "" where silent. Do not set payload to None.
        return payload  # type: ignore[name-defined]

    def compile_ir(self) -> SponsorshipIR:
        """Formal: normalize and validate slot output into ``SponsorshipIR``."""
        raw = self.raw_ir_as_dict()
        return _SPONSORSHIP_IR_TA.validate_python(
            _sanitize_ir_enums(_normalize_ir_payload(raw))
        )

    def raw_obligations(self, ir: SponsorshipIR) -> list[dict[str, Any]]:
        #> Given canonical IR as ``ir``, build a list of dicts matching Obligation fields:
        #> party, action_type, trigger, due_text, cure_text, consequence, clause_anchor.
        #> Assign the list to ``rows``. Ground rows in the IR; do not invent obligations.
        return rows  # type: ignore[name-defined]

    def infer_obligations(self, ir: SponsorshipIR) -> list[Obligation]:
        """Formal: validate slot rows into ``list[Obligation]``."""
        raw = self.raw_obligations(ir)
        return _OBL_LIST_TA.validate_python(raw)


def build_rights_matrix(labeled: list[tuple[str, SponsorshipIR]]) -> pd.DataFrame:
    kinds = sorted({r.kind for _, ir in labeled for r in ir.rights})
    rows: list[dict[str, object]] = []
    for label, ir in labeled:
        row: dict[str, object] = {"document": label}
        counts = {k: 0 for k in kinds}
        for r in ir.rights:
            counts[r.kind] = counts.get(r.kind, 0) + 1
        row.update(counts)
        rows.append(row)
    return pd.DataFrame(rows)


def build_fee_schedule(labeled: list[tuple[str, SponsorshipIR]]) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for label, ir in labeled:
        for i, ft in enumerate(ir.fee_terms):
            rows.append(
                {
                    "document": label,
                    "idx": i,
                    "kind": ft.kind,
                    "amount_text": ft.amount_text,
                    "trigger": ft.trigger,
                    "due_text": ft.due_text,
                }
            )
    return pd.DataFrame(rows)


def build_obligation_timeline(
    bundles: list[tuple[str, list[Obligation]]],
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for doc, obs in bundles:
        for o in obs:
            rows.append(
                {
                    "document": doc,
                    "party": o.party,
                    "action_type": o.action_type,
                    "trigger": o.trigger,
                    "due_text": o.due_text,
                    "cure_text": o.cure_text,
                    "consequence": o.consequence,
                }
            )
    return pd.DataFrame(rows)


def score_missing_protections(ir: SponsorshipIR) -> list[str]:
    issues: list[str] = []
    if not ir.remedies:
        issues.append("no remedy or cure language surfaced")
    if not any(x.kind == "insurance" for x in ir.restrictions):
        issues.append("insurance restriction not represented")
    if not ir.fee_terms:
        issues.append("no fee terms extracted")
    if not ir.rights:
        issues.append("no rights bundle extracted")
    return issues


def comparison_focus_line(
    labels: list[str],
    fee_df: pd.DataFrame,
    *,
    skip_semi: bool,
) -> str:
    """Formal branching; one ``semi()`` only for multi-doc dashboard title when API enabled."""
    if skip_semi or len(labels) < 2:
        return "Single-document run" if len(labels) < 2 else "Comparison (semi disabled)"
    n_fees = len(fee_df)
    return semi(
        f"Agreement labels: {labels!r}. Total fee-term rows across docs: {n_fees}. "
        f"Return one short comparison focus phrase (max 14 words) for an analyst dashboard title.",
        expected_type=str,
    )


def maybe_plot_rights_coverage(df: pd.DataFrame, out_path: Path) -> None:
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print("(matplotlib not installed; uv sync --extra example)")
        return
    if df.empty or "document" not in df.columns:
        return
    numeric = df.drop(columns=["document"], errors="ignore")
    if numeric.empty:
        return
    sums = numeric.sum(axis=0).sort_values(ascending=True)
    fig, ax = plt.subplots(figsize=(10, max(3.0, 0.35 * len(sums))))
    sums.plot.barh(ax=ax, color="#1f4e79")
    ax.set_xlabel("Rights mentions (sum across processed agreements)")
    ax.set_title("Sponsorship rights coverage (formal aggregate)")
    plt.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    print(f"Wrote {out_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Sponsorship IR + formal tables + optional semi focus")
    parser.add_argument("--glob", default=_DEFAULT_GLOB, help="Glob for PDF paths")
    parser.add_argument("--max", type=int, default=2, help="Max PDFs")
    parser.add_argument("--plot", action="store_true")
    parser.add_argument("--layout-heavy", action="store_true")
    parser.add_argument(
        "--backend",
        choices=("auto", "liteparse", "llama_cloud"),
        default="auto",
    )
    parser.add_argument("--skip-semi", action="store_true")
    args = parser.parse_args()

    configure(
        session_source=SESSION_SOURCE,
        resolution_async_verify=False,
        stream=True,
        console_verbosity="normal",
        console_timeline=True,
        console_show_elapsed=True,
        document_pdf_backend=args.backend,
        document_layout_heavy=args.layout_heavy,
    )

    from glob import glob as stdglob

    paths = sorted(
        Path(p).resolve()
        for p in stdglob(args.glob)
        if str(p).casefold().endswith(".pdf")
    )
    paths = paths[: max(1, args.max)]
    if not paths:
        raise SystemExit("No PDFs matched; add files under tests/pdf/contracts/ or pass --glob")

    labeled_ir: list[tuple[str, SponsorshipIR]] = []
    obligation_bundles: list[tuple[str, list[Obligation]]] = []

    for p in paths:
        label = p.name
        doc = SponsorshipAgreement(p, label)
        try:
            ir = doc.compile_ir()
        except ValidationError as e:
            raise SystemExit(f"Pydantic validation failed for {label} IR: {e}") from e
        _ = doc.rights_kind_counts(ir)
        _ = doc.exclusivity_surface(ir)
        try:
            obs = doc.infer_obligations(ir)
        except ValidationError as e:
            raise SystemExit(f"Pydantic validation failed for {label} obligations: {e}") from e
        labeled_ir.append((label, ir))
        obligation_bundles.append((label, obs))

    rights_df = build_rights_matrix(labeled_ir)
    fee_df = build_fee_schedule(labeled_ir)
    obl_df = build_obligation_timeline(obligation_bundles)

    print("\n=== IR summary (formal counts per document) ===")
    for label, ir in labeled_ir:
        short = label if len(label) <= 72 else label[:69] + "..."
        print(
            f"  {short}\n"
            f"    parties={len(ir.parties)} counterparty_roles={len(ir.counterparty_roles)} "
            f"rights={len(ir.rights)} fee_terms={len(ir.fee_terms)} "
            f"restrictions={len(ir.restrictions)} remedies={len(ir.remedies)}"
        )
    for label, obs in obligation_bundles:
        short = label if len(label) <= 72 else label[:69] + "..."
        print(f"    obligation_rows[{short}]={len(obs)}")

    print("\n=== Rights matrix (formal) ===")
    if rights_df.shape[1] <= 1:
        print("  (no rights kinds extracted across documents; matrix is document-only)")
    print(rights_df.to_string(index=False))
    print("\n=== Fee schedule (formal) ===")
    if fee_df.empty:
        print("  (no fee term rows)")
    else:
        print(fee_df.head(40).to_string(index=False))
    if len(fee_df) > 40:
        print(f"... ({len(fee_df) - 40} more fee rows)")
    print("\n=== Obligations sample (formal) ===")
    if obl_df.empty:
        print("  (no obligation rows)")
    else:
        print(obl_df.head(25).to_string(index=False))
    if len(obl_df) > 25:
        print(f"... ({len(obl_df) - 25} more obligation rows)")

    print("\n=== Formal protection gaps (first document) ===")
    if labeled_ir:
        for msg in score_missing_protections(labeled_ir[0][1]):
            print(f"  - {msg}")

    labels = [t[0] for t in labeled_ir]
    focus = comparison_focus_line(labels, fee_df, skip_semi=args.skip_semi)
    print("\n=== Comparison focus (semi only if multi-doc and not --skip-semi) ===")
    print(focus)

    if args.plot:
        maybe_plot_rights_coverage(
            rights_df,
            _REPO / "examples" / "output" / "sponsorship_rights_coverage.png",
        )

    print(
        f"\nDone: {len(paths)} agreement(s); "
        "slots = raw_ir_as_dict + raw_obligations per document; "
        "formal layer validates and builds tables."
    )


if __name__ == "__main__":
    main()
