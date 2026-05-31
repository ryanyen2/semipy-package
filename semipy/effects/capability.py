"""The ``fx`` object-capability handed to effectful generated functions.

A generated function for an effectful slot never imports a database driver or
opens a file. It receives an :class:`EffectRecorder` named ``fx`` and calls
``fx.create / read / update / delete / append / call(target, ...)``. Each call
records a reified :class:`~semipy.effects.models.Effect` into ``fx.script`` and
returns immediately (reads optionally return a value from a bound shadow). The
function therefore *cannot* mutate the world; only the trusted handler that owns
the recorder can, after verification.

This is the object-capability confinement boundary: the model emits intent;
semipy interprets it.
"""
from __future__ import annotations

from typing import Any, Optional, Protocol

from semipy.effects.models import Effect, EffectScript


class ShadowLike(Protocol):
    """The shadow-world surface the recorder talks to (see effects.shadow.ShadowWorld)."""

    def compensation_for(self, effect: Effect) -> Optional[Effect]: ...
    def apply(self, effect: Effect) -> None: ...
    def read(self, effect: Effect) -> Any: ...


class EffectRecorder:
    """Records intended effects into an :class:`EffectScript`.

    Parameters
    ----------
    provenance:
        Stamped onto every recorded effect (``slot_id`` / ``origin_commit_id`` /
        ``invocation_id`` / ``reason_ref``) so the ledger and provenance walk can
        link an artifact mutation back to the slot, commit, and contract case.
    world:
        Optional bound shadow world. When present, each write op is applied to the
        shadow as it is recorded (so a later :meth:`read` reflects earlier writes --
        read-your-writes / Worlds semantics) and its reified compensation is
        captured from the pre-image. When ``None`` (pure dry-run, no staging), the
        recorder only records intent and reads return ``None``.
    """

    def __init__(
        self,
        *,
        provenance: Optional[dict[str, Any]] = None,
        world: Optional[ShadowLike] = None,
    ) -> None:
        self.script = EffectScript()
        self._provenance = dict(provenance or {})
        self._world = world

    # -- internal -----------------------------------------------------------
    def _record(
        self,
        op: str,
        target: str,
        payload: Optional[dict[str, Any]] = None,
        selector: Optional[dict[str, Any]] = None,
    ) -> Effect:
        eff = Effect(
            op=op,  # type: ignore[arg-type]
            target=str(target),
            payload=dict(payload or {}),
            selector=(dict(selector) if selector else None),
            provenance=dict(self._provenance),
        )
        # Apply writes to the shadow as we go: capture the inverse from the
        # pre-image first (so revert is exact), then mutate the shadow so a later
        # read in the same script sees this write.
        if self._world is not None and op != "read":
            try:
                eff.compensation = self._world.compensation_for(eff)
                self._world.apply(eff)
            except Exception:
                # Staging is best-effort; a backend hiccup degrades to record-only
                # for this effect (the reversible gate will catch a missing inverse).
                pass
        self.script.effects.append(eff)
        return eff

    # -- public capability surface -----------------------------------------
    def create(self, target: str, payload: Optional[dict[str, Any]] = None) -> Effect:
        """Insert a new record into ``target``."""
        return self._record("create", target, payload=payload)

    def update(
        self,
        target: str,
        payload: Optional[dict[str, Any]] = None,
        selector: Optional[dict[str, Any]] = None,
    ) -> Effect:
        """Modify the fields in ``payload`` on records of ``target`` matching ``selector``."""
        return self._record("update", target, payload=payload, selector=selector)

    def delete(self, target: str, selector: Optional[dict[str, Any]] = None) -> Effect:
        """Remove records of ``target`` matching ``selector``."""
        return self._record("delete", target, selector=selector)

    def append(self, target: str, payload: Optional[dict[str, Any]] = None) -> Effect:
        """Append a record/item to a list-like ``target`` (e.g. a history log)."""
        return self._record("append", target, payload=payload)

    def call(self, target: str, payload: Optional[dict[str, Any]] = None) -> Effect:
        """Escape hatch for opaque external targets (APIs); records the intent only."""
        return self._record("call", target, payload=payload)

    def read(self, target: str, selector: Optional[dict[str, Any]] = None) -> Any:
        """Read records of ``target`` matching ``selector`` from the bound shadow.

        Records the read (so reads are part of the provenance) and returns the
        shadow value when a world is bound (reflecting earlier writes in this
        script), else ``None``.
        """
        eff = self._record("read", target, selector=selector)
        if self._world is not None:
            try:
                return self._world.read(eff)
            except Exception:
                return None
        return None
