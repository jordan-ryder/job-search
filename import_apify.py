#!/usr/bin/env python3
"""
Import Apify hiring.cafe scrape exports into jobs.db.

Reads every *.json file in ./scrapes/, inserts each posting into `jobs` with
source='apify', and moves the file to ./scrapes/imported/ once it's fully ingested.

Apify ships richer metadata than HN/ATS scrapes give us — most notably
enriched_company_data.nb_employees (a hard headcount signal). When present,
that value is written straight into jobs.company_size with
company_size_source='headcount' and regex_checked=lookup_checked=fetch_checked=1
so the sizing pipeline doesn't re-process these rows.

Idempotent: INSERT OR IGNORE on (source, source_id) means re-running the same
file is a no-op. New files in ./scrapes/ are picked up automatically.

Usage:
    python import_apify.py
    python import_apify.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from html import unescape
from pathlib import Path

import db

BASE = Path(__file__).parent
DB_PATH = BASE / "jobs.db"
SCRAPES_DIR = BASE / "scrapes"
IMPORTED_DIR = SCRAPES_DIR / "imported"


def _strip_html(s: str | None) -> str:
    if not s:
        return ""
    return unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s))).strip()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_utc_iso(s: str | None) -> str | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return s
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _coerce_headcount(v) -> int | None:
    """Apify returns headcount as a number, a string ('50'), or '' for unknown."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)) and v > 0:
        return int(v)
    if isinstance(v, str):
        m = re.search(r"\d+", v)
        if m:
            n = int(m.group())
            return n if n > 0 else None
    return None


def map_record(r: dict) -> dict | None:
    """Flatten one Apify record into a row matching the jobs table.
    Auto-detects the actor's format (rich `job_information`-shaped or the
    flatter newer one). Returns None if a required field is missing."""
    obj_id = r.get("objectID") or r.get("id")
    if not obj_id:
        return None

    if "job_information" in r:
        # Older "blackfalcondata/hiringcafe-scraper"-style: deeply nested.
        info = r.get("job_information") or {}
        proc = r.get("v5_processed_job_data") or {}
        enriched = r.get("enriched_company_data") or {}
        title = info.get("title") or proc.get("core_job_title")
        apply_url = r.get("apply_url")
        company = enriched.get("name") or proc.get("company_name")
        description = info.get("description")
        location = proc.get("formatted_workplace_location")
        workplace_type = proc.get("workplace_type")
        posted_at_raw = proc.get("estimated_publish_date")
        headcount_raw = enriched.get("nb_employees")
    else:
        # Newer flat-shape actor: top-level fields, no separate publish date —
        # fall back to scrapedAt so notebook date filters still match.
        title = r.get("title") or r.get("coreJobTitle")
        apply_url = r.get("applyUrl") or r.get("apply_url")
        company = r.get("companyName")
        description = r.get("description")
        cities = r.get("workplaceCities") or []
        countries = r.get("workplaceCountries") or []
        location = ", ".join(filter(None, cities + countries)) or r.get("companyHqCountry")
        workplace_type = r.get("workplaceType")
        posted_at_raw = r.get("scrapedAt")
        headcount_raw = r.get("companyEmployees")

    if not apply_url or not title:
        return None

    headcount = _coerce_headcount(headcount_raw)
    return {
        "source_id":           obj_id,
        "company":             company,
        "title":               title,
        "location":            location or None,
        "remote":              1 if (workplace_type or "").lower() == "remote" else 0,
        "url":                 apply_url,
        "posted_at":           _to_utc_iso(posted_at_raw),
        "raw_text":            (_strip_html(description)[:20000]) or None,
        "company_size":        headcount,
        "company_size_source": "headcount" if headcount is not None else None,
    }


def insert_record(conn: sqlite3.Connection, row: dict) -> bool:
    """Returns True if a new row was inserted."""
    cur = conn.cursor()
    cur.execute(
        """
        INSERT OR IGNORE INTO jobs
          (source, source_id, company, title, location, remote, url,
           posted_at, raw_text,
           company_size, company_size_source,
           regex_checked, lookup_checked, fetch_checked,
           fetched_at)
        VALUES ('apify', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 1, 1, ?)
        """,
        (
            row["source_id"], row["company"], row["title"], row["location"],
            row["remote"], row["url"], row["posted_at"], row["raw_text"],
            row["company_size"], row["company_size_source"],
            _now_iso(),
        ),
    )
    return bool(cur.rowcount)


def import_file(conn: sqlite3.Connection, path: Path, dry_run: bool) -> tuple[int, int]:
    """Import one JSON file. Returns (records_seen, rows_inserted)."""
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        print(f"  [{path.name}] JSON decode failed: {e}", file=sys.stderr)
        return 0, 0

    if not isinstance(data, list):
        print(f"  [{path.name}] expected JSON array, got {type(data).__name__}", file=sys.stderr)
        return 0, 0

    inserted = skipped = 0
    for r in data:
        row = map_record(r)
        if row is None:
            skipped += 1
            continue
        if dry_run:
            inserted += 1
            continue
        if insert_record(conn, row):
            inserted += 1

    if not dry_run:
        conn.commit()
    return len(data), inserted


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="parse and map, but don't write")
    args = ap.parse_args()

    if not SCRAPES_DIR.is_dir():
        print(f"no {SCRAPES_DIR.name}/ directory; nothing to import")
        return 0

    # glob only the top-level — already-imported files live in scrapes/imported/.
    files = sorted(p for p in SCRAPES_DIR.glob("*.json") if p.is_file())
    if not files:
        print(f"no JSON files in {SCRAPES_DIR.name}/; nothing to import")
        return 0

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    db.apply_migrations(conn)

    IMPORTED_DIR.mkdir(exist_ok=True)

    grand_seen = grand_inserted = 0
    for path in files:
        seen, inserted = import_file(conn, path, args.dry_run)
        grand_seen += seen
        grand_inserted += inserted
        print(f"  {path.name}: {seen} records, {inserted} inserted")
        # Only move once we've successfully written + committed.
        if not args.dry_run and seen > 0:
            target = IMPORTED_DIR / path.name
            shutil.move(str(path), str(target))
            print(f"    → moved to {SCRAPES_DIR.name}/{IMPORTED_DIR.name}/{path.name}")

    conn.close()
    print()
    print(f"summary: {len(files)} file(s), {grand_seen} records seen, {grand_inserted} new rows inserted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
