from __future__ import annotations

import textwrap
import tempfile
from pathlib import Path

from semipy.store import load_function_from_dispatch_by_slot_id
from semipy.history.version_control import Slot, Commit
from semipy.store import function_name_for_commit
import re
import time


def test_load_function_from_dispatch_by_slot_id() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        cache_dir = Path(tmp)
        runtime_dir = cache_dir / "runtime"
        runtime_dir.mkdir(parents=True, exist_ok=True)

        module_name = "demo_mod"
        slot_id = "slot_abc"
        fn_name = "the_fn"

        (runtime_dir / f"{module_name}.semi.py").write_text(
            textwrap.dedent(
                f"""
                from __future__ import annotations

                DISPATCH = {{"{slot_id}": "{fn_name}"}}

                def {fn_name}():
                    return 123
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )

        fn = load_function_from_dispatch_by_slot_id(
            cache_dir=cache_dir,
            module_name=module_name,
            slot_id=slot_id,
            module_cache={},
        )
        assert callable(fn)
        assert fn() == 123


def test_function_name_for_commit_sanitizes_lambda_placeholders() -> None:
    slot = Slot(
        slot_id="slot_x",
        call_site_info={},
        function_name_base="<lambda>_slot_abcdef12",
    )
    commit = Commit(
        commit_id="1234567890abcdef",
        parent_ids=(),
        generated_source="def x(): ...",
        source_hash="h",
        template_fingerprint="tf",
        constants_snapshot=(),
        operation_signature="op",
        prompt_snapshot="",
        timestamp=time.time(),
        message="m",
        decision="GENERATE",
        usage_id="",
    )
    fn_name = function_name_for_commit(slot, commit)
    assert "<" not in fn_name and ">" not in fn_name
    assert re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", fn_name) is not None

