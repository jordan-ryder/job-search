#!/usr/bin/env python3
"""
Populate the companies table by aggregating jobs.

Each row in jobs is a single posting; this script rolls those up by normalized
company name and writes one row per company:

  - size, size_source     rolled up from jobs.company_size (priority:
                          headcount > funding_stage > llm_guess; median within).
                          Stays NULL when we genuinely don't know.
  - ats_provider/url/slug regex-extracted from raw_text (Greenhouse, Lever,
                          Ashby, Workable)
  - regex_checked         1 iff any underlying job has been through the regex pass
  - fetch_checked         1 iff Haiku has been attempted on any underlying job
                          (the lookup pass uses this to skip re-running Haiku
                          on companies we've already given up on)
  - job_count             how many job postings we've seen for this company

Writes a row for every company, even ones with unknown size — that way the
lookup pass can short-circuit Haiku for repeat encounters. --min-size is now
informational only (counted in stats), not a filter.

Idempotent — re-running upserts and refreshes data as new rows arrive.

Usage:
    python populate_companies.py
    python populate_companies.py --dry-run
"""

import argparse
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

import db

DB_PATH = Path(__file__).parent / "jobs.db"

# Higher = stronger signal. None means the row had no size guess at all.
SIZE_SOURCE_PRIORITY = {"headcount": 3, "funding_stage": 2, "llm_guess": 1, None: 0}

# If a company has at least this many jobs whose raw_text mentions clearance/
# ITAR/polygraph AND it's a meaningful share of their total, flag the company
# so every future job from them skips Haiku.
DEFENSE_MIN_HITS = 3
DEFENSE_MIN_SHARE = 0.30

DEFENSE_RE = re.compile(
    r"\b(?:TS/SCI|TS\\SCI|TS-SCI|top\s+secret|secret\s+clearance|sci\s+clearance"
    r"|active\s+clearance|active\s+security\s+clearance|ITAR|EAR99|export[- ]controlled|polygraph)\b",
    re.I,
)

ATS_PATTERNS: list[tuple[str, re.Pattern]] = [
    # Greenhouse: new "job-boards" host (incl. EU), legacy "boards" host, and embed widget.
    ("greenhouse", re.compile(r"https?://(?:job-boards|boards)(?:\.eu)?\.greenhouse\.io/([a-z0-9_-]+)", re.I)),
    ("greenhouse", re.compile(r"https?://[a-z0-9.-]*greenhouse\.io/embed/job_app\?for=([a-z0-9_-]+)", re.I)),
    ("lever",      re.compile(r"https?://jobs\.lever\.co/([a-z0-9_-]+)", re.I)),
    ("ashby",      re.compile(r"https?://(?:jobs|app)\.ashbyhq\.com/([a-z0-9_-]+)", re.I)),
    ("workable",   re.compile(r"https?://(?:apply|jobs)\.workable\.com/([a-z0-9_-]+)", re.I)),
]

# Strip a single trailing legal/marketing suffix so "Acme, Inc." == "Acme".
_SUFFIX_RE = re.compile(
    r"[\s,]+(?:inc\.?|llc|ltd\.?|gmbh|co\.?|corp\.?|corporation|company|technologies|labs)\.?$",
    re.I,
)


def normalize_name(name: str) -> str:
    if not name:
        return ""
    s = name.strip().lower()
    s = _SUFFIX_RE.sub("", s).strip()
    return re.sub(r"\s+", " ", s)


def detect_ats(text: str) -> tuple[str, str, str] | None:
    """Return (provider, url, slug) for the first ATS link found, else None."""
    if not text:
        return None
    for provider, pat in ATS_PATTERNS:
        m = pat.search(text)
        if m:
            return provider, m.group(0), m.group(1).lower()
    return None


def ensure_columns(conn: sqlite3.Connection) -> None:
    """Reconcile the live DB to schema.sql via db.apply_migrations."""
    db.apply_migrations(conn, verbose=True)


