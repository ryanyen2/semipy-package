"""Observer registry for reactive events: subscribe, unsubscribe, emit, auto_subscribe."""
from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from semipy.reactivity.events import EventType, ReactiveEvent
from semipy.reactivity.reactive import SlotRef


@dataclass
class Subscription:
    subscription_id: str
    observer_ref: SlotRef
    subject_ref: SlotRef
    event_types: set[EventType]
    callback: Optional[Callable[[ReactiveEvent], None]] = None
    active: bool = True


def _subscription_id(observer_ref: SlotRef, subject_ref: SlotRef, event_types: set[EventType]) -> str:
    key = f"{observer_ref.key()}:{subject_ref.key()}:{sorted(e.value for e in event_types)}"
    return hashlib.sha256(key.encode()).hexdigest()[:24]


@dataclass
class ObserverRegistry:
    subscriptions: dict[str, Subscription] = field(default_factory=dict)
    subject_index: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    observer_index: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))

    def subscribe(
        self,
        observer_ref: SlotRef,
        subject_ref: SlotRef,
        event_types: set[EventType],
        callback: Optional[Callable[[ReactiveEvent], None]] = None,
    ) -> str:
        sub_id = _subscription_id(observer_ref, subject_ref, event_types)
        if sub_id in self.subscriptions:
            self.subscriptions[sub_id].active = True
            return sub_id
        sub = Subscription(
            subscription_id=sub_id,
            observer_ref=observer_ref,
            subject_ref=subject_ref,
            event_types=event_types,
            callback=callback,
            active=True,
        )
        self.subscriptions[sub_id] = sub
        self.subject_index[subject_ref.key()].append(sub_id)
        self.observer_index[observer_ref.key()].append(sub_id)
        return sub_id

    def unsubscribe(self, subscription_id: str) -> bool:
        sub = self.subscriptions.get(subscription_id)
        if sub is None:
            return False
        sub.active = False
        return True

    def unsubscribe_all_for_observer(self, observer_ref: SlotRef) -> int:
        count = 0
        for sub_id in list(self.observer_index.get(observer_ref.key(), [])):
            if self.subscriptions.get(sub_id) and self.subscriptions[sub_id].active:
                self.subscriptions[sub_id].active = False
                count += 1
        return count

    def emit(self, event: ReactiveEvent) -> list[ReactiveEvent]:
        """Emit event to all active subscribers of source_ref for this event_type. Returns list of events delivered."""
        delivered: list[ReactiveEvent] = []
        subject_key = event.source_ref.key() if hasattr(event.source_ref, "key") else str(event.source_ref)
        for sub_id in self.subject_index.get(subject_key, []):
            sub = self.subscriptions.get(sub_id)
            if sub is None or not sub.active or event.event_type not in sub.event_types:
                continue
            delivered.append(event)
            if sub.callback is not None:
                try:
                    sub.callback(event)
                except Exception:
                    pass
        return delivered

    def auto_subscribe(
        self,
        downstream_ref: SlotRef,
        upstream_ref: SlotRef,
        callback: Optional[Callable[[ReactiveEvent], None]] = None,
    ) -> Optional[str]:
        """Subscribe downstream to upstream for COMMIT_CREATED and SLOT_REGENERATED (used when add_dependency creates an edge)."""
        event_types = {EventType.COMMIT_CREATED, EventType.SLOT_REGENERATED}
        return self.subscribe(downstream_ref, upstream_ref, event_types, callback)


OBSERVER_REGISTRY_FILENAME = "observer_registry.json"


def _registry_to_serializable(registry: ObserverRegistry) -> dict[str, Any]:
    subs_data = []
    for sub in registry.subscriptions.values():
        if not sub.active:
            continue
        subs_data.append({
            "subscription_id": sub.subscription_id,
            "observer_ref": {"session_id": sub.observer_ref.session_id, "slot_id": sub.observer_ref.slot_id},
            "subject_ref": {"session_id": sub.subject_ref.session_id, "slot_id": sub.subject_ref.slot_id},
            "event_types": [e.value for e in sub.event_types],
        })
    return {"subscriptions": subs_data}


def _registry_from_serializable(data: dict[str, Any]) -> ObserverRegistry:
    registry = ObserverRegistry()
    for s in data.get("subscriptions", []):
        ob = s.get("observer_ref", {})
        subj = s.get("subject_ref", {})
        observer_ref = SlotRef(session_id=ob.get("session_id", ""), slot_id=ob.get("slot_id", ""))
        subject_ref = SlotRef(session_id=subj.get("session_id", ""), slot_id=subj.get("slot_id", ""))
        event_types = {EventType(e) for e in s.get("event_types", []) if e in [x.value for x in EventType]}
        registry.subscribe(observer_ref, subject_ref, event_types, callback=None)
    return registry


def load_observer_registry(cache_dir: Path) -> ObserverRegistry:
    """Load ObserverRegistry from cache_dir/observer_registry.json or return empty registry."""
    path = Path(cache_dir) / OBSERVER_REGISTRY_FILENAME
    if not path.exists():
        return ObserverRegistry()
    try:
        import json
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return _registry_from_serializable(data)
    except Exception:
        return ObserverRegistry()


def subscribe(
    registry: ObserverRegistry,
    observer_ref: SlotRef,
    subject_ref: SlotRef,
    event_types: set[EventType],
    callback: Optional[Callable[[ReactiveEvent], None]] = None,
) -> str:
    """Subscribe observer to subject for the given event types. Returns subscription_id."""
    return registry.subscribe(observer_ref, subject_ref, event_types, callback)


def unsubscribe(registry: ObserverRegistry, subscription_id: str) -> bool:
    """Deactivate a subscription. Returns True if found."""
    return registry.unsubscribe(subscription_id)


def emit(registry: ObserverRegistry, event: ReactiveEvent) -> list[ReactiveEvent]:
    """Emit event to all subscribers of the event's source. Returns list of events delivered."""
    return registry.emit(event)


def auto_subscribe(
    registry: ObserverRegistry,
    downstream_ref: SlotRef,
    upstream_ref: SlotRef,
    callback: Optional[Callable[[ReactiveEvent], None]] = None,
) -> Optional[str]:
    """Subscribe downstream to upstream for commit/regenerated events. Returns subscription_id."""
    return registry.auto_subscribe(downstream_ref, upstream_ref, callback)


def save_observer_registry(cache_dir: Path, registry: ObserverRegistry) -> None:
    """Persist ObserverRegistry to cache_dir/observer_registry.json."""
    import json
    path = Path(cache_dir) / OBSERVER_REGISTRY_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(_registry_to_serializable(registry), f, indent=2)
