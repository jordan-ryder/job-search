#!/usr/bin/env python3
"""
Score unreviewed rows in jobs.db against scoring_rubric.md using Claude Haiku 4.5.

- Reads context.md + scoring_rubric.md, sends them once as a cached system prompt.
- For each row where reviewed=0, asks the model for {score, interested, notes}.
- Writes back to jobs.db and commits per row, so Ctrl+C is safe to resume.

Requires: pip install anthropic pydantic
Env:     ANTHROPIC_API_KEY

Usage:
    python3 score_jobs.py                    # score every unreviewed row
    python3 score_jobs.py --limit 10         # try 10, inspect, then run full
    python3 score_jobs.py --dry-run          # print but don't write
"""

import argparse
import sqlite3
import sys
from pathlib import Path

import anthropic
from pydantic import BaseModel, Field

from prefilters import prefilter_job, load_company_blocklist

BASE = Path(__file__).parent
DB_PATH = BASE / "jobs.db"
CONTEXT_PATH = BASE / "prompts" / "context.md"
RUBRIC_PATH = BASE / "prompts" / "scoring_rubric.md"

MODEL = "claude-haiku-4-5"


# Pre-Haiku filter rules live in prefilters.py. Edit there to tune.

SYSTEM_INSTRUCTIONS = """\
You score job postings for Jordan's job search.

Read the context and rubric that follow, then score one job per message. Return
a single JSON object matching the caller's schema. Apply the rubric's scoring
math exactly:

  1. Check hard disqualifiers first. If any hit: score=0, interested=0.
  2. Otherwise start at baseline 5, add and subtract per the factors.
  3. Clamp to integer 1-10 (round half up).
  4. interested=1 iff score >= 7, else interested=0.
  5. notes: 1-2 sentences.
     - For disqualifiers: 'Skip. <reason>.'
     - For scored rows:   'Score: N. <reasoning tied to rubric factors>.'

Lean toward scoring rather than disqualifying when uncertain — the 5-7 band is
Jordan's manual second-pass band, so don't filter too aggressively.

HN "Who is Hiring" comments are messy: parsed company/title/location may be
empty or wrong. Always read the raw posting and use that as the source of truth.
If a single post lists multiple roles, score for the most senior data-relevant
role. If a post is a candidate 'SEEKING' entry (belongs to the Who-wants-to-be-
hired thread), treat as not-a-job: score=0, notes='Skip. Candidate post, not a
job listing.'
"""


class Score(BaseModel):
    score: int = Field(..., ge=0, le=10)
    interested: int = Field(..., ge=0, le=1)
    notes: str


def build_system(context: str, rubric: str) -> list[dict]:
    # Three stable blocks; cache_control on the last one caches the whole prefix.
    return [
        {"type": "text", "text": SYSTEM_INSTRUCTIONS},
        {"type": "text", "text": f"# Job Search Context\n\n{context}"},
        {
            "type": "text",
            "text": f"# Scoring Rubric\n\n{rubric}",
            "cache_control": {"type": "ephemeral"},
        },
    ]


