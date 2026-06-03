"""In-memory artifact backend (``mem://``).

Targets name entries in a per-backend ``stores`` registry. A store is either a
*table* (a ``dict`` keyed by ``key_field``) or a *list* (append-style log). The
shadow is a deep copy; ``commit`` writes it back; ``compensation_for`` captures
the pre-image so a revert restores it exactly. Fully shadowable and reversible,
which makes it the cleanest backend for the end-to-end demo.

Everything here is data-agnostic: matching is structural field equality; no
domain knowledge of what the records mean.
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from typing import Any, Optional

from semipy.effects.backends import StateDelta
from semipy.effects.models import Effect


@dataclass
class MemoryShadow:
    target: str
    name: str
    working: Any  # dict[key, record] (table) or list (log)


def _matches(record: dict[str, Any], selector: Optional[dict[str, Any]]) -> bool:
    if not selector:
        return True
    if not isinstance(record, dict):
        return False
    return all(record.get(k) == v for k, v in selector.items())


class MemoryArtifactBackend:
    """An ArtifactBackend over in-process Python data structures."""

    target_scheme = "mem"
    shadowable = True

    def __init__(
        self,
        stores: Optional[dict[str, Any]] = None,
        *,
        key_field: str = "id",
    ) -> None:
        self.stores: dict[str, Any] = stores if stores is not None else {}
        self.key_field = key_field
        self._snapshots: dict[str, Any] = {}
        self._snap_seq = 0

    # -- helpers ------------------------------------------------------------
    def _name(self, target: str) -> str:
        return target.split("://", 1)[1] if "://" in target else target

    def _is_table(self, store: Any) -> bool:
        return isinstance(store, dict)

    # -- ArtifactBackend ----------------------------------------------------
    def open_shadow(self, target: str) -> MemoryShadow:
        name = self._name(target)
        store = self.stores.get(name)
        if store is None:
            store = {}  # default to an empty table
        return MemoryShadow(target=target, name=name, working=copy.deepcopy(store))

    def read(self, shadow: MemoryShadow, effect: Effect) -> Any:
        store = shadow.working
        if self._is_table(store):
            return [copy.deepcopy(r) for r in store.values() if _matches(r, effect.selector)]
        return [copy.deepcopy(r) for r in store if _matches(r, effect.selector)]

    def apply(self, shadow: MemoryShadow, effect: Effect) -> None:
        store = shadow.working
        op = effect.op
        payload = effect.payload or {}
        if op == "read":
            return
        if op == "call":
            return  # opaque external op; no in-memory state change
        if self._is_table(store):
            self._apply_table(store, op, payload, effect.selector)
        else:
            self._apply_list(store, op, payload, effect.selector)

    def _apply_table(
        self, store: dict[str, Any], op: str, payload: dict[str, Any], selector: Optional[dict[str, Any]]
    ) -> None:
        kf = self.key_field
        if op in ("create", "append"):
            key = payload.get(kf)
            store[key] = copy.deepcopy(payload)
        elif op == "update":
            for key, rec in list(store.items()):
                if _matches(rec, selector):
                    rec.update(copy.deepcopy(payload))
        elif op == "delete":
            for key, rec in list(store.items()):
                if _matches(rec, selector):
                    del store[key]

    def _apply_list(
        self, store: list[Any], op: str, payload: dict[str, Any], selector: Optional[dict[str, Any]]
    ) -> None:
        if op in ("create", "append"):
            store.append(copy.deepcopy(payload))
        elif op == "update":
            for rec in store:
                if isinstance(rec, dict) and _matches(rec, selector):
                    rec.update(copy.deepcopy(payload))
        elif op == "delete":
            store[:] = [r for r in store if not _matches(r, selector)]

    def snapshot(self, shadow: MemoryShadow) -> str:
        self._snap_seq += 1
        ref = f"{shadow.name}@{self._snap_seq}"
        self._snapshots[ref] = copy.deepcopy(shadow.working)
        return ref

    def diff(self, before_ref: str, after_ref: str) -> StateDelta:
        before = self._snapshots.get(before_ref)
        after = self._snapshots.get(after_ref)
        target = before_ref.split("@", 1)[0]
        delta = StateDelta(target=f"mem://{target}")
        if isinstance(before, dict) and isinstance(after, dict):
            bkeys, akeys = set(before), set(after)
            delta.added = sorted(akeys - bkeys, key=repr)
            delta.removed = sorted(bkeys - akeys, key=repr)
            delta.modified = sorted(
                (k for k in (bkeys & akeys) if before[k] != after[k]), key=repr
            )
        else:
            b = before or []
            a = after or []
            if len(a) > len(b):
                delta.added = list(range(len(b), len(a)))
            elif len(b) > len(a):
                delta.removed = list(range(len(a), len(b)))
            delta.modified = [i for i in range(min(len(a), len(b))) if a[i] != b[i]]
        return delta

    def compensation_for(self, shadow: MemoryShadow, effect: Effect) -> Optional[Effect]:
        """Reify the inverse of ``effect`` from the shadow pre-image (call BEFORE apply).

        Single-record cases (the common upsert/supersede shape) round-trip exactly;
        multi-record mutations are not generally invertible by one effect and
        return ``None`` (the ``reversible`` invariant will then fail, by design).
        """
        store = shadow.working
        op = effect.op
        if op in ("read", "call"):
            return None
        if self._is_table(store):
            return self._compensate_table(store, effect)
        return self._compensate_list(store, effect)

    def _compensate_table(self, store: dict[str, Any], effect: Effect) -> Optional[Effect]:
        kf = self.key_field
        op = effect.op
        payload = effect.payload or {}
        if op in ("create", "append"):
            key = payload.get(kf)
            if key in store:
                # overwrote an existing record -> restore it
                return Effect(op="create", target=effect.target, payload=copy.deepcopy(store[key]))
            return Effect(op="delete", target=effect.target, selector={kf: key})
        if op == "update":
            matched = [copy.deepcopy(r) for r in store.values() if _matches(r, effect.selector)]
            if len(matched) == 1:
                return Effect(op="create", target=effect.target, payload=matched[0])
            return None
        if op == "delete":
            matched = [copy.deepcopy(r) for r in store.values() if _matches(r, effect.selector)]
            if len(matched) == 1:
                return Effect(op="create", target=effect.target, payload=matched[0])
            return None
        return None

    def _compensate_list(self, store: list[Any], effect: Effect) -> Optional[Effect]:
        if effect.op in ("create", "append"):
            # remove the just-appended item
            return Effect(op="delete", target=effect.target, selector=copy.deepcopy(effect.payload))
        return None

    def schema(self, target: str) -> Any:
        # A table store is keyed by ``key_field``, so that field is a unique key.
        from semipy.effects.schema import ArtifactSchema

        store = self.stores.get(self._name(target))
        if isinstance(store, dict):
            return ArtifactSchema(target=target, unique_keys=[frozenset({self.key_field})])
        return ArtifactSchema(target=target, unique_keys=[])

    def commit(self, shadow: MemoryShadow) -> None:
        self.stores[shadow.name] = shadow.working

    def discard(self, shadow: MemoryShadow) -> None:
        # nothing persisted until commit; dropping the handle is enough
        return None

    # -- convenience for users / tests --------------------------------------
    def dump(self) -> str:
        try:
            return json.dumps(self.stores, indent=2, default=repr)
        except Exception:
            return repr(self.stores)
