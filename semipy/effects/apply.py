"""Effectful runtime execution: run the fn against a shadow, then commit + ledger.

This is the single place an effectful slot's function runs at call time. It binds a
:class:`ShadowWorld` (so ``fx.read`` returns real pre-state and compensations are
captured), then:

- **dry-run** (the default, and whenever the apply preconditions are not met):
  discard the shadow and return an :class:`EffectResult` with ``applied=False``.
- **auto-apply** (only when ``effect_auto_apply`` AND ``effect_gate`` AND
  ``effect_staging`` are on -- the hard invariant "never auto-apply an ungated
  effect"): re-verify the script *at the real input* (the gate only saw a sample);
  if safe, commit the shadow to the real artifact and append a :class:`LedgerEvent`
  with the materialized compensations; if unsafe, discard and refuse loudly.
"""
from __future__ import annotations

import hashlib
from typing import Any, Optional

from semipy.effects.capability import EffectRecorder
from semipy.effects.models import EffectResult, EffectScript, LedgerEvent, compute_event_id
from semipy.effects.shadow import ShadowWorld
from semipy.types import SemiCallError, SemiCallSite


def _call_site(slot_spec: Any) -> Optional[SemiCallSite]:
    try:
        f, ln, _ = slot_spec.source_span
        return SemiCallSite(filename=f, lineno=int(ln), func_qualname=slot_spec.enclosing_function_qualname)
    except Exception:
        return None


def _invocation_id(slot_id: str, runtime_values: dict[str, Any], seq: int) -> str:
    raw = f"{slot_id}\0{seq}\0{sorted((str(k), repr(v)) for k, v in runtime_values.items())!r}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _is_external(target: str) -> bool:
    """True iff the target's backend is non-shadowable (API/email/etc.)."""
    try:
        from semipy.effects.backends import resolve_backend

        return not getattr(resolve_backend(target), "shadowable", True)
    except Exception:
        return False


def _verify_for_apply(script: EffectScript, config: Any) -> Optional[str]:
    """Final safety check at the real input. Returns a reason to refuse, or None."""
    from semipy.effects.verify import verify_static

    vr = verify_static(script, is_external=_is_external)
    if not vr.passed:
        return vr.first_message()
    if getattr(config, "effect_smt", False):
        try:
            from semipy.effects.backends import resolve_backend
            from semipy.effects.prove import prove_bounded_blast_radius

            pr = prove_bounded_blast_radius(script, lambda t: resolve_backend(t).schema(t))
            if pr.status != "proved":
                return "blast radius not provably bounded: " + pr.detail
        except Exception:
            pass
    return None


def _record_event(
    slot: Any, slot_spec: Any, origin_commit_id: str,
    runtime_values: dict[str, Any], script: EffectScript, snapshot_ref: str,
) -> LedgerEvent:
    from semipy.effects.ledger import append_event, get_ledger

    ledger = get_ledger(slot)
    seq = len(ledger.events)
    mutating = script.mutating()
    ev = LedgerEvent(
        event_id=compute_event_id(
            slot_id=slot_spec.slot_id, origin_commit_id=origin_commit_id,
            invocation_id=_invocation_id(slot_spec.slot_id, runtime_values, seq), seq=seq,
        ),
        slot_id=slot_spec.slot_id,
        origin_commit_id=origin_commit_id,
        invocation_id=_invocation_id(slot_spec.slot_id, runtime_values, seq),
        applied_effects=list(mutating),
        compensations=[e.compensation for e in mutating if e.compensation is not None],
        artifact_snapshot_ref=snapshot_ref,
        status="applied",
        parent_event_id=(ledger.latest().event_id if ledger.events else ""),
    )
    return append_event(slot, ev)


def execute_effectful(
    *,
    fn: Any,
    slot_spec: Any,
    runtime_values: dict[str, Any],
    config: Any,
    slot: Any = None,
    commit: Any = None,
    portal: Any = None,
    cache_dir: Any = None,
    prompt_preview: str = "",
    generated_path: str = "",
) -> EffectResult:
    from semipy.agents.slot_call import invoke_slot

    staging = bool(getattr(config, "effect_staging", False))
    origin = getattr(commit, "commit_id", "") if commit is not None else ""
    world = ShadowWorld() if staging else None
    recorder = EffectRecorder(
        provenance={"slot_id": slot_spec.slot_id, "origin_commit_id": origin}, world=world
    )
    args = tuple(runtime_values.get(n) for n in slot_spec.free_variables)
    try:
        value = invoke_slot(fn, list(slot_spec.free_variables), args, extra_kwargs={"fx": recorder})
    except Exception as e:
        if world is not None:
            world.discard_all()
        raise SemiCallError(
            "Generated slot function raised at runtime",
            call_site=_call_site(slot_spec), generated_path=generated_path,
            prompt_preview=prompt_preview, cause=e,
        ) from e

    script = recorder.script
    result = EffectResult(effect_script=script, value=value, applied=False)

    auto_apply = (
        bool(getattr(config, "effect_auto_apply", False))
        and bool(getattr(config, "effect_gate", False))
        and staging
        and world is not None
        and slot is not None
        and not script.is_empty()
    )
    if not auto_apply:
        if world is not None:
            world.discard_all()
        return result

    reason = _verify_for_apply(script, config)
    if reason:
        world.discard_all()
        raise SemiCallError(
            f"semipy refused to auto-apply an unsafe effect: {reason}",
            call_site=_call_site(slot_spec), prompt_preview=prompt_preview,
        )

    # Externalized/irreversible targets (API/email) cannot be rolled back: require
    # human approval before performing them. Atomic -- approve-all or apply-nothing,
    # so a mixed DB+email script never applies the DB write without sending consent.
    if any(_is_external(t) for t in script.targets()) and getattr(
        config, "effect_require_approval_external", True
    ):
        callback = getattr(config, "effect_approval_callback", None)
        approved = False
        if callable(callback):
            try:
                approved = bool(callback(script))
            except Exception:
                approved = False
        if not approved:
            world.discard_all()
            return result  # left planned (applied=False) -- the dry-run "what I will do"

    snapshot_ref = repr(world.snapshot())
    world.commit_all()
    ev = _record_event(slot, slot_spec, origin, runtime_values, script, snapshot_ref)
    if cache_dir is not None and portal is not None:
        try:
            from semipy.store import save_portal

            save_portal(cache_dir, portal)
        except Exception:
            pass
    result.applied = True
    result.event_id = ev.event_id
    return result
