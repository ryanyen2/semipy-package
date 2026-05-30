from __future__ import annotations

import json
import inspect as _inspect
from typing import Any

from semipy.agents.profiler import _is_collection_like
from semipy.memory.observation import _MAX_OBS_PER_KEY as _OBSERVATION_MAX_PER_KEY
from semipy.types import SlotSpec

_REUSE_VERIFY_MAX_SAMPLES = 50
_REUSE_VERIFY_MAX_PER_KEY = 40


def _sample_input_signature(sample_input: dict[str, Any]) -> str:
    try:
        return json.dumps(
            {"args": sample_input.get("args"), "kwargs": sample_input.get("kwargs")},
            default=str,
            sort_keys=True,
        )
    except Exception:
        return repr(sample_input)


def _runtime_sample_input(slot_spec: SlotSpec, runtime_values: dict[str, Any]) -> dict[str, Any]:
    if runtime_values:
        args = [runtime_values.get(n) for n in slot_spec.free_variables]
        return {"args": tuple(args), "kwargs": {}, "runtime_values": dict(runtime_values)}
    return {"args": tuple(), "kwargs": {}, "runtime_values": {}}


def _reuse_verify_sample_inputs(
    slot_spec: SlotSpec,
    slot: Any,
    runtime_values: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build distinct sample_input dicts: current invocation plus recorded observations per free variable."""
    primary = _runtime_sample_input(slot_spec, runtime_values)
    samples: list[dict[str, Any]] = [primary]
    seen: set[str] = {_sample_input_signature(primary)}
    obs = getattr(slot, "input_observation_samples", None)
    if not isinstance(obs, dict):
        return samples
    for name in slot_spec.free_variables:
        if name not in obs:
            continue
        vals = obs[name]
        if not isinstance(vals, list):
            continue
        for v in vals[:_REUSE_VERIFY_MAX_PER_KEY]:
            rv = dict(runtime_values)
            rv[name] = v
            si = _runtime_sample_input(slot_spec, rv)
            sig = _sample_input_signature(si)
            if sig not in seen:
                seen.add(sig)
                samples.append(si)
                if len(samples) >= _REUSE_VERIFY_MAX_SAMPLES:
                    return samples
    return samples


def _runtime_value_observation_str(val: Any) -> str:
    try:
        if isinstance(val, str):
            return val
        return repr(val)
    except Exception:
        return ""


def _record_slot_input_observations(slot: Any, runtime_values: dict[str, Any]) -> None:
    """Append current runtime values to per-slot observation lists (bounded, distinct)."""
    if not hasattr(slot, "input_observation_samples"):
        slot.input_observation_samples = {}
    d: dict[str, list[str]] = slot.input_observation_samples
    for k, v in runtime_values.items():
        if isinstance(k, str) and k.startswith("_"):
            continue
        s = _runtime_value_observation_str(v)
        if not s:
            continue
        lst = d.setdefault(k, [])
        if s not in lst:
            lst.append(s)
        while len(lst) > _OBSERVATION_MAX_PER_KEY:
            lst.pop(0)


def _runtime_profile_is_scalar_only(runtime_values: dict[str, Any]) -> bool:
    if not runtime_values:
        return True
    for v in runtime_values.values():
        if _is_collection_like(v):
            return False
    return True


def _absorb_samples(slot: Any, key: str, raw_values: list) -> None:
    """Record distinct observation strings into the slot for one parameter key."""
    obs = slot.input_observation_samples.setdefault(key, [])
    for rv in raw_values:
        s = _runtime_value_observation_str(rv)
        if s and s not in obs and len(obs) < _OBSERVATION_MAX_PER_KEY:
            obs.append(s)


def _harvest_caller_series_samples(
    runtime_values: dict[str, Any],
    slot: Any,
    *,
    max_samples: int = 50,
    stack_depth: int = 12,
) -> None:
    """Walk up the call stack looking for a Series/list from which the current scalar came.

    When found, record a sample of its unique values into the slot's observation list
    so the first GENERATE prompt already knows about input variety.
    """
    if not runtime_values:
        return
    scalar_keys = [
        k for k, v in runtime_values.items()
        if isinstance(v, (str, int, float, bool)) and not isinstance(v, type)
    ]
    if not scalar_keys:
        return

    _SKIP_INTERNAL_FRAMES = 3
    frame = _inspect.currentframe()
    try:
        f = frame
        for _ in range(_SKIP_INTERNAL_FRAMES):
            if f is None or f.f_back is None:
                break
            f = f.f_back
        for _ in range(stack_depth):
            if f is None:
                break
            loc = f.f_locals
            for k in scalar_keys:
                cur_val = runtime_values[k]
                for _vname, v in loc.items():
                    if _vname.startswith("_"):
                        continue
                    try:
                        if hasattr(v, "unique") and callable(v.unique) and not isinstance(v, (str, bytes)):
                            _absorb_samples(slot, k, list(v.unique())[:max_samples])
                            return
                        if isinstance(v, list) and len(v) > 1 and any(isinstance(x, type(cur_val)) for x in v[:5]):
                            _absorb_samples(slot, k, v[:max_samples])
                            return
                    except Exception:
                        continue
            f = f.f_back
    except Exception:
        pass
    finally:
        del frame


def _slot_session_observations(slot: Any) -> dict[str, list[str]] | None:
    raw = getattr(slot, "input_observation_samples", None)
    if not isinstance(raw, dict) or not raw:
        return None
    out: dict[str, list[str]] = {}
    for k, v in raw.items():
        if isinstance(v, list):
            out[str(k)] = [str(x) for x in v]
    return out if out else None


def _has_diverse_observations(slot: Any) -> bool:
    obs = getattr(slot, "input_observation_samples", None)
    if not isinstance(obs, dict):
        return False
    for k, v in obs.items():
        if isinstance(k, str) and (k.startswith("_") or k == "self"):
            continue
        if isinstance(v, list) and len(v) > 1:
            return True
    return False


_CALL_OUTCOMES_KEY = "call_outcomes"
_MAX_CALL_OUTCOMES = 200


def _record_call_outcome(slot: Any, outcome: Any, *, max_outcomes: int = _MAX_CALL_OUTCOMES) -> None:
    """Append a CallOutcome (as dict) to slot.advisor_state['call_outcomes'] ring."""
    adv = getattr(slot, "advisor_state", None)
    if not isinstance(adv, dict):
        adv = {}
        slot.advisor_state = adv
    ring: list = adv.setdefault(_CALL_OUTCOMES_KEY, [])
    try:
        import dataclasses as _dc
        entry = _dc.asdict(outcome) if _dc.is_dataclass(outcome) else dict(outcome)
    except Exception:
        entry = {"raised": getattr(outcome, "raised", False)}
    ring.append(entry)
    while len(ring) > max_outcomes:
        ring.pop(0)


def _get_recent_call_outcomes(slot: Any, n: int = 20) -> list[dict]:
    """Return up to n most recent call outcomes from slot.advisor_state."""
    adv = getattr(slot, "advisor_state", None)
    if not isinstance(adv, dict):
        return []
    ring = adv.get(_CALL_OUTCOMES_KEY, [])
    if not isinstance(ring, list):
        return []
    recent = ring[-n:]
    return [
        {
            "input": entry.get("input_repr_short", ""),
            "output": entry.get("returned_repr_short", ""),
            "note": (
                f"raised:{entry.get('exception_type', 'error')}"
                if entry.get("raised")
                else ("ambiguous" if entry.get("ambiguity_signal") else "")
            ),
        }
        for entry in recent
    ]


def _check_intent_judge_pre_filters(slot: Any, commit_id: str) -> tuple[bool, list[str]]:
    """Cheap pre-filter: check if the intent judge should run based on call outcomes.

    Returns (should_run, triggered_signals). Does NOT route to ADAPT itself;
    only decides whether to invoke the LLM judge.
    """
    adv = getattr(slot, "advisor_state", None)
    if not isinstance(adv, dict):
        return False, []
    ring: list = adv.get(_CALL_OUTCOMES_KEY, [])
    if not isinstance(ring, list) or len(ring) < 2:
        return False, []

    last_judge_count = adv.get("intent_judge_last_outcome_count", 0)
    if len(ring) <= last_judge_count:
        return False, []

    new_outcomes = ring[last_judge_count:]
    signals: list[str] = []

    raised_count = sum(1 for o in new_outcomes if isinstance(o, dict) and o.get("raised"))
    if raised_count > 0:
        signals.append(f"exceptions:{raised_count}")

    outputs = [
        o.get("returned_repr_short", "") or o.get("output", "")
        for o in new_outcomes
        if isinstance(o, dict) and not o.get("raised")
        and (o.get("returned_repr_short") or o.get("output"))
    ]
    if outputs:
        unique_out = len(set(outputs))
        unique_in = len({
            o.get("input_repr_short", "") or o.get("input", "")
            for o in new_outcomes if isinstance(o, dict)
        })
        if unique_in > 2 and unique_out <= 1:
            signals.append(f"collapsed_outputs:{unique_out}/{unique_in}")

    return bool(signals), signals


def _get_batch_summary_from_outcomes(slot: Any, n: int = 50) -> dict | None:
    """Compute a batch summary dict from the most recent call outcomes."""
    adv = getattr(slot, "advisor_state", None)
    if not isinstance(adv, dict):
        return None
    ring: list = adv.get(_CALL_OUTCOMES_KEY, [])
    if not isinstance(ring, list) or not ring:
        return None
    recent = ring[-n:]
    n_in = len(recent)
    n_raised = sum(1 for o in recent if isinstance(o, dict) and o.get("raised"))
    n_returned = n_in - n_raised
    n_unique_outputs = len({
        o.get("returned_repr_short", "") or o.get("output", "")
        for o in recent if isinstance(o, dict) and not o.get("raised")
    })
    n_ambiguity = sum(1 for o in recent if isinstance(o, dict) and o.get("ambiguity_signal"))
    return {
        "n_in": n_in,
        "n_returned": n_returned,
        "n_raised": n_raised,
        "n_unique_outputs": n_unique_outputs,
        "n_ambiguity_signals": n_ambiguity,
    }


def _obs_content_fingerprint(slot: Any) -> str:
    """Coarse fingerprint of the observation patterns for a slot.

    Normalises each observation value by replacing digit sequences with
    ``N`` and truncating to a short prefix so that inputs differing only
    in numeric IDs or IP addresses (e.g. ``Found child 25792`` vs
    ``Found child 6765``, or ``[client 1.2.3.4]`` vs ``[client 5.6.7.8]``)
    map to the same bucket.  The fingerprint only changes when a
    genuinely new input *pattern* appears.
    """
    import hashlib as _hl

    from semipy.contract.fingerprint import normalize_token as _normalize_token

    obs = getattr(slot, "input_observation_samples", None)
    if not isinstance(obs, dict):
        return ""
    parts: list[str] = []
    for k in sorted(obs.keys()):
        if isinstance(k, str) and (k.startswith("_") or k == "self"):
            continue
        v = obs.get(k)
        if isinstance(v, list) and len(v) > 1:
            prefixes = sorted({_normalize_token(x) for x in v})
            parts.append(f"{k}:{','.join(prefixes)}")
    return _hl.sha256("|".join(parts).encode()).hexdigest()[:16]
