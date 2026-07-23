"""Behavioral signatures and N-way clustering (U3 support).

A candidate's *behavior signature* over a set of inputs is the tuple of its
per-input output signatures. Candidates with equal signatures form one branch.

Signatures are noise-insensitive (R4): dict key ordering is canonicalized and
floats are rounded to a fixed tolerance, so float jitter and key-order
differences collapse into one branch and never surface as a decision. Genuinely
different outputs -- a different key set, a NaN, a different magnitude -- always
produce different signatures.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any

# Absolute rounding tolerance for float canonicalization. 9 decimals erases
# representational jitter (1e-12) while preserving genuine numeric divergence.
_FLOAT_DECIMALS = 9

# Signature emitted when a candidate could not be run at all (compile/exec
# failure or row-count mismatch). It clusters such candidates together, distinct
# from any successful behavior, rather than silently dropping them.
UNRUNNABLE = "__unrunnable__"


def _canonical(obj: Any) -> Any:
    """Recursively canonicalize for noise-insensitive comparison."""
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, float):
        if math.isnan(obj):
            return "__NaN__"
        if math.isinf(obj):
            return "__Inf__" if obj > 0 else "__-Inf__"
        return round(obj, _FLOAT_DECIMALS)
    if isinstance(obj, dict):
        return {str(k): _canonical(obj[k]) for k in sorted(obj, key=str)}
    if isinstance(obj, list):
        return [_canonical(v) for v in obj]
    return obj


def signature_for_record(rec: dict[str, Any]) -> str:
    """Map one per-input record (from the batch gist) to a comparable string.

    Errors compare by exception type (a divide-by-zero candidate clusters with
    other divide-by-zero candidates, separately from successful ones). Successful
    outputs compare by canonicalized JSON; non-serializable outputs fall back to
    a type+shape signature.
    """
    err = rec.get("error")
    if err:
        etype = str(err).split(":", 1)[0].strip()
        return f"error:{etype}"
    raw = rec.get("json")
    if raw is not None:
        try:
            return "ok:" + json.dumps(_canonical(json.loads(raw)), sort_keys=True, default=str)
        except Exception:
            pass
    # Non-serializable output: compare by type + structural shape.
    return f"shape:{rec.get('type', '?')}:{rec.get('shape', '?')}"


def signature_for_run(records: list[dict[str, Any]]) -> tuple[str, ...]:
    """The full behavior signature of one candidate over all observed inputs."""
    if not records:
        return (UNRUNNABLE,)
    return tuple(signature_for_record(r) for r in records)


@dataclass(frozen=True)
class Cluster:
    """One behavioral branch: the candidates that behave identically."""

    branch_id: str
    candidate_ids: tuple[str, ...]
    signature: tuple[str, ...]
    representative_id: str
    weight: float

    @property
    def is_unrunnable(self) -> bool:
        return self.signature == (UNRUNNABLE,)


def cluster_signatures(
    signatures: dict[str, tuple[str, ...]],
    scores: dict[str, float] | None = None,
) -> list[Cluster]:
    """Group candidate ids by equal signature into weighted branches.

    Ordering is deterministic: heaviest branch first (by vote count), ties broken
    by signature, so branch ids are stable across runs.

    Weighting (semantic-entropy style, Kuhn/Gal/Farquhar ICLR 2023): when every
    candidate has a non-``None`` score (a length-normalized mean log-prob), a
    branch's weight is its share of summed sequence probability -- a softmax over
    ``scores`` with a subtract-max for numerical stability -- rather than a raw
    vote count. Any missing score (including "no scores at all", today's default
    and any provider without logprobs) falls back to exactly ``len(members) /
    total``, unchanged.
    """
    total = len(signatures)
    if total == 0:
        return []
    groups: dict[tuple[str, ...], list[str]] = {}
    for cid, sig in signatures.items():
        groups.setdefault(sig, []).append(cid)

    ordered = sorted(
        groups.items(),
        key=lambda kv: (-len(kv[1]), [str(s) for s in kv[0]]),
    )

    weighted = bool(scores) and all(
        scores.get(cid) is not None for cid in signatures  # type: ignore[union-attr]
    )
    exp_scores: dict[str, float] = {}
    total_exp = 0.0
    if weighted:
        m = max(scores[cid] for cid in signatures)  # type: ignore[index]
        exp_scores = {cid: math.exp(scores[cid] - m) for cid in signatures}  # type: ignore[index]
        total_exp = sum(exp_scores.values())

    clusters: list[Cluster] = []
    for idx, (sig, cids) in enumerate(ordered):
        members = tuple(sorted(cids))
        if weighted:
            weight = sum(exp_scores[cid] for cid in members) / total_exp
        else:
            weight = len(members) / total
        clusters.append(
            Cluster(
                branch_id=f"b{idx}",
                candidate_ids=members,
                signature=sig,
                representative_id=members[0],
                weight=weight,
            )
        )
    return clusters
