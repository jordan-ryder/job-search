#!/usr/bin/env python3
"""
Estimate company size for rows in jobs.db.

Adds four columns to `jobs`:
  company_size          — integer headcount estimate, or NULL if unknown
  company_size_source   — 'headcount' | 'funding_stage' | 'llm_guess' | NULL
  regex_checked         — 1 once the regex pass has scanned the row
  fetch_checked         — 1 once Claude Haiku has scanned the row

Each run does both passes, but only on rows that haven't been checked yet, so
re-running is idempotent and Ctrl+C is safe to resume:

  - Regex pass scans every row where regex_checked = 0, sets company_size on
    matches, then marks all scanned rows regex_checked = 1.
  - Haiku pass scans every row where company_size IS NULL AND fetch_checked = 0,
    sets company_size, company_size_source, fetch_checked = 1 per row.

`company_size_source` lets you tell apart hard signals from rough estimates:
  headcount      — explicit number stated in the post ("team of 12")
  funding_stage  — midpoint guess from funding stage (Series A -> 25, etc.)
  llm_guess      — Haiku used general knowledge of the company

Requires: pip install anthropic pydantic
Env:     ANTHROPIC_API_KEY (only for the Haiku pass)

Usage:
    python3 size_companies.py                  # full run, picks up where it left off
    python3 size_companies.py --skip-llm       # regex only, no API calls
    python3 size_companies.py --limit 50       # cap LLM pass for testing
    python3 size_companies.py --dry-run        # print but don't write
"""

import argparse
import re
import sqlite3
import sys
from pathlib import Path
from typing import Literal

import anthropic
from pydantic import BaseModel

BASE = Path(__file__).parent
DB_PATH = BASE / "jobs.db"

MODEL = "claude-haiku-4-5"

SizeSource = Literal["headcount", "funding_stage", "llm_guess"]

# Funding-stage midpoints. Used by both the regex pass (when matching a stage
# hint) and described to the LLM so its `funding_stage` source is comparable.
STAGE_MIDPOINTS = {
    "public_or_late": 5000,  # Series D+ / public / IPO'd
    "series_c": 500,
    "series_b": 100,
    "series_a": 25,
    "seed_or_earlier": 5,    # bootstrapped / seed / pre-seed / YC / early-stage
}


# Tight headcount patterns — only ones that bind to a "team/company/employees"
# context, so we don't false-match on "looking for 5 engineers" hiring intent.
RANGE_PATTERN = re.compile(
    r"\b(\d{1,5})\s*[-–]\s*(\d{1,5})\s*(?:employees|people|FTEs?)\b",
    re.I,
)
HEADCOUNT_PATTERNS = [
    re.compile(r"\bteam of\s+(\d{1,5})\b", re.I),
    re.compile(r"\b(\d{1,5})[-\s]person\s+(?:team|company|startup|org)\b", re.I),
    re.compile(r"\b(\d{1,5})\+?\s*(?:employees|FTEs?)\b", re.I),
    re.compile(r"\b(?:we['’]?re|we are)\s+a\s+(?:team\s+of\s+)?(\d{1,5})\b", re.I),
]

# Funding stage fallback. Order matters — most specific first.
STAGE_HINTS: list[tuple[re.Pattern, int]] = [
    (re.compile(r"\b(?:public(?:ly)?\s+(?:traded|listed)|NYSE|NASDAQ|FTSE|IPO['’]?d?)\b", re.I), STAGE_MIDPOINTS["public_or_late"]),
    (re.compile(r"\bSeries\s+[D-Z]\b", re.I), STAGE_MIDPOINTS["public_or_late"]),
    (re.compile(r"\bSeries\s+C\b", re.I), STAGE_MIDPOINTS["series_c"]),
    (re.compile(r"\bSeries\s+B\b", re.I), STAGE_MIDPOINTS["series_b"]),
    (re.compile(r"\bSeries\s+A\b", re.I), STAGE_MIDPOINTS["series_a"]),
    (re.compile(r"\b(?:seed[-\s]stage|pre[-\s]?seed)\b", re.I), STAGE_MIDPOINTS["seed_or_earlier"]),
    (re.compile(r"\b(?:bootstrapped|early[-\s]stage|YC\s+[WSF]\d{2}|founding\s+(?:team|engineer))\b", re.I), STAGE_MIDPOINTS["seed_or_earlier"]),
]


def regex_extract(text: str) -> tuple[int, SizeSource] | None:
    """Return (size, source) if a confident match, else None."""
    if not text:
        return None

    m = RANGE_PATTERN.search(text)
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
        return (lo + hi) // 2, "headcount"

    for pat in HEADCOUNT_PATTERNS:
        m = pat.search(text)
        if m:
            n = int(m.group(1))
            if 1 <= n <= 100_000:
                return n, "headcount"

    for pat, midpoint in STAGE_HINTS:
        if pat.search(text):
            return midpoint, "funding_stage"

    return None


