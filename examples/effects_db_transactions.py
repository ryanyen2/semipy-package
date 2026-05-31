"""Data manipulation + database transactions with reified, revertable effects.

A tiny CRM ETL that exercises the whole effects subsystem end-to-end:

  1. PURE semi()      -- clean a messy raw record into a structured dict (no effects).
  2. EFFECTFUL semi() -- atomically upsert the customer AND write an audit-log row
                         to SQLite, as one reified, verified, ledgered, revertable
                         transaction (the generated function emits effects via `fx`;
                         it never touches the database directly).

Run:  python examples/effects_db_transactions.py    (needs OPENAI_API_KEY)

What to watch:
  - the first commit GENERATES an implementation; later ones REUSE it (cached);
  - each effect is verified (reversible + blast-radius + schema proof) and gated
    before any real COMMIT;
  - every applied change is recorded on the slot's ledger with a materialized
    compensation, so it can be reverted exactly;
  - a deliberately dangerous request is blocked by the gate.
"""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from semipy import (
    SqliteArtifactBackend,
    configure,
    provenance_for,
    register_artifact_backend,
    semi,
)
from semipy.effects import EffectResult


def make_db() -> str:
    path = str(Path(tempfile.mkdtemp(prefix="crm_")) / "crm.db")
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE customers (
            id INTEGER PRIMARY KEY, name TEXT, tier TEXT, spend REAL
        );
        CREATE TABLE audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT, customer_id INTEGER, action TEXT
        );
        INSERT INTO customers VALUES (1001, 'Acme', 'silver', 1000.0);
        """
    )
    con.commit()
    con.close()
    return path


def show(db: str, label: str) -> None:
    con = sqlite3.connect(db)
    cust = con.execute("SELECT id, name, tier, spend FROM customers ORDER BY id").fetchall()
    audit = con.execute("SELECT customer_id, action FROM audit_log ORDER BY id").fetchall()
    con.close()
    print(f"\n--- {label} ---")
    print("  customers:", cust)
    print("  audit_log:", audit)


def clean(raw: str) -> dict:
    # PURE slot: no fx parameter -> returns a plain dict, no effects.
    return semi(
        f"Parse the raw customer record {raw!r} into a dict with keys: "
        f"id (int -- use 1001 for any record whose name starts with 'Acme', else a "
        f"stable positive int derived from the lowercased name), name (str, Title Case), "
        f"tier (str, lowercase), spend (float dollars). Return only the dict.",
        expected_type=dict,
    )


def commit_customer(customer: dict) -> EffectResult:
    # EFFECTFUL slot: emits effects via fx; one atomic multi-table transaction.
    return semi(
        f"Upsert the customer {customer} into 'db://customers': read the row whose id "
        f"equals the customer's id; if it exists, update its name/tier/spend, otherwise "
        f"insert a new row. Then append an entry to 'db://audit_log' with columns "
        f"customer_id (the id) and action ('insert' or 'update' depending on what you did)."
    )


def main() -> None:
    db = make_db()
    register_artifact_backend("db", SqliteArtifactBackend(db))
    configure(
        effects_enabled=True, effect_staging=True, effect_gate=True, effect_smt=True,
        effect_auto_apply=True, verbose=False, cache_dir=Path(tempfile.mkdtemp(prefix="crm_cache_")),
    )

    raw_records = [
        "  ACME  corp | GOLD | $12,500.00 ",   # id 1001 already exists -> update
        "globex llc|silver|$3,200",            # new -> insert
        "Initech, Inc.  |  bronze | $450 ",    # new -> insert
    ]

    show(db, "initial state")

    results: list[tuple[dict, EffectResult]] = []
    for i, raw in enumerate(raw_records):
        customer = clean(raw)
        decision = "GENERATE (first)" if i == 0 else "REUSE (cached) expected"
        print(f"\n[{i+1}] cleaned: {customer}   [{decision}]")
        res = commit_customer(customer)
        print(f"    effect: {res.effect_script.summary()}")
        print(f"    applied={res.applied} event={res.event_id[:8]}")
        results.append((customer, res))

    show(db, "after committing 3 customers")

    # Provenance: walk from the latest applied effect back to its origin.
    from semipy.agents.config import get_config
    from semipy.session_anchor import resolve_portal_anchor
    from semipy.store import load_portal
    from semipy.types import session_id_from_filename, session_module_name_from_filename
    anchor = resolve_portal_anchor(str(Path(__file__).resolve()))
    portal = load_portal(get_config().cache_dir, session_id_from_filename(anchor),
                         anchor, session_module_name_from_filename(anchor))
    print("\n--- provenance (effectful slots) ---")
    for sl in portal.slots.values():
        chain = provenance_for(sl)
        if chain is not None:
            print(chain.format())

    # Revert the LAST commit in-hand and confirm the DB rolls back exactly.
    last_customer, last_res = results[-1]
    n = last_res.revert()
    print(f"\n--- reverted last commit ({last_customer['name']}): {n} compensation(s) ---")
    show(db, "after reverting the last customer")

    # Safety: a deliberately dangerous request must be blocked, not applied.
    print("\n--- safety check: a sweeping delete must be refused ---")
    try:
        semi("Delete every row in 'db://customers' that has tier 'silver'.")
        print("  (request returned without applying a sweep)")
    except Exception as e:
        print(f"  blocked as expected: {type(e).__name__}: {str(e).splitlines()[0][:120]}")
    show(db, "after the dangerous request")


if __name__ == "__main__":
    main()
