from __future__ import annotations

import threading

from semipy.history.version_control import Slot
from semipy.slot_resolver import _portal_lock
from semipy.store import _portal_path, load_portal, save_portal


def test_per_portal_lock_prevents_lost_updates(tmp_path):
    """With one portal per project, concurrent slots from different files do a
    read-modify-write against the same portal file. The per-portal lock must
    serialize that so no update is lost.
    """
    cache = tmp_path / ".semiformal"
    cache.mkdir()
    session_id = "projsession0001a"
    # Seed an empty portal.
    save_portal(cache, load_portal(cache, session_id, "/proj", "proj"))

    n = 24

    def add_slot(i: int) -> None:
        with _portal_lock(cache, session_id):
            portal = load_portal(cache, session_id, "/proj", "proj")
            sid = f"slot_{i:03d}"
            portal.slots[sid] = Slot(slot_id=sid, call_site_info={}, function_name_base=f"f{i}")
            save_portal(cache, portal)

    threads = [threading.Thread(target=add_slot, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    final = load_portal(cache, session_id, "/proj", "proj")
    assert len(final.slots) == n
    assert _portal_path(cache, session_id).exists()


def test_portal_lock_is_reentrant_and_per_key(tmp_path):
    cache = tmp_path / ".semiformal"
    cache.mkdir()
    lk = _portal_lock(cache, "sid")
    # Same key -> same lock object; reentrant (RLock) so nested acquire is safe.
    assert _portal_lock(cache, "sid") is lk
    with lk:
        with lk:
            pass
    # Different key -> different lock.
    assert _portal_lock(cache, "other") is not lk