def roll_up_size(rows: list[sqlite3.Row]) -> tuple[int | None, str | None]:
    sized = [r for r in rows if r["company_size"] is not None]
    if not sized:
        return None, None
    best = max(SIZE_SOURCE_PRIORITY.get(r["company_size_source"], 0) for r in sized)
    pool = [r for r in sized if SIZE_SOURCE_PRIORITY.get(r["company_size_source"], 0) == best]
    sizes = sorted(r["company_size"] for r in pool)
    return sizes[len(sizes) // 2], pool[0]["company_size_source"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-size", type=int, default=20, help="skip companies with size < N")
    ap.add_argument("--dry-run", action="store_true", help="print but don't write")
    args = ap.parse_args()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    ensure_columns(conn)

    rows = conn.execute(
        """
        SELECT id, source, company, raw_text, company_size, company_size_source,
               regex_checked, fetch_checked
        FROM jobs
        WHERE company IS NOT NULL AND TRIM(company) != ''
        """
    ).fetchall()

    groups: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for r in rows:
        norm = normalize_name(r["company"])
        if norm:
            groups[norm].append(r)

    upserted = ats_found = unknown_size = below_min = blocked_count = 0

    for norm, group in groups.items():
        size, size_source = roll_up_size(group)
        # Track which buckets things fall into, but don't skip — we want a row
        # for every company so the lookup pass can short-circuit Haiku later.
        if size is None:
            unknown_size += 1
        elif size < args.min_size:
            below_min += 1

        # Canonical display name = most common original casing.
        names = Counter(r["company"].strip() for r in group)
        canonical = names.most_common(1)[0][0]

        ats: tuple[str, str, str] | None = None
        defense_hits = 0
        for r in group:
            text = r["raw_text"] or ""
            if ats is None:
                ats = detect_ats(text)
            if DEFENSE_RE.search(text):
                defense_hits += 1
        if ats:
            ats_found += 1

        # Auto-flag clear-pattern defense contractors so every job from them
        # skips Haiku going forward. Conservative thresholds avoid catching
        # generalist companies that happen to have a few defense roles.
        skip_reason: str | None = None
        if defense_hits >= DEFENSE_MIN_HITS and (defense_hits / len(group)) >= DEFENSE_MIN_SHARE:
            skip_reason = "defense"

        # Prefer 'hn' as the source label when any HN row contributed.
        source = "hn" if any(r["source"] == "hn" for r in group) else group[0]["source"]

        # OR semantics: a flag is 1 if any underlying job has been through that
        # pass, regardless of which one produced the canonical size.
        regex_checked = 1 if any(r["regex_checked"] for r in group) else 0
        fetch_checked = 1 if any(r["fetch_checked"] for r in group) else 0

        if not args.dry_run:
            conn.execute(
                """
                INSERT INTO companies
                  (name, source, size, size_source, ats_provider, ats_url, ats_slug,
                   job_count, regex_checked, fetch_checked, skip_reason, first_seen)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(name, source) DO UPDATE SET
                  size = excluded.size,
                  size_source = excluded.size_source,
                  ats_provider = COALESCE(excluded.ats_provider, companies.ats_provider),
                  ats_url      = COALESCE(excluded.ats_url,      companies.ats_url),
                  ats_slug     = COALESCE(excluded.ats_slug,     companies.ats_slug),
                  job_count    = excluded.job_count,
                  regex_checked = excluded.regex_checked,
                  fetch_checked = excluded.fetch_checked,
                  -- Preserve any manually-set skip_reason; only fill in if blank.
                  skip_reason  = COALESCE(companies.skip_reason, excluded.skip_reason)
                  -- last_fetch_checked intentionally not touched here;
                  -- size_companies.py owns that column.
                """,
                (
                    canonical, source, size, size_source,
                    ats[0] if ats else None,
                    ats[1] if ats else None,
                    ats[2] if ats else None,
                    len(group),
                    regex_checked, fetch_checked,
                    skip_reason,
                ),
            )
        upserted += 1
        if skip_reason:
            blocked_count += 1

    if not args.dry_run:
        conn.commit()

    print(f"groups scanned: {len(groups)}")
    print(f"  upserted:                {upserted}")
    print(f"    with ATS link:         {ats_found}")
    print(f"    size unknown:          {unknown_size}")
    print(f"    size < {args.min_size}:             {below_min}")
    print(f"    auto-flagged (skip_reason):    {blocked_count}")

    if not args.dry_run:
        print("\nats coverage in companies:")
        for prov, n in conn.execute(
            "SELECT COALESCE(ats_provider, '(none)'), COUNT(*) "
            "FROM companies GROUP BY 1 ORDER BY 2 DESC"
        ):
            print(f"  {prov:<12} {n}")

    conn.close()


if __name__ == "__main__":
    main()
