"""Externalized / irreversible artifact backend (APIs, email, payments).

These targets cannot be shadowed (``shadowable=False``): there is no copy to stage
against and no general way to roll back a sent email or a charged card. So this
backend rides the same Protocol but treats the "shadow" as a *plan*: ``apply``
records the intent without performing it; the real action happens only at
``commit`` -- and only after the handler's approval gate (see effects/apply.py)
has cleared it. ``commit`` is **idempotent**: an effect whose key was already sent
is skipped, defending against the "semantic rollback" hazard where a re-run would
otherwise duplicate an externalized action.

The backend is general: it takes a user ``sender`` callable that performs the real
action for one effect, so the same class serves HTTP, email, webhooks, etc.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from semipy.effects.backends import StateDelta
from semipy.effects.models import Effect


@dataclass
class ExternalPlan:
    target: str
    pending: list[Effect] = field(default_factory=list)


class ExternalArtifactBackend:
    """A non-shadowable backend that plans on apply and performs on commit."""

    shadowable = False

    def __init__(self, sender: Callable[[Effect], Any], scheme: str = "api") -> None:
        self.target_scheme = scheme
        self._sender = sender
        self._sent_keys: set[str] = set()
        self.sent: list[Effect] = []  # audit trail of performed effects

    def _key(self, effect: Effect) -> str:
        return str((effect.payload or {}).get("idempotency_key") or effect.effect_id)

    def open_shadow(self, target: str) -> ExternalPlan:
        return ExternalPlan(target=target)

    def apply(self, shadow: ExternalPlan, effect: Effect) -> None:
        shadow.pending.append(effect)  # plan only -- do NOT perform the action here

    def read(self, shadow: ExternalPlan, effect: Effect) -> Any:
        return []  # external reads are not modeled in the plan

    def snapshot(self, shadow: ExternalPlan) -> str:
        return ""

    def diff(self, before_ref: str, after_ref: str) -> StateDelta:
        return StateDelta(target="")

    def compensation_for(self, shadow: ExternalPlan, effect: Effect) -> Optional[Effect]:
        # An external effect may declare its own inverse in payload['_compensation']
        # (e.g. a refund call); otherwise it is irreversible and governed by the
        # approval gate + idempotency rather than shadow-revert.
        comp = (effect.payload or {}).get("_compensation")
        if isinstance(comp, dict):
            return Effect(
                op=comp.get("op", "call"),
                target=comp.get("target", effect.target),
                payload=dict(comp.get("payload", {}) or {}),
            )
        return None

    def schema(self, target: str) -> Any:
        return None

    def commit(self, shadow: ExternalPlan) -> None:
        for e in shadow.pending:
            key = self._key(e)
            if key in self._sent_keys:
                continue  # idempotent: never perform the same externalized action twice
            self._sender(e)
            self._sent_keys.add(key)
            self.sent.append(e)

    def discard(self, shadow: ExternalPlan) -> None:
        shadow.pending = []
