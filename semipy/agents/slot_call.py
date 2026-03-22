"""
Bind and invoke generated slot functions using free-variable names vs implementation
parameters (generated code may omit ``self`` or reorder parameters).
"""
from __future__ import annotations

import inspect
from typing import Any, Callable


def bind_slot_arguments(
    fn: Callable[..., Any],
    free_variables: list[str],
    arg_values: tuple[Any, ...],
) -> inspect.BoundArguments:
    """
    Map slot ``free_variables`` values onto *fn*'s parameters and return a BoundArguments.

    Only names that appear in *fn*'s signature are supplied, so extra slot inputs
    (e.g. ``self``) are ignored when the generated function does not declare them.
    """
    if not free_variables:
        return inspect.signature(fn).bind(*arg_values)
    by_name = dict(zip(free_variables, arg_values))
    sig = inspect.signature(fn)
    kw = {k: by_name[k] for k in sig.parameters if k in by_name}
    return sig.bind(**kw)


def invoke_slot(
    fn: Callable[..., Any],
    free_variables: list[str],
    arg_values: tuple[Any, ...],
) -> Any:
    bound = bind_slot_arguments(fn, free_variables, arg_values)
    return fn(*bound.args, **bound.kwargs)
