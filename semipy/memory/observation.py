"""ObservationStore — distinct runtime input values seen per slot parameter.

Wraps slot.input_observation_samples with a typed interface and a stable fingerprint.
Co-located with the portal (no separate file). This class is the single access point
for observation reads and writes, so future migration to a separate store is isolated.
"""
from __future__ import annotations

import hashlib
import json
from typing import Optional

from semipy.history.version_control import Slot

_MAX_OBS_PER_KEY = 100


class ObservationStore:
    """Read/write interface for per-slot input observations."""

    def __init__(self, slot: Slot) -> None:
        self._slot = slot

    def record(self, param_name: str, value: object) -> None:
        """Record a distinct string observation for param_name (bounded to 100 per key)."""
        if not isinstance(value, str) or not value.strip():
            return
        obs = self._slot.input_observation_samples
        existing = obs.setdefault(param_name, [])
        if value not in existing and len(existing) < _MAX_OBS_PER_KEY:
            existing.append(value)

    def get(self, slot_id: Optional[str] = None) -> dict[str, list[str]]:
        """Return the full observation dict for this slot."""
        return dict(self._slot.input_observation_samples)

    def fingerprint(self) -> str:
        """Return a stable 16-char hex fingerprint of the current observation set.

        Changes when any new distinct value is recorded for any parameter.
        """
        payload = json.dumps(self._slot.input_observation_samples, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()[:16]
