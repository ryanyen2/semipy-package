"""
Normalize heterogeneous FASTA-style header lines into a conservative metadata table.

Formal code reads sequence records, fingerprints delimiter families, and runs compiled
parsers. ``@semiformal`` compiles *family grammars* once per distinct header shape; later
batches reuse the same bundle without recompilation.

Uses ``examples/data/h3n2_ha.fasta`` (pipe-delimited influenza headers) and
``examples/data/CikA_GAF.hmm`` (HMMER profile header metadata as a second structured source).

Run from repo root::

  uv run python examples/fasta_header_metadata.py

Clear this example's portal and dispatch module, then rerun::

  rm -f .semiformal/runtime/fasta_header_metadata.semi.py \\
        .semiformal/*fasta_header_metadata*.portal.json
  uv run python examples/fasta_header_metadata.py
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from semipy import configure, semiformal, semi

_REPO = Path(__file__).resolve().parents[1]
FASTA_PATH = _REPO / "examples" / "data" / "h3n2_ha.fasta"
HMM_PATH = _REPO / "examples" / "data" / "CikA_GAF.hmm"
SESSION_SOURCE = str(Path(__file__).resolve())

INVARIANT_TAG = "inv=2025-03-21-a"

# Formal fallback when compiled indices are invalid (same file layout as examples/data/h3n2_ha.fasta).
_FLU_PIPE13_REF: dict[str, int] = {
    "strain_name": 0,
    "subtype": 1,
    "accession": 2,
    "collection_date": 3,
    "host": 9,
    "region": 5,
    "country": 6,
    "city": 8,
}


def _roles_usable(role_to_index: dict[str, int]) -> bool:
    acc = role_to_index.get("accession", -1)
    sn = role_to_index.get("strain_name", -1)
    return isinstance(acc, int) and isinstance(sn, int) and acc >= 0 and sn >= 0


def _flu_roles_match_tokens(tokens: list[str], role_to_index: dict[str, int]) -> bool:
    """Reject index maps that misplace lineage, dates, or accessions."""
    if not _roles_usable(role_to_index):
        return False
    si = role_to_index.get("strain_name", -1)
    ai = role_to_index.get("accession", -1)
    if si < 0 or si >= len(tokens) or ai < 0 or ai >= len(tokens):
        return False
    strain = tokens[si].strip()
    if "/" not in strain:
        return False
    epi_idxs = [i for i, t in enumerate(tokens) if re.match(r"^EPI\d+$", t.strip(), re.IGNORECASE)]
    if epi_idxs:
        return ai == epi_idxs[0]
    acc = tokens[ai].strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", acc):
        return False
    if len(acc) < 4:
        return False
    return True


def read_fasta_headers(path: Path) -> list[tuple[str, str]]:
    """Return (sequence_id, header_without_gt) for each record; sequence lines are skipped."""
    rows: list[tuple[str, str]] = []
    current_id = ""
    header_rest = ""
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line:
            continue
        if line.startswith(">"):
            if current_id:
                rows.append((current_id, header_rest))
            rest = line[1:].strip()
            first = rest.split(maxsplit=1)[0]
            current_id = first
            header_rest = rest
        elif current_id:
            rows.append((current_id, header_rest))
            current_id = ""
            header_rest = ""
    if current_id:
        rows.append((current_id, header_rest))
    return rows


def fingerprint_header(header_line: str) -> str:
    """Stable family key from structure only (not accession text)."""
    body = header_line.strip()
    if body.startswith(">"):
        body = body[1:]
    pipe = body.count("|")
    if "|" in body:
        return f"pipe_{pipe}"
    if re.match(r"^\S+\s+.+$", body.strip()):
        return "space_desc"
    return "unknown"


def parse_hmm_metadata(path: Path) -> dict[str, str]:
    """Formal extraction of HMMER3 profile header fields (not GenBank, but same workflow role)."""
    text = path.read_text(encoding="utf-8", errors="replace")
    out: dict[str, str] = {}
    for line in text.splitlines():
        if line.startswith("NAME"):
            out["name"] = line.split(None, 1)[1].strip() if len(line.split()) > 1 else ""
        elif line.startswith("ACC"):
            out["accession"] = line.split(None, 1)[1].strip() if len(line.split()) > 1 else ""
        elif line.startswith("DESC"):
            out["description"] = line.split(None, 1)[1].strip() if len(line.split()) > 1 else ""
        if len(out) >= 3:
            break
    return out


_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_PARTIAL_DATE = re.compile(r"^\d{4}(-\d{2}-XX|-XX-XX)$", re.IGNORECASE)
_AMBIG_SLASH = re.compile(r"^\d{1,2}/\d{1,2}/\d{4}$")


def normalize_date(raw: str, policy: str) -> tuple[str, str]:
    """Return (normalized_or_empty, status_ok_or_ambiguous)."""
    s = (raw or "").strip()
    if not s or s == "?":
        return "", "OK"
    if _PARTIAL_DATE.match(s):
        return s.upper(), "AMBIGUOUS_DATE"
    if _AMBIG_SLASH.match(s):
        return s, "AMBIGUOUS_DATE"
    if _ISO_DATE.match(s):
        return s, "OK"
    return s, "VALIDATION_FAILED"


@dataclass
class HeaderRow:
    sequence_id: str
    accession: str
    strain_name: str
    host: str
    location: str
    collection_date: str
    subtype: str
    raw_header: str
    status: str
    detail: str


@dataclass
class PipeFamilyBundle:
    """Frozen parser artifact for one delimiter family."""

    family_key: str
    delimiter: str
    role_to_index: dict[str, int]
    date_policy: str


def _empty_row(raw: str, status: str, detail: str) -> HeaderRow:
    return HeaderRow(
        sequence_id="",
        accession="",
        strain_name="",
        host="",
        location="",
        collection_date="",
        subtype="",
        raw_header=raw,
        status=status,
        detail=detail,
    )


def apply_pipe_bundle(header_line: str, bundle: PipeFamilyBundle) -> HeaderRow:
    """Deterministic parse using a compiled family bundle."""
    raw = header_line.strip()
    if not raw.startswith(">"):
        return _empty_row(raw, "BAD_HEADER", "missing_gt")
    body = raw[1:]
    parts = body.split(bundle.delimiter)
    idx = bundle.role_to_index

    def tok(name: str) -> str:
        i = idx.get(name, -1)
        if i < 0 or i >= len(parts):
            return ""
        v = parts[i].strip()
        if v in ("?", ""):
            return ""
        return v

    strain = tok("strain_name")
    acc = tok("accession")
    loc_bits = [tok(n) for n in ("region", "country", "city") if idx.get(n, -1) >= 0]
    loc = " / ".join(x for x in loc_bits if x)

    date_raw = tok("collection_date")
    norm_date, date_st = normalize_date(date_raw, bundle.date_policy)
    if date_st != "OK":
        detail = date_st
        status = date_st
    else:
        detail = ""
        status = "OK"

    seq_id = strain or body.split()[0] if body else ""
    return HeaderRow(
        sequence_id=seq_id,
        accession=acc,
        strain_name=strain,
        host=tok("host"),
        location=loc or tok("location"),
        collection_date=norm_date,
        subtype=tok("subtype"),
        raw_header=raw,
        status=status,
        detail=detail or "",
    )


@dataclass
class CompactPipeBundle:
    family_key: str
    role_to_index: dict[str, int]
    date_policy: str


def apply_compact_pipe(header_line: str, bundle: CompactPipeBundle) -> HeaderRow:
    raw = header_line.strip()
    if not raw.startswith(">"):
        return _empty_row(raw, "BAD_HEADER", "missing_gt")
    parts = raw[1:].split("|")
    idx = bundle.role_to_index

    def tok(name: str) -> str:
        i = idx.get(name, -1)
        if i < 0 or i >= len(parts):
            return ""
        return parts[i].strip()

    date_raw = tok("collection_date")
    norm_date, date_st = normalize_date(date_raw, bundle.date_policy)
    status = date_st if date_st != "OK" else "OK"
    loc = tok("location")
    return HeaderRow(
        sequence_id=tok("strain_name"),
        accession=tok("accession"),
        strain_name=tok("strain_name"),
        host="",
        location=loc,
        collection_date=norm_date,
        subtype="",
        raw_header=raw,
        status=status,
        detail="" if status == "OK" else status,
    )


def apply_space_accession(header_line: str) -> HeaderRow:
    """Formal parse for ``>ACCESSION remainder`` (no pipes)."""
    raw = header_line.strip()
    if not raw.startswith(">"):
        return _empty_row(raw, "BAD_HEADER", "missing_gt")
    rest = raw[1:].strip()
    m = re.match(r"^(\S+)\s+(.+)$", rest)
    if not m:
        return _empty_row(raw, "VALIDATION_FAILED", "space_pattern")
    acc, desc = m.group(1), m.group(2)
    return HeaderRow(
        sequence_id=acc,
        accession=acc,
        strain_name=desc,
        host="",
        location="",
        collection_date="",
        subtype="",
        raw_header=raw,
        status="OK",
        detail="",
    )


@semiformal
class FluPipeCompiler:
    """Compile-time inference for the 14-field influenza pipe family."""

    def __init__(self, bootstrap_headers: list[str]) -> None:
        self.bootstrap_headers = bootstrap_headers

    def role_map(self) -> dict[str, int]:
        for header in self.bootstrap_headers:
            tokens = header.lstrip(">").split("|")
            role_to_index = ... #> map the sequence of tokens based on their category
        
        return role_to_index

    def date_policy(self) -> str:
        #> One line: preferred date interpretation for this family (ISO vs partial vs flag ambiguous).
        #> Invariant tag: inv=2025-03-21-a.
        return policy  # type: ignore[name-defined]


@semiformal
class CompactPipeCompiler:
    """Second lab style: short pipe records (accession position differs from flu)."""

    def __init__(self, bootstrap_headers: list[str]) -> None:
        self.bootstrap_headers = bootstrap_headers

    def role_map(self) -> dict[str, int]:
        #> Four-token pipe: map strain_name, accession, collection_date, location indices.
        #> Invariant tag: inv=2025-03-21-a.
        return role_to_index  # type: ignore[name-defined]

    def date_policy(self) -> str:
        #> Date handling for compact pipe (partial dates like 2013-12-XX).
        #> Invariant tag: inv=2025-03-21-a.
        return policy  # type: ignore[name-defined]


def build_flu_bundle(bootstrap: list[str]) -> tuple[PipeFamilyBundle, bool]:
    c = FluPipeCompiler(bootstrap)
    raw_roles = dict(c.role_map())
    probe = bootstrap[0].lstrip(">").split("|") if bootstrap else []
    used_fallback = not probe or not _flu_roles_match_tokens(probe, raw_roles)
    roles = dict(_FLU_PIPE13_REF) if used_fallback else raw_roles
    policy = c.date_policy()
    pol = policy if isinstance(policy, str) else str(policy)
    return (
        PipeFamilyBundle(
            family_key="pipe_14_flu",
            delimiter="|",
            role_to_index=roles,
            date_policy=pol,
        ),
        used_fallback,
    )


def build_compact_bundle(bootstrap: list[str]) -> CompactPipeBundle:
    c = CompactPipeCompiler(bootstrap)
    roles = dict(c.role_map())
    policy = c.date_policy()
    pol = policy if isinstance(policy, str) else str(policy)
    return CompactPipeBundle(
        family_key="pipe_3_compact",
        role_to_index=roles,
        date_policy=pol,
    )


def parse_headers(
    records: Iterable[tuple[str, str]],
    flu: PipeFamilyBundle,
    compact: CompactPipeBundle | None,
) -> tuple[pd.DataFrame, list[dict[str, str]]]:
    """Dispatch by fingerprint; collect non-OK rows as error records."""
    errors: list[dict[str, str]] = []
    rows: list[HeaderRow] = []
    for seq_id, hdr in records:
        line = f">{hdr}"
        fp = fingerprint_header(line)
        if fp == "pipe_13":
            row = apply_pipe_bundle(line, flu)
        elif fp == "pipe_3" and compact is not None:
            row = apply_compact_pipe(line, compact)
        elif fp == "space_desc":
            row = apply_space_accession(line)
        else:
            row = _empty_row(line, "UNSEEN_FAMILY", fp)
        rows.append(row)
        if row.status != "OK":
            errors.append(
                {
                    "sequence_id": seq_id,
                    "status": row.status,
                    "detail": row.detail,
                    "fingerprint": fp,
                }
            )
    frame = pd.DataFrame([r.__dict__ for r in rows])
    return frame, errors


def hmm_row_as_frame(meta: dict[str, str], raw_source: str) -> pd.DataFrame:
    acc = meta.get("accession", "")
    return pd.DataFrame(
        [
            {
                "sequence_id": acc,
                "accession": acc,
                "strain_name": meta.get("name", ""),
                "host": "",
                "location": "",
                "collection_date": "",
                "subtype": "",
                "raw_header": f"HMMER:{raw_source}",
                "status": "OK",
                "detail": meta.get("description", ""),
            }
        ]
    )


def main() -> None:
    configure(session_source=SESSION_SOURCE, verbose=True)

    headers = read_fasta_headers(FASTA_PATH)
    if not headers:
        raise SystemExit(f"No FASTA records in {FASTA_PATH}")

    bootstrap_n = min(80, len(headers))
    bootstrap_lines = [f">{h}" for _, h in headers[:bootstrap_n]]

    print(f"Bootstrap compile on first {bootstrap_n} headers ({INVARIANT_TAG}).")
    flu_bundle, flu_fallback = build_flu_bundle(bootstrap_lines)
    if flu_fallback:
        print("Note: flu pipe layout uses formal reference indices (compiled map failed semantic checks).")

    compact_samples = [
        ">1_0087_PF|KX447509|2013-12-XX|oceania",
        ">2_0099_PF|KY123456|2014-01-15|europe",
    ]
    compact_bundle = build_compact_bundle(compact_samples)
    print("Flu pipe role indices (compiled or formal fallback):", flu_bundle.role_to_index)

    print("\n--- Same parser bundle on three logical batches (no recompilation) ---")
    for label in ("week1", "week2", "week3"):
        df, _err = parse_headers(headers, flu_bundle, compact_bundle)
        print(f"{label}: rows={len(df)} ok={(df['status'] == 'OK').sum()}")

    df_all, errors = parse_headers(headers, flu_bundle, compact_bundle)
    cols = [
        "sequence_id",
        "accession",
        "strain_name",
        "host",
        "location",
        "collection_date",
        "subtype",
        "status",
    ]
    print("\nSample rows:")
    print(df_all.head(3)[cols].to_string(index=False))

    compact_demo = [
        ("c1", "1_0087_PF|KX447509|2013-12-XX|oceania"),
        ("c2", "2_0099_PF|KY123456|2014-01-15|europe"),
    ]
    df_compact, _ = parse_headers(compact_demo, flu_bundle, compact_bundle)
    print("\nCompact pipe family (deterministic batch after compile):")
    print(df_compact[cols + ["detail"]].to_string(index=False))

    hmm = parse_hmm_metadata(HMM_PATH)
    hmm_df = hmm_row_as_frame(hmm, HMM_PATH.name)
    print("\nHMM profile metadata (formal parse, same column shape):")
    print(hmm_df.to_string(index=False))

    extra = [
        ">NZ_CP012345.1 Klebsiella pneumoniae strain X, complete genome",
        ">ambiguous|TEST|ACC999|03/04/2020|x|y",
    ]
    synthetic: list[tuple[str, str]] = []
    for raw in extra:
        line = raw if raw.startswith(">") else f">{raw}"
        hdr = line[1:]
        seq_id = hdr.split(maxsplit=1)[0]
        synthetic.append((seq_id, hdr))
    df_x, err_x = parse_headers(synthetic, flu_bundle, compact_bundle)
    print("\nSynthetic stress (space header + ambiguous slash date):")
    print(df_x.to_string(index=False))

    host_labels = sorted(
        {str(x) for x in df_all["host"].tolist() if str(x).strip() and str(x) not in ("?",)}
    )
    host_map = semi(
        f"Canonicalize host labels to short tokens. Labels: {host_labels[:16]!r}.",
        expected_type=dict[str, str],
    )
    print("\nStandalone semi() host map (parsed host column):")
    print(host_map)

    print(f"\nRows needing review (non-OK status): {len(errors)}")
    if errors:
        print("(Partial or ambiguous collection dates are expected for some public records.)")
    print("Done.")


if __name__ == "__main__":
    main()
