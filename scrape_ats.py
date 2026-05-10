#!/usr/bin/env python3
"""
Pull current open jobs from each company's ATS into jobs.db.

For every row in `companies` with `ats_provider` set, hit the matching public
ATS API and insert any postings we don't already have. Each new posting goes
into `jobs` with source = ats_provider (e.g. 'greenhouse'), so they flow
through the rest of the pipeline (scoring, sizing) like HN posts do.

Idempotent: INSERT OR IGNORE on (source, source_id), so re-runs only add
newly-opened roles.

Usage:
    python scrape_ats.py
    python scrape_ats.py --provider greenhouse
    python scrape_ats.py --slug findigs
    python scrape_ats.py --dry-run
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from html import unescape
from pathlib import Path

import requests

import db

DB_PATH = Path(__file__).parent / "jobs.db"
USER_AGENT = "job-search-scraper (personal use)"
REQUEST_TIMEOUT = 30
SLEEP_BETWEEN = 0.5  # be polite

REMOTE_TAGS = ("remote", "anywhere", "worldwide", "us-remote", "distributed")


def _strip_html(s: str | None) -> str:
    if not s:
        return ""
    return unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s))).strip()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _epoch_ms_to_iso(ms: int | None) -> str | None:
    if not ms:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def _to_utc_iso(s: str | None) -> str | None:
    """Parse any ISO 8601 timestamp (with or without offset) and return UTC ISO.
    Falls back to the original string if it can't be parsed."""
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return s
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _is_remote(text: str) -> bool:
    t = (text or "").lower()
    return any(k in t for k in REMOTE_TAGS)


# ---------- per-provider fetchers ----------
# Each returns list of dicts shaped like the jobs table needs.

