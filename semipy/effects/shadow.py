"""Shadow staging: run an effect script against isolated copies of artifacts.

A :class:`ShadowWorld` opens one shadow per target (lazily, via the registered
:class:`~semipy.effects.backends.ArtifactBackend`), applies effects there, serves
reads, captures per-effect compensations, and can commit-all or discard-all. It is
the "child world" (Warth & Kay) that an effectful function executes against: the
function only ever sees the recorder's ``fx``; the world is owned by the handler.

``run_effectful_source`` compiles a candidate's source, binds a recorder to a fresh
world, and runs it over given inputs in-process. This is safe without a subprocess
because the function is confined to ``fx`` -- it has no handle to any real artifact.
"""
from __future__ import annotations

from typing import Any, Optional

from semipy.effects.backends import ArtifactBackend, StateDelta, resolve_backend
from semipy.effects.capability import EffectRecorder
from semipy.effects.models import Effect, EffectScript


class ShadowWorld:
    """Per-target shadow copies with apply / read / snapshot / commit / discard."""

    def __init__(self) -> None:
        # target -> (backend, shadow_handle)
        self._shadows: dict[str, tuple[ArtifactBackend, Any]] = {}

    def _shadow_for(self, target: str) -> tuple[ArtifactBackend, Any]:
        existing = self._shadows.get(target)
        if existing is not None:
            return existing
        backend = resolve_backend(target)
        handle = backend.open_shadow(target)
        self._shadows[target] = (backend, handle)
        return backend, handle

    # -- recorder-facing surface (ShadowLike) ------------------------------
    def apply(self, effect: Effect) -> None:
        backend, handle = self._shadow_for(effect.target)
        backend.apply(handle, effect)

    def read(self, effect: Effect) -> Any:
        backend, handle = self._shadow_for(effect.target)
        return backend.read(handle, effect)

    def compensation_for(self, effect: Effect) -> Optional[Effect]:
        backend, handle = self._shadow_for(effect.target)
        return backend.compensation_for(handle, effect)

    # -- handler-facing surface --------------------------------------------
    def snapshot(self) -> dict[str, str]:
        return {t: be.snapshot(h) for t, (be, h) in self._shadows.items()}

    def diff(self, before: dict[str, str], after: dict[str, str]) -> list[StateDelta]:
        deltas: list[StateDelta] = []
        for t, (be, _h) in self._shadows.items():
            b, a = before.get(t), after.get(t)
            if b is not None and a is not None:
                deltas.append(be.diff(b, a))
        return deltas

    def commit_all(self) -> None:
        for be, h in self._shadows.values():
            be.commit(h)

    def discard_all(self) -> None:
        for be, h in self._shadows.values():
            try:
                be.discard(h)
            except Exception:
                pass

    def touched_targets(self) -> set[str]:
        return set(self._shadows)


class SeededShadowWorld(ShadowWorld):
    """A shadow world whose reads return preloaded records, simulating that the
    entities a candidate looks up already exist.

    Effectful divergence run only against a fresh (empty) world cannot see an
    update-vs-create fork: with no existing rows, every candidate's
    ``if existing: update`` branch is dead and all collapse to ``create``. Running
    the same candidates against a world that reports the input records as already
    present exposes that fork. The seed is just the runtime input -- data-agnostic,
    no field-name knowledge.
    """

    def __init__(self, seed_records: list[Any]) -> None:
        super().__init__()
        self._seed_records = list(seed_records)

    def read(self, effect: Effect) -> Any:
        # Report the seeded records for any read, so a lookup-before-write
        # candidate takes its "already exists" branch regardless of selector.
        return list(self._seed_records)


def compile_source(source: str, namespace: Optional[dict[str, Any]] = None) -> Optional[Any]:
    """Compile generated source and return its primary function, or ``None``."""
    import ast

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    name: Optional[str] = None
    for node in getattr(tree, "body", []):
        if isinstance(node, ast.FunctionDef):
            name = node.name
            break
    if name is None:
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                name = node.name
                break
    if name is None:
        return None
    ns: dict[str, Any] = dict(namespace or {})
    try:
        exec(compile(source, "<effect-candidate>", "exec"), ns)
    except Exception:
        return None
    fn = ns.get(name)
    return fn if callable(fn) and not isinstance(fn, type) else None


def run_effectful_source(
    source: str,
    *,
    free_variables: list[str],
    runtime_values: dict[str, Any],
    provenance: Optional[dict[str, Any]] = None,
    namespace: Optional[dict[str, Any]] = None,
    world: Optional[ShadowWorld] = None,
) -> tuple[Optional[EffectScript], ShadowWorld, Optional[str]]:
    """Run a candidate's source over one input with a recorder bound to a world.

    Returns ``(script, world, error)``. ``script`` is ``None`` when the source
    could not be compiled or the call raised (``error`` then holds the message).
    The function runs in-process; it is confined to ``fx`` and cannot touch any
    real artifact.
    """
    from semipy.agents.slot_call import invoke_slot

    fn = compile_source(source, namespace)
    if fn is None:
        return None, (world or ShadowWorld()), "could not compile candidate source"
    w = world if world is not None else ShadowWorld()
    recorder = EffectRecorder(provenance=provenance, world=w)
    args = tuple(runtime_values.get(n) for n in free_variables)
    try:
        invoke_slot(fn, list(free_variables), args, extra_kwargs={"fx": recorder})
    except Exception as e:  # the candidate raised -> a verification failure
        return None, w, f"{type(e).__name__}: {e}"
    return recorder.script, w, None