SYSTEM_INSTRUCTIONS = f"""\
You estimate the headcount of a company from a job posting.

Return:
- company_size: integer estimate of headcount, or null if you genuinely cannot tell.
- source: which signal you used. One of:
    'headcount'      — the post stated a headcount or team size; use the stated
                       number (or midpoint of a range)
    'funding_stage'  — estimated from a funding stage with no headcount mentioned;
                       use these midpoints:
                         bootstrapped / seed / "early-stage" / YC batch  -> {STAGE_MIDPOINTS['seed_or_earlier']}
                         Series A                                        -> {STAGE_MIDPOINTS['series_a']}
                         Series B                                        -> {STAGE_MIDPOINTS['series_b']}
                         Series C                                        -> {STAGE_MIDPOINTS['series_c']}
                         Series D+ / public / IPO'd / publicly-traded    -> {STAGE_MIDPOINTS['public_or_late']}
    'llm_guess'      — estimated from your general knowledge of this company
                       (e.g. 'well-known FAANG' -> use a realistic number)
  Use null if company_size is null.
- evidence: short phrase quoting or paraphrasing the signal you used
  (e.g. '"team of 12"', 'Series B funding', 'well-known FAANG').

If you genuinely cannot tell, return null for both company_size and source.
Don't guess wildly.
"""


class SizeGuess(BaseModel):
    company_size: int | None
    source: SizeSource | None
    evidence: str


def ensure_columns(conn: sqlite3.Connection) -> None:
    cols = {r[1]: r[2] for r in conn.execute("PRAGMA table_info(jobs)")}

    # Migrate stale TEXT company_size from a prior version of this script.
    if cols.get("company_size") == "TEXT":
        non_null = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE company_size IS NOT NULL"
        ).fetchone()[0]
        if non_null:
            sys.exit(
                "company_size exists as TEXT and has data; "
                "refusing to migrate to INTEGER automatically"
            )
        conn.execute("ALTER TABLE jobs DROP COLUMN company_size")
        cols.pop("company_size")
        print("migrated company_size: TEXT -> INTEGER (was empty)")

    added = []
    if "company_size" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN company_size INTEGER")
        added.append("company_size")
    if "company_size_source" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN company_size_source TEXT")
        added.append("company_size_source")
    if "regex_checked" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN regex_checked INTEGER DEFAULT 0")
        added.append("regex_checked")
    if "fetch_checked" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN fetch_checked INTEGER DEFAULT 0")
        added.append("fetch_checked")
    if added:
        conn.commit()
        print(f"added columns: {', '.join(added)}")


def run_regex_pass(conn: sqlite3.Connection, dry_run: bool) -> None:
    rows = conn.execute(
        "SELECT id, raw_text FROM jobs WHERE regex_checked = 0"
    ).fetchall()

    if not rows:
        print("regex pass: nothing to check")
        return

    matches: list[tuple[int, str, int]] = []  # (size, source, id)
    for row in rows:
        result = regex_extract(row["raw_text"])
        if result is not None:
            size, source = result
            matches.append((size, source, row["id"]))

    print(f"regex pass: {len(matches)}/{len(rows)} matched")

    if dry_run:
        return

    if matches:
        conn.executemany(
            "UPDATE jobs SET company_size=?, company_size_source=? WHERE id=?",
            matches,
        )
    conn.executemany(
        "UPDATE jobs SET regex_checked=1 WHERE id=?",
        [(r["id"],) for r in rows],
    )
    conn.commit()


def format_job_for_llm(row: sqlite3.Row) -> str:
    parts = []
    if row["company"]:
        parts.append(f"parsed_company={row['company']!r}")
    if row["title"]:
        parts.append(f"parsed_title={row['title']!r}")
    header = " | ".join(parts)
    return (header + "\n\n" if header else "") + "RAW POSTING:\n" + (row["raw_text"] or "")


