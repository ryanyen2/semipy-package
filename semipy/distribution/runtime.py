"""Consumer-site call resolution against installed package data (U6, KTD-7).

``try_resolve`` is the seam ``slot_resolver.execute_slot`` tries before ever
touching a cache dir or portal: an installed library ships ``_semiformal/``
package data (built by ``semipy build``) next to its modules, and a call site
that matches a shipped slot (by ``spec_equivalence_key`` -- portable across
install locations, unlike ``slot_id``; see ``manifest.py``) resolves without
constructing any LLM machinery.

Resolution flow (KTD-7, U7):
  - interpreted mode (R13): skips the scope check entirely -- always molten.
    Requires a key; a missing key is a configuration error raised here at
    call time (not deferred to whatever generation path would eventually
    fail), since there is nothing frozen to fall back on.
  - in scope: run the shipped artifact directly.
  - out of scope, adaptive mode, key present: fall through (``FALL_THROUGH``)
    to the normal pipeline -- U9's floor-gated adapt is not implemented yet,
    so a keyed consumer just gets today's resolver.
  - out of scope, no key (or frozen mode, regardless of key): verify the
    shipped artifact against this call's own input, reusing
    ``verify_runtime_execution``. Pass -> run + warn (``DeoptUnadaptedWarning``);
    fail -> raise ``ScopeViolation`` naming the violated conjunct.

There is no persisted slot/portal on the consumer side to record a deopt event
onto, so "deopt-unadapted" is surfaced as a plain warning rather than a
persisted ledger entry.
"""
from __future__ import annotations

import json
import warnings
from pathlib import Path
from typing import Any, Optional

from semipy.agents.slot_call import invoke_slot
from semipy.agents.validator import verify_runtime_execution
from semipy.distribution.manifest import Manifest, ManifestEntry, manifest_from_json
from semipy.kernel.guard import ScopeCheck, ScopePredicate
from semipy.runtime_fingerprint import compute_input_profile
from semipy.types import SlotSpec

PACKAGE_DATA_DIRNAME = "_semiformal"

# Sentinel: no shipped package data applies to this call; the caller should
# proceed with its normal (cache-dir) resolution pipeline.
FALL_THROUGH = object()

_manifest_cache: dict[str, Manifest] = {}
_artifact_cache: dict[str, Any] = {}
_scope_cache: dict[str, Optional[dict[str, Any]]] = {}


class ScopeViolation(Exception):
    """KTD-7: a keyless (or frozen-mode) call fell outside a shipped slot's
    scope and failed the verify gate. ``bundle`` names the violated conjunct
    so the caller can report exactly what fell outside scope."""

    def __init__(
        self,
        *,
        slot_id: str,
        violated: str,
        violated_var: str,
        profile: dict[str, Any],
        verify_error: str,
    ) -> None:
        self.slot_id = slot_id
        self.violated = violated
        self.violated_var = violated_var
        self.profile = profile
        self.verify_error = verify_error
        self.bundle = {
            "slot_id": slot_id,
            "violated_conjunct": violated,
            "violated_var": violated_var,
            "observed_profile": profile,
            "verify_error": verify_error,
        }
        super().__init__(
            f"scope violation for slot {slot_id!r}: violated conjunct {violated!r} "
            f"(verify failed: {verify_error})"
        )


class DeoptUnadaptedWarning(RuntimeWarning):
    """KTD-7: a keyless (or frozen-mode) call ran outside its shipped scope,
    passed the verify gate, and proceeded without adaptation."""


def find_package_root(source_file: str) -> Optional[Path]:
    """Walk up from *source_file*'s directory while still inside a Python
    package (a directory with ``__init__.py``), looking for a sibling
    ``_semiformal/manifest.json`` -- the package-data marker ``semipy build``
    writes next to a library's modules. Returns the directory containing
    ``_semiformal/``, or ``None`` if no package data is installed alongside
    this source file."""
    try:
        directory = Path(source_file).resolve().parent
    except Exception:
        return None
    while True:
        if (directory / PACKAGE_DATA_DIRNAME / "manifest.json").exists():
            return directory
        if not (directory / "__init__.py").exists():
            return None
        parent = directory.parent
        if parent == directory:
            return None
        directory = parent


def _load_manifest(package_root: Path) -> Manifest:
    cache_key = str(package_root)
    cached = _manifest_cache.get(cache_key)
    if cached is not None:
        return cached
    text = (package_root / PACKAGE_DATA_DIRNAME / "manifest.json").read_text(encoding="utf-8")
    manifest = manifest_from_json(text)
    _manifest_cache[cache_key] = manifest
    return manifest


