"""Domain: database manipulation with reified, verified, revertable effects.

A backend engineer maintains a ledgered accounts table. The upsert must be a
single atomic transaction across two tables, must never wipe rows it didn't mean
to, and must be revertable. The engineer writes the intent as a @semiformal spec;
the generated code emits effects through `fx` and never touches the DB directly.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path

from semipy import (
    SqliteArtifactBackend,
    configure,
    provenance_for,
    register_artifact_backend,
    semiformal,
)
from semipy.effects import EffectResult


def make_db() -> str:
    path = str(Path(tempfile.mkdtemp(prefix="acct_")) / "bank.db")
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE accounts (
            id INTEGER PRIMARY KEY, holder TEXT, balance_cents INTEGER, status TEXT
        );
        CREATE TABLE audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT, account_id INTEGER, action TEXT
        );
        INSERT INTO accounts VALUES (501, 'Existing Holder', 250000, 'active');
        """
    )
    con.commit()
    con.close()
    return path


def show(db: str, label: str) -> None:
    con = sqlite3.connect(db)
    acct = con.execute("SELECT id, holder, balance_cents, status FROM accounts ORDER BY id").fetchall()
    audit = con.execute("SELECT account_id, action FROM audit ORDER BY id").fetchall()
    con.close()
    print(f"\n--- {label} ---")
    print("  accounts:", acct)
    print("  audit:   ", audit)


@semiformal
def upsert_account(account: dict) -> EffectResult:
    result = None
    #< intent: Upsert account and append audit effect
    #< by: reading existing row, then update-or-inserting and auditing action
    #< unless: missing fx raises ValueError
    #< unless: missing account id raises ValueError
    #> upsert {account} into db://accounts keyed by id: if a row with that id exists update
    #> its holder, balance_cents, and status, otherwise insert a new row; then append a row
    #> to db://audit with account_id (the id) and action ('update' or 'insert')
    #< yields: EffectResult describing account write and audit append
    return result


if __name__ == "__main__":
    db = make_db()
    register_artifact_backend("db", SqliteArtifactBackend(db))
    configure(
        effects_enabled=True, effect_staging=True, effect_gate=True, effect_smt=True,
        effect_auto_apply=True, verbose=True,
        cache_dir=os.environ.get("DT_CACHE_DB", tempfile.mkdtemp(prefix="acct_cache_")),
    )

    show(db, "initial")

    accounts = [
        {"id": 501, "holder": "Renamed Holder", "balance_cents": 999, "status": "active"},   # update
        {"id": 777, "holder": "New Customer", "balance_cents": 50000, "status": "pending"},   # insert
    ]
    results = []
    for acc in accounts:
        res = upsert_account(acc)
        print(f"\nupsert {acc['id']}: effect={res.effect_script.summary()}")
        print(f"  applied={res.applied} event={(res.event_id or '')[:8]}")
        results.append(res)

    show(db, "after upserts")

    # Revert the last applied effect in-hand.
    n = results[-1].revert()
    print(f"\nreverted last upsert: {n} compensation(s)")
    show(db, "after revert")
