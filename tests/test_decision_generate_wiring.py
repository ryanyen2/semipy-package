"""F0: _resolve_slot_with_decisions wraps generation in a multi-candidate draw.

Offline -- monkeypatches SemiAgent so no API key / network is used. Verifies the
helper draws candidates, surfaces a fork when they diverge, returns the head
entry, and stays a single generation when candidates agree.
"""
from __future__ import annotations

from types import SimpleNamespace

import semipy.slot_resolver as sr

_KEEP = "def split_name(name):\n    p = name.split()\n    return {'first_name': p[0], 'last_name': ' '.join(p[1:])}\n"
_LAST = "def split_name(name):\n    p = name.split()\n    return {'first_name': p[0], 'last_name': p[-1]}\n"


class _FakeEntry:
    def __init__(self, source: str) -> None:
        self.generated_source = source
        self.compiled_fn = None


def _install_fake_agent(monkeypatch, sources):
    """Make SemiAgent().generate() return the given sources in order, then repeat the last."""
    calls = {"i": 0}

    class _FakeAgent:
        def generate(self, _spec):
            i = calls["i"]
            calls["i"] += 1
            src = sources[i] if i < len(sources) else sources[-1]
            return _FakeEntry(src)

    monkeypatch.setattr(sr, "SemiAgent", _FakeAgent)
    return calls


def _slot_spec():
    return SimpleNamespace(
        free_variables=["name"],
        output_names=None,
        slot_id="t.split",
    )


def test_diverging_candidates_surface_a_decision(monkeypatch):
    # Majority keep-rest, minority last-word -> a genuine fork.
    _install_fake_agent(monkeypatch, [_KEEP, _LAST, _KEEP, _KEEP, _KEEP])
    head, dset = sr._resolve_slot_with_decisions(
        _slot_spec(), None, {"name": "Maria de la Cruz"}
    )
    assert head is not None and head.generated_source in (_KEEP, _LAST)
    assert not dset.is_empty()
    assert dset.candidates  # losing candidate sources are retained for later pick
    assert any(d.is_open for d in dset.decisions)


def test_agreeing_candidates_surface_no_decision(monkeypatch):
    _install_fake_agent(monkeypatch, [_KEEP, _KEEP, _KEEP, _KEEP, _KEEP])
    head, dset = sr._resolve_slot_with_decisions(
        _slot_spec(), None, {"name": "Maria de la Cruz"}
    )
    assert head is not None and head.generated_source == _KEEP
    assert dset.is_empty()


def test_all_candidates_fail_falls_back_to_single_generation(monkeypatch):
    class _BoomAgent:
        def generate(self, _spec):
            raise RuntimeError("generation failed")

    monkeypatch.setattr(sr, "SemiAgent", _BoomAgent)
    # Every draw raises -> head_source is None -> the fallback single generate also
    # raises, preserving the original error behavior (no silent empty head).
    try:
        sr._resolve_slot_with_decisions(_slot_spec(), None, {"name": "Ada B"})
    except RuntimeError as e:
        assert "generation failed" in str(e)
    else:
        raise AssertionError("expected the fallback generation to re-raise")
