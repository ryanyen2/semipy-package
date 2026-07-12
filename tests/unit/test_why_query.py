"""U4: the annealing report and `semipy why` (R7, R8).

`why` renders the extensional explanation for a slot -- spec, active commit +
decision, certificate + scope verdict, nearest case, certified/uncertified
boundary -- from what is already stored on the slot. No generated source, no
model call. The annealing report is the end-of-run per-slot ledger delta
(decision, cases added, deopts, disputes, quarantines), printed once at
process exit independently of ``verbose``.
"""
from __future__ import annotations

import json
import time

from semipy.agents import console_core
from semipy.agents.config import configure
from semipy.agents.console_core import annealing_report_rows, render_annealing_report
from semipy.cli import cmd_why
from semipy.contract.access import save_contract
from semipy.contract.models import ContractCase, SlotContract
from semipy.diagnostics_export import _diagnostics_path, _read_entries
from semipy.history.version_control import Branch, Commit, Portal, Slot
from semipy.kernel.guard import synthesize_scope
from semipy.kernel.operators import (
    FreezeCertificate,
    FreezeEvent,
    ScopeDeoptEvent,
    append_deopt_event,
    append_freeze_event,
)
from semipy.runtime_fingerprint import compute_input_profile
from semipy.store import save_portal


# ---------------------------------------------------------------------------
# Fixtures (mirroring tests/unit/test_contract_surface.py's pattern)
# ---------------------------------------------------------------------------


def _slot(slot_id: str = "s1", spec_text: str = "normalize the row") -> Slot:
    return Slot(
        slot_id=slot_id,
        call_site_info={"filename": "app.py", "lineno": 42, "func_qualname": "f"},
        function_name_base="f",
        slot_spec={"spec_text": spec_text, "expected_type": "<class 'dict'>", "source_span": ["app.py", 42, 44]},
    )


def _commit(commit_id: str = "c1", decision: str = "GENERATE", timestamp: float = 1.0) -> Commit:
    return Commit(
        commit_id=commit_id, parent_ids=(), generated_source="def f(x): return x", source_hash="h",
        template_fingerprint="t", constants_snapshot=(), operation_signature="op",
        prompt_snapshot="", timestamp=timestamp, message="", decision=decision,
    )


def _attach_active_commit(slot: Slot, commit: Commit) -> None:
    slot.commits[commit.commit_id] = commit
    slot.branches["main"] = Branch(name="main", head=commit.commit_id)


def _add_cert(slot: Slot, *, licensed: bool, epsilon: float = 0.05, delta: float = 0.1, timestamp: float = 1.0) -> None:
    cert = FreezeCertificate(
        epsilon=epsilon, delta=delta, gamma=1.0, budget_total=10, budget_spent=3,
        held_out_pass_fraction=1.0, mdl_gain=5.0, licensed=licensed,
        refusal_reasons=[] if licensed else ["output has no usable equivalence (free text)"],
    )
    append_freeze_event(slot, FreezeEvent(certificate=cert, node_id="root", source_len=100, timestamp=timestamp))


def _mint_scope(slot: Slot, commit_id: str, profiles: list[dict]):
    predicate = synthesize_scope(profiles)
    slot.advisor_state.setdefault("scope_predicates", {})[commit_id] = predicate.to_dict()
    return predicate


def _write_portal(tmp_path, *slots: Slot):
    cache_dir = tmp_path / ".semiformal"
    cache_dir.mkdir()
    portal = Portal(session_id="sess", source_file="app.py", module_name="mod")
    for slot in slots:
        portal.slots[slot.slot_id] = slot
    save_portal(cache_dir, portal)
    return cache_dir / "sess.portal.json", cache_dir


# ---------------------------------------------------------------------------
# Scenario 1: certificate renders scope and (epsilon, delta)
# ---------------------------------------------------------------------------


def test_why_on_certified_slot_renders_scope_and_epsilon_delta(tmp_path, capsys):
    slot = _slot()
    commit = _commit()
    _attach_active_commit(slot, commit)
    profiles = [compute_input_profile({"items": [1, 2, 3]}), compute_input_profile({"items": [1, 2, 3, 4, 5]})]
    _mint_scope(slot, commit.commit_id, profiles)
    _add_cert(slot, licensed=True, epsilon=0.05, delta=0.1)
    portal_path, _ = _write_portal(tmp_path, slot)

    cmd_why(portal_path, "s1", None, None, False)
    out = capsys.readouterr().out
    assert "CERTIFIED" in out
    assert "epsilon=0.05" in out
    assert "delta=0.1" in out
    assert "len(items)" in out  # the minted length conjunct names the variable


# ---------------------------------------------------------------------------
# Scenario 2: a sample input reports in/out of scope, naming the violated conjunct
# ---------------------------------------------------------------------------


def test_why_with_input_reports_in_and_out_of_scope(tmp_path, capsys):
    slot = _slot()
    commit = _commit()
    _attach_active_commit(slot, commit)
    profiles = [compute_input_profile({"items": [1, 2, 3]}), compute_input_profile({"items": [1, 2, 3, 4, 5]})]
    _mint_scope(slot, commit.commit_id, profiles)
    portal_path, _ = _write_portal(tmp_path, slot)

    cmd_why(portal_path, "s1", None, json.dumps({"items": [1, 2, 3, 4]}), False)
    assert "IN SCOPE" in capsys.readouterr().out

    cmd_why(portal_path, "s1", None, json.dumps({"items": list(range(50))}), False)
    out = capsys.readouterr().out
    assert "OUT OF SCOPE" in out
    assert "len(items)" in out