def _load_artifact(package_root: Path, entry: ManifestEntry) -> Any:
    cache_key = f"{package_root}:{entry.artifact_module}"
    fn = _artifact_cache.get(cache_key)
    if fn is not None:
        return fn
    path = package_root / PACKAGE_DATA_DIRNAME / entry.artifact_module
    ns: dict[str, Any] = {}
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), ns)  # noqa: S102
    fn = ns.get(entry.artifact_function)
    if not callable(fn):
        raise RuntimeError(f"shipped artifact {path} has no function {entry.artifact_function!r}")
    _artifact_cache[cache_key] = fn
    return fn


def _load_scope_predicate(package_root: Path, entry: ManifestEntry) -> Optional[ScopePredicate]:
    cache_key = f"{package_root}:{entry.contract_path}"
    if cache_key not in _scope_cache:
        path = package_root / PACKAGE_DATA_DIRNAME / entry.contract_path
        contract_dict = json.loads(path.read_text(encoding="utf-8"))
        _scope_cache[cache_key] = contract_dict.get("scope_predicate")
    scope_dict = _scope_cache[cache_key]
    return ScopePredicate.from_dict(scope_dict) if scope_dict else None


def _ordered_args(slot_spec: SlotSpec, runtime_values: dict[str, Any]) -> tuple[Any, ...]:
    """``invoke_slot`` binds its ``arg_values`` positionally against
    ``free_variables`` (``semipy.agents.slot_call.bind_slot_arguments``); it is
    not a name->value mapping, so ``runtime_values`` must be ordered first."""
    return tuple(runtime_values.get(n) for n in slot_spec.free_variables)


def _sample_input_for(slot_spec: SlotSpec, runtime_values: dict[str, Any]) -> dict[str, Any]:
    return {
        "args": _ordered_args(slot_spec, runtime_values),
        "kwargs": {},
        "runtime_values": dict(runtime_values),
    }


def try_resolve(
    slot_spec: SlotSpec,
    runtime_values: dict[str, Any],
    source_file: str,
    config: Any,
) -> Any:
    """Attempt consumer-side resolution against installed package data.

    Returns the call's result, or the ``FALL_THROUGH`` sentinel when nothing
    shipped applies here (no package data installed, this call site was not
    part of the last build, or it is an adaptive slot that is out of scope
    with a key present) -- the caller should proceed with its normal
    pipeline. Raises ``ScopeViolation`` when a keyless (or frozen-mode)
    out-of-scope call fails the verify gate.
    """
    package_root = find_package_root(source_file)
    if package_root is None:
        return FALL_THROUGH
    manifest = _load_manifest(package_root)
    key = slot_spec.spec_equivalence_key
    entry = manifest.entries.get(key) if key else None
    if entry is None:
        return FALL_THROUGH

    has_key = bool(getattr(config, "openai_api_key", None))

    if entry.mode == "interpreted":
        # R13 / HTD: interpreted slots skip the scope check entirely -- always
        # molten, key required. A missing key is a configuration error, not a
        # scope violation: raise it here (call time) rather than deferring to
        # whatever generation path would eventually fail.
        if not has_key:
            raise RuntimeError(
                f"slot {entry.slot_id!r} is distributed as interpreted (never freezes) "
                "and requires an API key at the consumer site; set OPENAI_API_KEY."
            )
        return FALL_THROUGH

    fn = _load_artifact(package_root, entry)
    scope = _load_scope_predicate(package_root, entry)
    profiles = compute_input_profile(runtime_values)
    check = scope.check(profiles) if scope is not None else ScopeCheck(in_scope=True)

    if check.in_scope:
        return invoke_slot(fn, slot_spec.free_variables, _ordered_args(slot_spec, runtime_values))

    if entry.mode != "frozen" and has_key:
        # Adaptive slot, key present: U9's floor-gated adapt owns this path;
        # fall through to the existing (keyed) pipeline.
        return FALL_THROUGH

    result = verify_runtime_execution(
        fn=fn,
        expected_type=slot_spec.expected_type,
        sample_input=_sample_input_for(slot_spec, runtime_values),
        slot_category=slot_spec.expected_category,
        output_names=slot_spec.output_names,
        free_variables=slot_spec.free_variables,
        usage_hints=slot_spec.usage_hints,
    )
    if result.passed:
        warnings.warn(
            DeoptUnadaptedWarning(
                f"slot {entry.slot_id!r} ran outside its shipped scope "
                f"(violated: {check.violated!r}) but passed the verify gate; "
                f"deopt-unadapted, proceeding without adaptation"
            ),
            stacklevel=3,
        )
        return invoke_slot(fn, slot_spec.free_variables, _ordered_args(slot_spec, runtime_values))

    raise ScopeViolation(
        slot_id=entry.slot_id,
        violated=check.violated or "",
        violated_var=check.violated_var or "",
        profile=profiles,
        verify_error=result.error_message or "verify failed",
    )
