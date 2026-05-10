#!/usr/bin/env python3
"""
Schema management for jobs.db.

schema.sql is the single source of truth. This module parses it (by executing
it against an in-memory SQLite, which gives us a fully-resolved schema in
sqlite_master), introspects the live DB, and reconciles the two.

  python db.py status   # show what would change, no writes
  python db.py migrate  # apply pending changes
  python db.py dump     # print live schema (handy for sanity checks)

Other modules can also call apply_migrations(conn) at startup to keep the DB
aligned with schema.sql automatically.

Limitations (intentional — SQLite ALTER is restricted):
  - Adds missing tables, missing columns, missing indexes.
  - DOES NOT drop columns/tables/indexes (warns instead).
  - DOES NOT change column types/defaults/constraints (warns instead).
  - Anything in those categories needs a hand-written migration: rebuild the
    table (CREATE new, INSERT SELECT, DROP old, RENAME) — see SQLite docs.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

BASE = Path(__file__).parent
DB_PATH = BASE / "jobs.db"
SCHEMA_PATH = BASE / "schema.sql"


# ----------------- introspection -----------------

def _columns(conn: sqlite3.Connection, table: str) -> dict[str, dict]:
    cols = {}
    for cid, name, ctype, notnull, dflt, pk in conn.execute(
        f"PRAGMA table_info({table})"
    ):
        cols[name] = {
            "type": ctype,
            "notnull": notnull,
            "default": dflt,
            "pk": pk,
        }
    return cols


def snapshot(conn: sqlite3.Connection) -> dict:
    """Return {tables: {name: {sql, cols}}, indexes: {name: sql}, triggers: {name: sql}}."""
    tables: dict[str, dict] = {}
    for name, sql in conn.execute(
        "SELECT name, sql FROM sqlite_master "
        "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ):
        tables[name] = {"sql": sql, "cols": _columns(conn, name)}

    indexes: dict[str, str] = {}
    for name, sql in conn.execute(
        "SELECT name, sql FROM sqlite_master "
        "WHERE type='index' AND name NOT LIKE 'sqlite_%' AND sql IS NOT NULL "
        "ORDER BY name"
    ):
        indexes[name] = sql

    triggers: dict[str, str] = {}
    for name, sql in conn.execute(
        "SELECT name, sql FROM sqlite_master "
        "WHERE type='trigger' AND sql IS NOT NULL ORDER BY name"
    ):
        triggers[name] = sql

    return {"tables": tables, "indexes": indexes, "triggers": triggers}


def declared_snapshot(schema_path: Path = SCHEMA_PATH) -> dict:
    mem = sqlite3.connect(":memory:")
    mem.executescript(schema_path.read_text())
    snap = snapshot(mem)
    mem.close()
    return snap


# ----------------- diffing -----------------

def _column_alter(table: str, name: str, defn: dict) -> str:
    parts = [f"{name} {defn['type']}".rstrip()]
    if defn["notnull"] and defn["default"] is not None:
        parts.append("NOT NULL")
    if defn["default"] is not None:
        parts.append(f"DEFAULT {defn['default']}")
    return f"ALTER TABLE {table} ADD COLUMN {' '.join(parts)};"


def _col_differs(a: dict, b: dict) -> bool:
    return (
        (a["type"] or "").upper() != (b["type"] or "").upper()
        or a["notnull"] != b["notnull"]
        or str(a["default"]) != str(b["default"])
        or a["pk"] != b["pk"]
    )


def plan(declared: dict, actual: dict) -> tuple[list[str], list[str]]:
    """Return (statements_to_run, warnings_for_human)."""
    statements: list[str] = []
    warnings: list[str] = []

    # Missing tables -> CREATE.
    for tname, t in declared["tables"].items():
        if tname not in actual["tables"]:
            statements.append(t["sql"].strip().rstrip(";") + ";")
            continue
        # Missing columns -> ALTER ADD COLUMN.
        actual_cols = actual["tables"][tname]["cols"]
        for cname, cdef in t["cols"].items():
            if cname not in actual_cols:
                statements.append(_column_alter(tname, cname, cdef))
            elif _col_differs(cdef, actual_cols[cname]):
                warnings.append(
                    f"column {tname}.{cname} differs (declared "
                    f"type={cdef['type']!r} notnull={cdef['notnull']} "
                    f"default={cdef['default']!r}; actual "
                    f"type={actual_cols[cname]['type']!r} "
                    f"notnull={actual_cols[cname]['notnull']} "
                    f"default={actual_cols[cname]['default']!r}). "
                    "SQLite ALTER cannot do this automatically — manual rebuild needed."
                )
        for cname in actual_cols:
            if cname not in t["cols"]:
                warnings.append(
                    f"column {tname}.{cname} exists in DB but not in schema.sql "
                    "(not auto-dropped)"
                )

    for tname in actual["tables"]:
        if tname not in declared["tables"]:
            warnings.append(
                f"table {tname} exists in DB but not in schema.sql (not auto-dropped)"
            )

    # Missing indexes -> CREATE.
    for iname, isql in declared["indexes"].items():
        if iname not in actual["indexes"]:
            statements.append(isql.strip().rstrip(";") + ";")

    for iname in actual["indexes"]:
        if iname not in declared["indexes"]:
            warnings.append(
                f"index {iname} exists in DB but not in schema.sql (not auto-dropped)"
            )

    # Missing triggers -> CREATE.
    for tname, tsql in declared.get("triggers", {}).items():
        if tname not in actual.get("triggers", {}):
            statements.append(tsql.strip().rstrip(";") + ";")

    for tname in actual.get("triggers", {}):
        if tname not in declared.get("triggers", {}):
            warnings.append(
                f"trigger {tname} exists in DB but not in schema.sql (not auto-dropped)"
            )

    return statements, warnings


# ----------------- public API -----------------

def apply_migrations(conn: sqlite3.Connection, *, verbose: bool = False) -> int:
    """Bring `conn` into line with schema.sql. Returns number of statements run."""
    stmts, warnings = plan(declared_snapshot(), snapshot(conn))
    for w in warnings:
        print(f"  warning: {w}", file=sys.stderr)
    for s in stmts:
        if verbose:
            print(f"  apply: {s}")
        conn.execute(s)
    if stmts:
        conn.commit()
    return len(stmts)


# ----------------- CLI -----------------

def _cmd_status() -> int:
    conn = sqlite3.connect(DB_PATH)
    stmts, warnings = plan(declared_snapshot(), snapshot(conn))
    conn.close()
    if not stmts and not warnings:
        print("up to date.")
        return 0
    if stmts:
        print("pending statements:")
        for s in stmts:
            print(f"  {s}")
    if warnings:
        print("warnings:")
        for w in warnings:
            print(f"  - {w}")
    return 0 if not stmts else 1  # nonzero if drift, like terraform plan


def _cmd_migrate() -> int:
    conn = sqlite3.connect(DB_PATH)
    n = apply_migrations(conn, verbose=True)
    conn.close()
    print(f"applied {n} statements.")
    return 0


def _cmd_dump() -> int:
    conn = sqlite3.connect(DB_PATH)
    snap = snapshot(conn)
    for t in snap["tables"].values():
        print((t["sql"] or "").rstrip() + ";\n")
    for sql in snap["indexes"].values():
        print(sql.rstrip() + ";")
    conn.close()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status",  help="show pending changes (no writes)")
    sub.add_parser("migrate", help="apply pending changes")
    sub.add_parser("dump",    help="print the live schema")
    args = ap.parse_args()
    return {"status": _cmd_status, "migrate": _cmd_migrate, "dump": _cmd_dump}[args.cmd]()


if __name__ == "__main__":
    sys.exit(main())