def format_job(row: sqlite3.Row) -> str:
    header = [
        f"id={row['id']}",
        f"source={row['source']}",
        f"remote_flag={row['remote']}",
    ]
    if row["company"]:
        header.append(f"parsed_company={row['company']!r}")
    if row["title"]:
        header.append(f"parsed_title={row['title']!r}")
    if row["location"]:
        header.append(f"parsed_location={row['location']!r}")
    if row["posted_at"]:
        header.append(f"posted_at={row['posted_at']}")
    return " | ".join(header) + "\n\nRAW POSTING:\n" + (row["raw_text"] or "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None, help="max rows to score this run")
    ap.add_argument("--dry-run", action="store_true", help="print but don't write to DB")
    ap.add_argument(
        "--order",
        choices=("newest", "oldest", "id"),
        default="newest",
        help="order in which to process unreviewed rows",
    )
    args = ap.parse_args()

    context = CONTEXT_PATH.read_text()
    rubric = RUBRIC_PATH.read_text()
    system = build_system(context, rubric)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    order_clause = {
        "newest": "posted_at DESC",
        "oldest": "posted_at ASC",
        "id": "id ASC",
    }[args.order]
    query = f"SELECT * FROM jobs WHERE reviewed = 0 ORDER BY {order_clause}"
    if args.limit:
        query += f" LIMIT {int(args.limit)}"
    rows = conn.execute(query).fetchall()

    # Pre-load the company blocklist once so per-row prefilter calls are O(1).
    blocklist = load_company_blocklist(conn)
    if blocklist:
        print(f"company blocklist: {len(blocklist)} companies flagged in companies.skip_reason")

    total = len(rows)
    if total == 0:
        print("nothing to score (reviewed=0 count is 0)")
        return

    print(f"scoring {total} rows with {MODEL} (prompt-cached system, ~5K tokens)")

    # The SDK auto-retries 429/5xx with exponential backoff (default max_retries=2).
    # Bump it a little for resilience on long batches.
    client = anthropic.Anthropic(max_retries=4)

    cache_read_total = 0
    cache_write_total = 0
    input_total = 0
    output_total = 0
    scored = 0
    prefiltered = 0
    errored = 0

    for i, row in enumerate(rows, 1):
        # Pre-filter before spending a Haiku call. Order: company blocklist
        # → title rules → description rules. All rules live in prefilters.py
        # — `python prefilters.py audit` reports current coverage.
        reason = prefilter_job(
            row["title"], row["raw_text"],
            company=row["company"], blocklist=blocklist,
        )
        if reason is not None:
            note = f"Skip. Pre-filtered ({reason})."
            if not args.dry_run:
                conn.execute(
                    "UPDATE jobs SET reviewed=1, score=0, interested=0, notes=? WHERE id=?",
                    (note, row["id"]),
                )
                conn.commit()
            prefiltered += 1
            if i == 1 or i % 50 == 0 or i == total:
                print(f"  [{i}/{total}] prefiltered={prefiltered} scored={scored} errored={errored}")
            continue

        try:
            resp = client.messages.parse(
                model=MODEL,
                max_tokens=500,
                system=system,
                messages=[{"role": "user", "content": format_job(row)}],
                output_format=Score,
            )
        except anthropic.APIError as e:
            print(f"  [{i}/{total}] id={row['id']} API error: {e}", file=sys.stderr)
            errored += 1
            continue
        except Exception as e:
            print(f"  [{i}/{total}] id={row['id']} unexpected: {e}", file=sys.stderr)
            errored += 1
            continue

        parsed = resp.parsed_output
        u = resp.usage
        cr = getattr(u, "cache_read_input_tokens", 0) or 0
        cw = getattr(u, "cache_creation_input_tokens", 0) or 0
        cache_read_total += cr
        cache_write_total += cw
        input_total += u.input_tokens
        output_total += u.output_tokens

        tag = "INT" if parsed.interested else "   "
        title_preview = (row["title"] or "?").replace("\n", " ").strip()[:55]
        note_preview = parsed.notes.replace("\n", " ")[:80]
        print(
            f"  [{i}/{total}] id={row['id']:<5} score={parsed.score:>2} {tag} "
            f"cache_r={cr:>5} | {title_preview:<55} | {note_preview}"
        )

        if not args.dry_run:
            conn.execute(
                "UPDATE jobs SET reviewed=1, score=?, interested=?, notes=? WHERE id=?",
                (parsed.score, parsed.interested, parsed.notes, row["id"]),
            )
            conn.commit()
        scored += 1

    conn.close()

    # Rough cost estimate at Haiku 4.5 pricing: $1 / 1M input, $5 / 1M output.
    # Cached reads billed at ~0.1x input price; cache writes at ~1.25x.
    est_cost = (
        (input_total * 1.00)
        + (cache_write_total * 1.25)
        + (cache_read_total * 0.10)
        + (output_total * 5.00)
    ) / 1_000_000

    print()
    print(f"done: {scored} scored, {prefiltered} prefiltered (skipped Haiku), {errored} errored")
    print(
        f"tokens: input={input_total:,} "
        f"cache_read={cache_read_total:,} "
        f"cache_write={cache_write_total:,} "
        f"output={output_total:,}"
    )
    print(f"rough cost (Haiku 4.5): ${est_cost:.4f}")


if __name__ == "__main__":
    main()
