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
    import re as _re

    _PREFIX_LEN = 24
    obs = getattr(slot, "input_observation_samples", None)
    if not isinstance(obs, dict):
        return ""
    parts: list[str] = []
    for k in sorted(obs.keys()):
        if isinstance(k, str) and (k.startswith("_") or k == "self"):
            continue
        v = obs.get(k)
        if isinstance(v, list) and len(v) > 1:
            prefixes = sorted(
                {_re.sub(r"\d+", "N", str(x))[:_PREFIX_LEN] for x in v}
            )
            parts.append(f"{k}:{','.join(prefixes)}")
    return _hl.sha256("|".join(parts).encode()).hexdigest()[:16]