def run_llm_pass(
    conn: sqlite3.Connection,
    dry_run: bool,
    order_clause: str,
    limit: int | None,
    min_score: int | None,
) -> None:
    where = ["company_size IS NULL", "fetch_checked = 0"]
    if min_score is not None:
        where.append(f"score >= {int(min_score)}")
    query = (
        "SELECT id, company, title, raw_text FROM jobs "
        f"WHERE {' AND '.join(where)} "
        f"ORDER BY {order_clause}"
    )
    if limit:
        query += f" LIMIT {int(limit)}"
    rows = conn.execute(query).fetchall()

    if not rows:
        print("llm pass: nothing left to size")
        return

    print(f"llm pass: sizing {len(rows)} rows with {MODEL} (cached system prompt)")

    system = [
        {
            "type": "text",
            "text": SYSTEM_INSTRUCTIONS,
            "cache_control": {"type": "ephemeral"},
        }
    ]

    client = anthropic.Anthropic(max_retries=4)

    cache_read_total = 0
    cache_write_total = 0
    input_total = 0
    output_total = 0
    sized = 0
    errored = 0

    for i, row in enumerate(rows, 1):
        try:
            resp = client.messages.parse(
                model=MODEL,
                max_tokens=200,
                system=system,
                messages=[{"role": "user", "content": format_job_for_llm(row)}],
                output_format=SizeGuess,
            )
        except anthropic.APIError as e:
            print(f"  [{i}/{len(rows)}] id={row['id']} API error: {e}", file=sys.stderr)
            errored += 1
            continue
        except Exception as e:
            print(f"  [{i}/{len(rows)}] id={row['id']} unexpected: {e}", file=sys.stderr)
            errored += 1
            continue

        parsed = resp.parsed_output
        if parsed is None:
            print(f"  [{i}/{len(rows)}] id={row['id']} no parsed output", file=sys.stderr)
            errored += 1
            continue
        u = resp.usage
        cr = getattr(u, "cache_read_input_tokens", 0) or 0
        cw = getattr(u, "cache_creation_input_tokens", 0) or 0
        cache_read_total += cr
        cache_write_total += cw
        input_total += u.input_tokens
        output_total += u.output_tokens

        size_str = f"{parsed.company_size:>5}" if parsed.company_size is not None else " null"
        src_str = parsed.source or "-"
        ev = parsed.evidence.replace("\n", " ")[:60]
        print(
            f"  [{i}/{len(rows)}] id={row['id']:<5} {size_str} {src_str:<14} | {ev}"
        )

        if not dry_run:
            conn.execute(
                "UPDATE jobs SET company_size=?, company_size_source=?, fetch_checked=1 "
                "WHERE id=?",
                (parsed.company_size, parsed.source, row["id"]),
            )
            conn.commit()
        sized += 1

    # Same pricing model as score_jobs.py: $1/MTok input, $5/MTok output,
    # cache reads ~0.1x input, cache writes ~1.25x input.
    est_cost = (
        (input_total * 1.00)
        + (cache_write_total * 1.25)
        + (cache_read_total * 0.10)
        + (output_total * 5.00)
    ) / 1_000_000

    print()
    print(f"llm pass done: {sized} sized, {errored} errored")
    print(
        f"tokens: input={input_total:,} "
        f"cache_read={cache_read_total:,} "
        f"cache_write={cache_write_total:,} "
        f"output={output_total:,}"
    )
    print(f"rough cost (Haiku 4.5): ${est_cost:.4f}")


def print_coverage(conn: sqlite3.Connection) -> None:
    sizes = [r[0] for r in conn.execute("SELECT company_size FROM jobs")]
    buckets = {"unknown (NULL)": 0, "1-10": 0, "11-50": 0, "51-200": 0, "201-1000": 0, "1000+": 0}
    for n in sizes:
        if n is None:
            buckets["unknown (NULL)"] += 1
        elif n <= 10:
            buckets["1-10"] += 1
        elif n <= 50:
            buckets["11-50"] += 1
        elif n <= 200:
            buckets["51-200"] += 1
        elif n <= 1000:
            buckets["201-1000"] += 1
        else:
            buckets["1000+"] += 1
    total = sum(buckets.values()) or 1
    print()
    print("coverage by size:")
    for label, n in buckets.items():
        print(f"  {label:<16} {n:>5}  ({n / total:.1%})")

    by_source = conn.execute(
        "SELECT COALESCE(company_size_source, 'NULL'), COUNT(*) "
        "FROM jobs GROUP BY 1 ORDER BY 2 DESC"
    ).fetchall()
    print()
    print("coverage by source:")
    for label, n in by_source:
        print(f"  {label:<16} {n:>5}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-llm", action="store_true", help="run only the regex pass, no API calls")
    ap.add_argument("--limit", type=int, default=None, help="cap rows in the LLM pass")
    ap.add_argument(
        "--min-score",
        type=int,
        default=None,
        help="LLM pass: only size jobs with score >= N (NULL scores excluded)",
    )
    ap.add_argument("--dry-run", action="store_true", help="print but don't write to DB")
    ap.add_argument(
        "--order",
        choices=("score", "newest", "oldest", "id"),
        default="score",
        help="order in which to process rows in the LLM pass (default: score)",
    )
    args = ap.parse_args()

    # NULLS LAST so unscored rows fall to the bottom when ordering by score.
    order_clause = {
        "score": "score DESC NULLS LAST, posted_at DESC",
        "newest": "posted_at DESC",
        "oldest": "posted_at ASC",
        "id": "id ASC",
    }[args.order]

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    ensure_columns(conn)
    run_regex_pass(conn, args.dry_run)

    if not args.skip_llm:
        run_llm_pass(conn, args.dry_run, order_clause, args.limit, args.min_score)

    print_coverage(conn)
    conn.close()


if __name__ == "__main__":
    main()
