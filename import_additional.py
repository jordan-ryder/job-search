#!/usr/bin/env python3
"""
Import individual job URLs from a flat-text file (one URL per line).

For URLs hosted on ATS systems we already scrape (Greenhouse, Lever, Ashby,
Workable), the company slug is added to `companies` so the next scrape_ats.py
run pulls the entire portfolio — much higher value than ingesting one job.

URLs from ATSes we don't scrape are skipped with a warning — they'd otherwise
land in `jobs` as bookmark rows with no description, which is just noise. To
add a new ATS, register a fetcher in scrape_ats.py and an extractor pattern
in SUPPORTED_PATTERNS below.

Usage:
    python import_additional.py
    python import_additional.py --file additional_posts.txt
    python import_additional.py --dry-run
"""

from __future__ import annotations

import argparse
import re
import sqlite3
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

import db

BASE = Path(__file__).parent
DB_PATH = BASE / "jobs.db"
DEFAULT_FILE = BASE / "additional_posts.txt"


# Ordered: matched providers we can scrape full portfolios for.
SUPPORTED_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("greenhouse", re.compile(r"https?://(?:job-boards|boards)(?:\.eu)?\.greenhouse\.io/([a-z0-9_-]+)", re.I)),
    ("lever",      re.compile(r"https?://jobs\.lever\.co/([a-z0-9_-]+)", re.I)),
    ("ashby",      re.compile(r"https?://(?:jobs|app)\.ashbyhq\.com/([a-z0-9_-]+)", re.I)),
    # Workable widget form (NOT /view/<job> — that doesn't expose a slug).
    ("workable",   re.compile(r"https?://apply\.workable\.com/([a-z0-9_-]+)/?$", re.I)),
    ("breezy",     re.compile(r"https?://([a-z0-9_-]+)\.breezy\.hr", re.I)),
]


# Best-effort provider classification for everything else (no portfolio pull).
def _classify_other(url: str) -> tuple[str, str]:
    host = (urlparse(url).hostname or "").lower()
    if host.endswith("breezy.hr"):
        return "breezy", host.split(".")[0]
    if host.endswith("applytojob.com"):
        return "jazzhr", host.split(".")[0]
    if host.endswith("teamtailor.com"):
        return "teamtailor", host.split(".")[0].split(".")[0]
    if "icims.com" in host:
        # careers-<company>.icims.com → "<company>"
        sub = host.split(".")[0]
        return "icims", sub.replace("careers-", "")
    if "paylocity.com" in host:
        return "paylocity", "unknown"
    if "rippling.com" in host:
        m = re.search(r"rippling\.com/([a-z0-9_-]+)", url, re.I)
        return "rippling", m.group(1) if m else "unknown"
    if "jobvite.com" in host:
        m = re.search(r"jobvite\.com/([a-z0-9_-]+)/job", url, re.I)
        return "jobvite", m.group(1) if m else "unknown"
    if "workforcenow.adp.com" in host:
        return "adp", "unknown"
    if "saashr.com" in host or "ukg.com" in host:
        return "ukg", "unknown"
    if "taleo.net" in host:
        m = re.search(r"\?org=([A-Z0-9_-]+)", url, re.I)
        return "taleo", m.group(1).lower() if m else "unknown"
    if "isolvedhire.com" in host:
        return "isolved", host.split(".")[0]
    if "entertimeonline.com" in host:
        return "entertime", "unknown"
    if "jobs.workable.com" in host:
        # /view/<id>/<slug>-at-<company>
        m = re.search(r"-at-([a-z0-9-]+)$", urlparse(url).path, re.I)
        return "workable_view", m.group(1) if m else "unknown"
    return "other", host.replace("www.", "")


def detect(url: str) -> tuple[str, str, bool]:
    """Return (provider, slug, supported)."""
    for provider, pat in SUPPORTED_PATTERNS:
        m = pat.search(url)
        if m:
            return provider, m.group(1).lower(), True
    provider, slug = _classify_other(url)
    return provider, slug, False


def slug_to_display(slug: str) -> str:
    """Best-effort human name from a URL slug."""
    return " ".join(w.capitalize() for w in re.split(r"[-_]+", slug)) or slug


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=str(DEFAULT_FILE))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    path = Path(args.file)
    if not path.exists():
        print(f"file not found: {path}", flush=True)
        return 1

    urls: list[str] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line.startswith("http"):
            urls.append(line)
    print(f"read {len(urls)} URLs from {path.name}")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    db.apply_migrations(conn)

    by_company: dict[tuple[str, str, bool], list[str]] = defaultdict(list)
    for url in urls:
        by_company[detect(url)].append(url)

    new_companies = skipped_urls = 0
    print()
    print("supported (full portfolio queued for next scrape_ats run):")
    for (provider, slug, supported), urls_list in sorted(by_company.items()):
        if not supported:
            continue
        display = slug_to_display(slug)
        ats_url = urls_list[0]
        if not args.dry_run:
            conn.execute(
                """
                INSERT INTO companies
                  (name, source, first_seen, ats_provider, ats_url, ats_slug)
                VALUES (?, ?, datetime('now'), ?, ?, ?)
                ON CONFLICT(name, source) DO UPDATE SET
                  ats_provider = COALESCE(companies.ats_provider, excluded.ats_provider),
                  ats_url      = COALESCE(companies.ats_url,      excluded.ats_url),
                  ats_slug     = COALESCE(companies.ats_slug,     excluded.ats_slug)
                """,
                (display, provider, provider, ats_url, slug),
            )
        new_companies += 1
        print(f"  [{provider:<10}] {slug:<30}  → companies.{provider}/{slug}")

    unsupported = [(p, s, urls) for (p, s, supported), urls in by_company.items() if not supported]
    if unsupported:
        print()
        print("skipped (ATS not supported — add a fetcher in scrape_ats.py to ingest these):")
        for provider, slug, urls_list in sorted(unsupported):
            skipped_urls += len(urls_list)
            print(f"  [{provider:<10}] {slug:<30}  ({len(urls_list)} url(s))")

    if not args.dry_run:
        conn.commit()
    conn.close()
    print()
    print(f"summary: {new_companies} companies queued for next scrape_ats.py run,")
    print(f"         {skipped_urls} URLs skipped (unsupported ATS).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
