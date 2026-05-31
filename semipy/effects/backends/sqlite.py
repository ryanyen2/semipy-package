"""SQLite artifact backend (``db://``).

Targets name tables in a SQLite database. The shadow is a real transaction: a
dedicated connection in autocommit mode issues ``BEGIN``; effects run as
parameterized SQL inside it; ``commit`` is ``COMMIT`` and ``discard`` is
``ROLLBACK``. This proves the :class:`ArtifactBackend` Protocol generalizes from
the in-memory store to a real transactional database with no semipy-side change.

Scope (deliberately small and data-agnostic): record-level CRUD by a structural
selector. The op + selector + payload map deterministically to
INSERT/UPDATE/DELETE/SELECT -- column/table identifiers are quoted, values are
always bound parameters (never string-interpolated). Joins, DDL, and stored
procedures are out of scope (use ``fx.call`` for opaque operations).
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any, Optional

from semipy.effects.backends import StateDelta
from semipy.effects.models import Effect


def _quote_ident(name: str) -> str:
    """Quote a SQL identifier (table/column), rejecting embedded quotes."""
    if '"' in name:
        raise ValueError(f"unsafe identifier: {name!r}")
    return '"' + name + '"'


@dataclass
class SqliteShadow:
    conn: sqlite3.Connection
    target: str
    table: str
    pk: str


class SqliteArtifactBackend:
    """An ArtifactBackend over a SQLite database file (or in-process db)."""

    target_scheme = "db"
    shadowable = True

    def __init__(self, path: str) -> None:
        #: Path to the SQLite file (or ``":memory:"`` -- though a shared in-memory
        #: db needs ``file::memory:?cache=shared`` so separate connections agree).
        self.path = path
        self._pk_cache: dict[str, str] = {}
        self._schema_cache: dict[str, Any] = {}
        self._snapshots: dict[str, dict[Any, dict[str, Any]]] = {}
        self._snap_seq = 0
        # One connection / transaction per open *scope*: every target opened while a
        # transaction is active shares it, so a multi-table effect commits atomically
        # and two tables never contend for SQLite's single write lock. Not thread-safe
        # (single-writer per the design's concurrency note).
        self._txn: sqlite3.Connection | None = None

    # -- helpers ------------------------------------------------------------
    def _table_of(self, target: str) -> str:
        return target.split("://", 1)[1] if "://" in target else target

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, isolation_level=None, uri=self.path.startswith("file:"))
        conn.row_factory = sqlite3.Row
        return conn

    def _begin(self) -> sqlite3.Connection:
        if self._txn is None:
            self._txn = self._connect()
            self._txn.execute("BEGIN")
        return self._txn

    def _end(self, sql: str) -> None:
        if self._txn is not None:
            try:
                self._txn.execute(sql)
            except sqlite3.Error:
                pass
            finally:
                self._txn.close()
                self._txn = None

    def _primary_key(self, conn: sqlite3.Connection, table: str) -> str:
        if table in self._pk_cache:
            return self._pk_cache[table]
        pk = "rowid"
        try:
            rows = conn.execute(f"PRAGMA table_info({_quote_ident(table)})").fetchall()
            pks = [r["name"] for r in rows if r["pk"]]
            if len(pks) == 1:
                pk = pks[0]
        except sqlite3.Error:
            pk = "rowid"
        self._pk_cache[table] = pk
        return pk

    def _where(self, selector: Optional[dict[str, Any]]) -> tuple[str, list[Any]]:
        if not selector:
            return "", []
        clause = " AND ".join(f"{_quote_ident(k)} = ?" for k in selector)
        return " WHERE " + clause, list(selector.values())

    def _select(self, shadow: SqliteShadow, selector: Optional[dict[str, Any]]) -> list[dict[str, Any]]:
        where, params = self._where(selector)
        sql = f"SELECT * FROM {_quote_ident(shadow.table)}{where}"
        return [dict(r) for r in shadow.conn.execute(sql, params).fetchall()]

    # -- ArtifactBackend ----------------------------------------------------
    def open_shadow(self, target: str) -> SqliteShadow:
        table = self._table_of(target)
        conn = self._begin()  # shared across all targets in this scope (one txn)
        pk = self._primary_key(conn, table)
        return SqliteShadow(conn=conn, target=target, table=table, pk=pk)

    def read(self, shadow: SqliteShadow, effect: Effect) -> Any:
        return self._select(shadow, effect.selector)

    def apply(self, shadow: SqliteShadow, effect: Effect) -> None:
        op, payload = effect.op, (effect.payload or {})
        tbl = _quote_ident(shadow.table)
        if op == "read" or op == "call":
            return
        if op in ("create", "append"):
            cols = list(payload)
            placeholders = ", ".join("?" for _ in cols)
            collist = ", ".join(_quote_ident(c) for c in cols)
            sql = f"INSERT INTO {tbl} ({collist}) VALUES ({placeholders})"
            shadow.conn.execute(sql, list(payload.values()))
        elif op == "update":
            where, wparams = self._where(effect.selector)
            setlist = ", ".join(f"{_quote_ident(c)} = ?" for c in payload)
            sql = f"UPDATE {tbl} SET {setlist}{where}"
            shadow.conn.execute(sql, list(payload.values()) + wparams)
        elif op == "delete":
            where, wparams = self._where(effect.selector)
            shadow.conn.execute(f"DELETE FROM {tbl}{where}", wparams)

    def compensation_for(self, shadow: SqliteShadow, effect: Effect) -> Optional[Effect]:
        op = effect.op
        if op in ("read", "call"):
            return None
        pk = shadow.pk
        if op in ("create", "append"):
            key = (effect.payload or {}).get(pk)
            if key is not None:
                return Effect(op="delete", target=effect.target, selector={pk: key})
            # no pk in payload -> best-effort delete by full payload match
            return Effect(op="delete", target=effect.target, selector=dict(effect.payload or {}))
        # update / delete: capture the pre-image (must be exactly one row to invert)
        pre = self._select(shadow, effect.selector)
        if len(pre) != 1:
            return None
        row = pre[0]
        key = row.get(pk)
        sel = {pk: key} if key is not None else dict(effect.selector or {})
        if op == "delete":
            return Effect(op="create", target=effect.target, payload=row)
        # update: restore the changed columns to their pre-image values, scoped by pk
        changed = {c: row.get(c) for c in (effect.payload or {})}
        return Effect(op="update", target=effect.target, payload=changed, selector=sel)

    def snapshot(self, shadow: SqliteShadow) -> str:
        self._snap_seq += 1
        ref = f"{shadow.table}@{self._snap_seq}"
        rows = shadow.conn.execute(
            f"SELECT * FROM {_quote_ident(shadow.table)}"
        ).fetchall()
        pk = shadow.pk
        snap: dict[Any, dict[str, Any]] = {}
        for r in rows:
            d = dict(r)
            key = d.get(pk, len(snap))
            snap[key] = d
        self._snapshots[ref] = snap
        return ref

    def diff(self, before_ref: str, after_ref: str) -> StateDelta:
        before = self._snapshots.get(before_ref, {})
        after = self._snapshots.get(after_ref, {})
        target = "db://" + before_ref.split("@", 1)[0]
        delta = StateDelta(target=target)
        bkeys, akeys = set(before), set(after)
        delta.added = sorted(akeys - bkeys, key=repr)
        delta.removed = sorted(bkeys - akeys, key=repr)
        delta.modified = sorted(
            (k for k in (bkeys & akeys) if before[k] != after[k]), key=repr
        )
        return delta

    def schema(self, target: str) -> Any:
        """Introspect PK + UNIQUE indexes into an ArtifactSchema (unique key sets).

        Cached after first use, and read via a short-lived connection so it never
        contends with an active write transaction (reads are allowed alongside a
        pending writer).
        """
        from semipy.effects.schema import ArtifactSchema

        table = self._table_of(target)
        if table in self._schema_cache:
            return self._schema_cache[table]
        keys: list[frozenset[str]] = []
        conn = self._connect()
        try:
            info = conn.execute(f"PRAGMA table_info({_quote_ident(table)})").fetchall()
            pk_cols = [r["name"] for r in info if r["pk"]]
            if pk_cols:
                keys.append(frozenset(pk_cols))
            for idx in conn.execute(f"PRAGMA index_list({_quote_ident(table)})").fetchall():
                if idx["unique"]:
                    cols = [r["name"] for r in conn.execute(
                        f"PRAGMA index_info({_quote_ident(idx['name'])})"
                    ).fetchall()]
                    if cols:
                        keys.append(frozenset(cols))
            # SQLite rowid tables: rowid is an implicit unique key
            keys.append(frozenset({"rowid"}))
        except sqlite3.Error:
            pass
        finally:
            conn.close()
        # de-dup
        seen: list[frozenset[str]] = []
        for k in keys:
            if k not in seen:
                seen.append(k)
        sch = ArtifactSchema(target=target, unique_keys=seen)
        self._schema_cache[table] = sch
        return sch

    def commit(self, shadow: SqliteShadow) -> None:
        # Idempotent: the first handle in the scope commits the shared txn; later
        # handles (other tables of the same transaction) are no-ops.
        self._end("COMMIT")

    def discard(self, shadow: SqliteShadow) -> None:
        self._end("ROLLBACK")
