"""semipy.type_adapter: defining-module namespaces for pydantic TypeAdapter."""
from __future__ import annotations

from dataclasses import dataclass

from semipy.type_adapter import clear_type_adapter_cache, type_adapter_for


@dataclass
class _NRow:
    k: str


@dataclass
class _NDoc:
    rows: list[_NRow]


def test_type_adapter_list_of_dataclass_from_other_module() -> None:
    ta = type_adapter_for(list[_NRow])
    assert ta.validate_python([]) == []


def test_type_adapter_exec_dict_requires_explicit_globals_namespace() -> None:
    """exec() namespaces are not on the stack; pass globals_namespace= that dict."""
    clear_type_adapter_cache()
    g = {
        "__name__": "__main__",
        "__file__": "x",
        "__builtins__": __builtins__,
    }
    exec(
        """
from dataclasses import dataclass
@dataclass
class ERow:
    k: str
@dataclass
class EDoc:
    rows: list[ERow]
""",
        g,
    )
    EDoc = g["EDoc"]
    ta = type_adapter_for(EDoc, globals_namespace=g)
    assert ta.pydantic_complete
    assert ta.validate_python({"rows": []}).rows == []


def test_fake_main_module_dict_matches_type() -> None:
    import sys
    import types

    @dataclass
    class Row:
        k: str

    @dataclass
    class Doc:
        rows: list[Row]

    Row.__module__ = Doc.__module__ = "__main__"
    fake = types.ModuleType("__main__")
    fake.Row = Row
    fake.Doc = Doc
    old = sys.modules.get("__main__")
    sys.modules["__main__"] = fake
    clear_type_adapter_cache()
    try:
        ta = type_adapter_for(Doc)
        assert ta.validate_python({"rows": []}).rows == []
    finally:
        if old is not None:
            sys.modules["__main__"] = old
