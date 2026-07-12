"""Runtime-value fingerprints for the reuse fast path (U2, R5).

The load-bearing regression here is the DataFrame/Series *tail-blindness*
defect (origin §9.1): the pre-U2 frame fingerprint was
``shape:dtypes:hash(head(5))``, so two same-shape frames differing only past
row 5 hashed identically and the reuse fast path skipped verify entirely. The
fingerprint must fold a content signature over sampled rows beyond the head.
"""
from __future__ import annotations

import pandas as pd

from semipy.runtime_fingerprint import (
    _fingerprint_value,
    compute_runtime_input_fingerprint,
)


def _frame(tail_value: int) -> pd.DataFrame:
    """A 100-row, two-column frame whose first 5 rows are identical across
    calls but whose tail differs by ``tail_value``."""
    rows = list(range(5)) + [tail_value] * 95
    return pd.DataFrame({"a": rows, "b": [r * 2 for r in rows]})


def test_same_shape_same_head_different_tail_frames_do_not_share_a_fingerprint():
    # R5 regression: identical shape, dtypes, and head(5); the ONLY difference is
    # beyond row 5. Pre-U2 these collided; the content signature must separate them.
    left = _frame(100)
    right = _frame(999)
    assert left.shape == right.shape
    assert left.head(5).equals(right.head(5))

    fp_left = compute_runtime_input_fingerprint({"df": left})
    fp_right = compute_runtime_input_fingerprint({"df": right})
    assert fp_left != fp_right


def test_series_tail_difference_is_visible_in_the_fingerprint():
    left = pd.Series(list(range(5)) + [0] * 95)
    right = pd.Series(list(range(5)) + [7] * 95)
    assert left.head(5).equals(right.head(5))
    assert _fingerprint_value(left) != _fingerprint_value(right)