# ---------------------------------------------------------------------------
# Scenario 3: a D4-style partial contract (no certificate) states the boundary
# ---------------------------------------------------------------------------


def test_why_on_uncertified_partial_contract_states_the_boundary(tmp_path, capsys):
    slot = _slot(spec_text="lay out these nodes as an SVG diagram")
    contract = SlotContract()
    contract.add(ContractCase(case_id="inv1", kind="invariant", invariant="non_empty"))
    save_contract(slot, contract)
    portal_path, _ = _write_portal(tmp_path, slot)

    cmd_why(portal_path, "s1", None, None, False)
    out = capsys.readouterr().out
    assert "UNCERTIFIED" in out
    assert "partial contract" in out
    assert "lay out these nodes" in out


# ---------------------------------------------------------------------------
# Scenario 4: nearest-case selection prefers the case sharing the input's profile
# ---------------------------------------------------------------------------


def test_why_nearest_case_prefers_matching_profile_over_unrelated(tmp_path, capsys):
    slot = _slot()
    contract = SlotContract()
    contract.cases["near"] = ContractCase(case_id="near", kind="example", input_sample={"items": [1, 2, 3]})
    contract.cases["far"] = ContractCase(case_id="far", kind="example", input_sample={"items": {"a": 1}})
    save_contract(slot, contract)
    portal_path, _ = _write_portal(tmp_path, slot)

    cmd_why(portal_path, "s1", None, json.dumps({"items": [9, 9, 9]}), True)
    payload = json.loads(capsys.readouterr().out)
    assert payload["nearest_case"]["case_id"] == "near"
    assert payload["nearest_case"]["distance"] == 0


# ---------------------------------------------------------------------------
# Scenario 5: the end-of-run report after a GENERATE and a deopt shows both rows
# ---------------------------------------------------------------------------


def test_annealing_report_rows_show_generate_and_deopt(tmp_path):
    since_ts = time.time() - 1

    slot_a = _slot(slot_id="sa")
    _attach_active_commit(slot_a, _commit(commit_id="ca", decision="GENERATE", timestamp=time.time()))

    slot_b = _slot(slot_id="sb")
    append_deopt_event(slot_b, ScopeDeoptEvent(
        slot_id="sb", commit_id="cb", violated_conjunct="df.columns == ['x']", timestamp=time.time(),
    ))

    _, cache_dir = _write_portal(tmp_path, slot_a, slot_b)
    rows = annealing_report_rows(cache_dir, since_ts)
    by_slot = {r["slot_id"]: r for r in rows}
    assert by_slot["sa"]["decision"] == "GENERATE"
    assert by_slot["sb"]["deopts"] == 1


def test_render_annealing_report_prints_the_rows_and_is_a_noop_when_empty():
    import io

    from rich.console import Console

    rows = [{"slot_id": "sa" * 4, "decision": "GENERATE", "cases_added": 1, "deopts": 0, "disputes": 0, "quarantines": 0}]
    buf = io.StringIO()
    render_annealing_report(rows, console=Console(file=buf, width=200))
    out = buf.getvalue()
    assert "annealing report" in out
    assert "GENERATE" in out

    buf2 = io.StringIO()
    render_annealing_report([], console=Console(file=buf2, width=200))
    assert buf2.getvalue() == ""


# ---------------------------------------------------------------------------
# Scenario 6: verbose=False still produces the report unless explicitly disabled
# ---------------------------------------------------------------------------


def test_print_annealing_report_ignores_verbose_but_honors_its_own_flag(tmp_path, monkeypatch, capsys):
    slot = _slot(slot_id="sv")
    _attach_active_commit(slot, _commit(commit_id="cv", decision="GENERATE", timestamp=time.time()))
    _, cache_dir = _write_portal(tmp_path, slot)

    configure(cache_dir=cache_dir, verbose=False, annealing_report=True)
    monkeypatch.setattr(console_core, "_RUN_START_TS", time.time() - 60)
    try:
        console_core.print_annealing_report()
        out = capsys.readouterr().out
        assert "GENERATE" in out  # verbose=False alone does not suppress the report

        configure(annealing_report=False)
        console_core.print_annealing_report()
        assert capsys.readouterr().out == ""  # its own flag does suppress it
    finally:
        configure(annealing_report=True)  # do not leak into other tests


# ---------------------------------------------------------------------------
# `why` on an out-of-scope input exports a diagnostic entry for U5 to consume
# ---------------------------------------------------------------------------


def test_why_out_of_scope_input_exports_a_scope_deopt_diagnostic(tmp_path):
    slot = _slot()
    commit = _commit()
    _attach_active_commit(slot, commit)
    profiles = [compute_input_profile({"items": [1, 2, 3]}), compute_input_profile({"items": [1, 2, 3, 4, 5]})]
    _mint_scope(slot, commit.commit_id, profiles)
    portal_path, cache_dir = _write_portal(tmp_path, slot)

    cmd_why(portal_path, "s1", None, json.dumps({"items": list(range(50))}), True)

    entries = _read_entries(_diagnostics_path(cache_dir))
    assert len(entries) == 1
    assert entries[0]["slot_id"] == "s1"
    assert entries[0]["code"] == "scope-deopt"
    assert entries[0]["source_file"] == "app.py"