def fetch_greenhouse(slug: str) -> list[dict]:
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
    r = requests.get(url, timeout=REQUEST_TIMEOUT, headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    out = []
    for j in r.json().get("jobs", []):
        loc = (j.get("location") or {}).get("name") or ""
        text = _strip_html(j.get("content") or "")
        out.append({
            "source_id": str(j["id"]),
            "title":     j.get("title"),
            "location":  loc or None,
            "remote":    _is_remote(loc + " " + text),
            "url":       j.get("absolute_url"),
            "posted_at": _to_utc_iso(j.get("updated_at") or j.get("first_published")),
            "raw_text":  text,
        })
    return out


def fetch_lever(slug: str) -> list[dict]:
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    r = requests.get(url, timeout=REQUEST_TIMEOUT, headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    out = []
    for j in r.json():
        cats = j.get("categories") or {}
        loc = cats.get("location") or ""
        text = j.get("descriptionPlain") or _strip_html(j.get("description") or "")
        for lst in (j.get("lists") or []):
            text += "\n\n" + (lst.get("text") or "") + "\n" + _strip_html(lst.get("content") or "")
        commitment = cats.get("commitment") or ""
        out.append({
            "source_id": str(j["id"]),
            "title":     j.get("text"),
            "location":  loc or None,
            "remote":    _is_remote(loc + " " + commitment + " " + text),
            "url":       j.get("hostedUrl"),
            "posted_at": _epoch_ms_to_iso(j.get("createdAt")),
            "raw_text":  text,
        })
    return out


def fetch_ashby(slug: str) -> list[dict]:
    url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
    r = requests.get(url, timeout=REQUEST_TIMEOUT, headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    out = []
    for j in r.json().get("jobs", []):
        loc = j.get("locationName") or ""
        text = _strip_html(j.get("descriptionHtml") or j.get("descriptionPlain") or "")
        is_remote = bool(j.get("isRemote")) or _is_remote(loc + " " + text)
        out.append({
            "source_id": str(j.get("id") or j.get("jobId")),
            "title":     j.get("title"),
            "location":  loc or None,
            "remote":    is_remote,
            "url":       j.get("jobUrl") or j.get("applyUrl"),
            "posted_at": _to_utc_iso(j.get("publishedAt") or j.get("publishedDate")),
            "raw_text":  text,
        })
    return out


def fetch_breezy(slug: str) -> list[dict]:
    # Public job board JSON feed at <slug>.breezy.hr/json. No auth needed.
    url = f"https://{slug}.breezy.hr/json"
    r = requests.get(url, timeout=REQUEST_TIMEOUT, headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    out = []
    for j in r.json():
        loc_obj = j.get("location") or {}
        loc = loc_obj.get("name") or ""
        text = _strip_html(j.get("description") or "")
        is_remote = bool(loc_obj.get("is_remote")) or _is_remote(loc + " " + text)
        out.append({
            "source_id": str(j.get("id") or j.get("friendly_id")),
            "title":     j.get("name"),
            "location":  loc or None,
            "remote":    is_remote,
            "url":       j.get("url"),
            "posted_at": _to_utc_iso(j.get("published_date") or j.get("updated_date")),
            "raw_text":  text,
        })
    return out


def fetch_workable(slug: str) -> list[dict]:
    # Public widget endpoint — stable across accounts.
    url = f"https://apply.workable.com/api/v1/widget/accounts/{slug}"
    r = requests.get(url, timeout=REQUEST_TIMEOUT, headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    out = []
    for j in r.json().get("jobs", []):
        loc = j.get("location") or ""
        text = _strip_html(j.get("description") or j.get("snippet") or "")
        out.append({
            "source_id": j.get("shortcode") or str(j.get("id")),
            "title":     j.get("title"),
            "location":  loc or None,
            "remote":    _is_remote((loc or "") + " " + text),
            "url":       j.get("url") or j.get("application_url"),
            "posted_at": _to_utc_iso(j.get("published")),
            "raw_text":  text,
        })
    return out


FETCHERS = {
    "greenhouse": fetch_greenhouse,
    "lever":      fetch_lever,
    "ashby":      fetch_ashby,
    "workable":   fetch_workable,
    "breezy":     fetch_breezy,
}


def insert_jobs(conn: sqlite3.Connection, source: str, company: str, postings: list[dict]) -> int:
    cur = conn.cursor()
    inserted = 0
    for p in postings:
        cur.execute(
            """
            INSERT OR IGNORE INTO jobs
              (source, source_id, company, title, location, remote, url,
               posted_at, raw_text, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source, p["source_id"], company, p["title"], p["location"],
                1 if p["remote"] else 0,
                p["url"], p["posted_at"],
                (p["raw_text"] or "")[:20000] or None,
                _now_iso(),
            ),
        )
        if cur.rowcount:
            inserted += 1
    return inserted


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", choices=list(FETCHERS), default=None,
                    help="restrict to one ATS provider")
    ap.add_argument("--slug", default=None, help="restrict to one slug")
    ap.add_argument("--dry-run", action="store_true",
                    help="print counts but don't write")
    args = ap.parse_args()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    db.apply_migrations(conn)

    where = ["ats_provider IS NOT NULL", "ats_slug IS NOT NULL"]
    params: list = []
    if args.provider:
        where.append("ats_provider = ?")
        params.append(args.provider)
    if args.slug:
        where.append("ats_slug = ?")
        params.append(args.slug)

    rows = conn.execute(
        f"SELECT name, ats_provider, ats_slug FROM companies "
        f"WHERE {' AND '.join(where)} ORDER BY ats_provider, ats_slug",
        params,
    ).fetchall()

    print(f"hitting {len(rows)} ATS endpoints"
          f"{' (dry-run)' if args.dry_run else ''}")

    total_seen = total_new = errored = 0
    for r in rows:
        provider = r["ats_provider"]
        slug = r["ats_slug"]
        company = r["name"]

        try:
            postings = FETCHERS[provider](slug)
        except requests.HTTPError as e:
            code = e.response.status_code if e.response is not None else "?"
            print(f"  [{provider:<10}] {slug:<25} HTTP {code}", file=sys.stderr)
            errored += 1
            time.sleep(SLEEP_BETWEEN)
            continue
        except Exception as e:  # noqa: BLE001 — log and continue across providers
            print(f"  [{provider:<10}] {slug:<25} ERROR {e}", file=sys.stderr)
            errored += 1
            time.sleep(SLEEP_BETWEEN)
            continue

        total_seen += len(postings)
        if args.dry_run:
            print(f"  [{provider:<10}] {slug:<25} {len(postings):>3} postings")
        else:
            new = insert_jobs(conn, provider, company, postings)
            conn.commit()
            total_new += new
            print(f"  [{provider:<10}] {slug:<25} {len(postings):>3} postings  +{new} new")

        time.sleep(SLEEP_BETWEEN)

    conn.close()
    print()
    print(f"done. {total_seen} postings seen, {total_new} new inserts, {errored} errors")
    return 0


if __name__ == "__main__":
    sys.exit(main())
