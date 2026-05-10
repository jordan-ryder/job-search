#!/usr/bin/env python3
"""
Jobs scraper → SQLite.

Sources (phase 1):
  - Hacker News "Who is Hiring" monthly threads via Algolia API (primary job feed)
  - Key Values company directory (seed list of culture-vetted companies)

Ingest is intentionally unfiltered — every HN comment in a hiring thread is
stored with the full raw text so you can filter later with `search` or by
querying jobs.db directly.

Usage:
    python scrape.py init
    python scrape.py scrape-hn [--months 3]
    python scrape.py scrape-keyvalues
    python scrape.py list [--source hn] [--limit 20]
    python scrape.py search "data engineer"
    python scrape.py stats

Requires: requests, beautifulsoup4
    pip install requests beautifulsoup4
"""

import argparse
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

import db

DB_PATH = Path(__file__).parent / "jobs.db"

HN_ALGOLIA_SEARCH = "https://hn.algolia.com/api/v1/search_by_date"
HN_ALGOLIA_ITEM = "https://hn.algolia.com/api/v1/items/{id}"
KEY_VALUES_URL = "https://www.keyvalues.com/"

BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# Tag-only helpers — ingest is not filtered by these, but each job is tagged
# with any matches so you can slice/filter later.
KEYWORD_TAGS = [
    "data engineer",
    "analytics engineer",
    "data platform",
    "data infrastructure",
    "ml engineer",
    "machine learning engineer",
    "data scientist",
    "data science",
]
REMOTE_TAGS = ["remote", "anywhere", "worldwide", "us-remote", "distributed"]


# ---------- DB ----------

def db_connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    """Apply schema.sql to bring jobs.db into the declared state."""
    conn = db_connect()
    n = db.apply_migrations(conn, verbose=True)
    conn.close()
    print(f"db at {DB_PATH}: applied {n} statements.")


def now_iso():
    return datetime.now(timezone.utc).isoformat()


# ---------- HN scraper ----------

def find_hn_hiring_threads(months=3):
    """Return the most recent 'Who is hiring?' story IDs posted by whoishiring."""
    params = {
        "tags": "story,author_whoishiring",
        "hitsPerPage": 50,
    }
    r = requests.get(HN_ALGOLIA_SEARCH, params=params, timeout=30)
    r.raise_for_status()
    hits = r.json().get("hits", [])
    threads = []
    for h in hits:
        title = (h.get("title") or "").lower()
        if "who is hiring" not in title:
            continue
        threads.append(
            {
                "id": int(h["objectID"]),
                "title": h.get("title"),
                "created_at": h.get("created_at"),
                "created_at_i": h.get("created_at_i") or 0,
            }
        )
    threads.sort(key=lambda x: x["created_at_i"], reverse=True)
    return threads[:months]


def strip_html(s):
    if not s:
        return ""
    text = re.sub(r"<[^>]+>", " ", s)
    return unescape(re.sub(r"\s+", " ", text)).strip()


def parse_hn_comment(text):
    """Best-effort parse of an HN 'Who is hiring' post. Raw text is always kept."""
    low = text.lower()
    matched = [k for k in KEYWORD_TAGS if k in low]
    remote = any(re.search(rf"\b{re.escape(t)}\b", low) for t in REMOTE_TAGS)

    # HN convention on the first line is typically:
    #   Company | Role | Location | [REMOTE/ONSITE] | tech stack...
    first_line = text.splitlines()[0] if text else ""
    first_line = first_line.strip()[:400]
    company = title = location = None
    if "|" in first_line:
        parts = [p.strip() for p in first_line.split("|") if p.strip()]
        if parts:
            company = parts[0][:120] or None
        if len(parts) >= 2:
            title = parts[1][:200] or None
        if len(parts) >= 3:
            location = parts[2][:200] or None
    else:
        # Fallback: take up to the first " - " or sentence break as a company guess.
        m = re.match(r"([A-Z][^\-\.\n]{1,80})(?:\s*[-–]|\.)", first_line)
        if m:
            company = m.group(1).strip()

    return {
        "company": company,
        "title": title,
        "location": location,
        "remote": 1 if remote else 0,
        "matched": ",".join(matched) if matched else None,
    }


def _iter_comments(node):
    """Yield only top-level comments of the story. In HN 'Who is Hiring' threads,
    replies are usually candidates/Q&A, not job posts, so we stop at depth 1."""
    for child in node.get("children") or []:
        if child.get("type") == "comment" and child.get("text"):
            yield child


def scrape_hn(months=3):
    threads = find_hn_hiring_threads(months=months)
    if not threads:
        print("no 'Who is hiring' threads found", file=sys.stderr)
        return
    print(f"found {len(threads)} recent 'Who is hiring' threads")

    conn = db_connect()
    cur = conn.cursor()
    total = 0
    for t in threads:
        print(f"  thread {t['id']}: {t['title']}")
        r = requests.get(HN_ALGOLIA_ITEM.format(id=t["id"]), timeout=120)
        r.raise_for_status()
        data = r.json()

        inserted = 0
        for c in _iter_comments(data):
            text = strip_html(c.get("text") or "")
            if not text:
                continue
            parsed = parse_hn_comment(text)
            source_id = str(c["id"])
            url = f"https://news.ycombinator.com/item?id={c['id']}"
            cur.execute(
                """
                INSERT OR IGNORE INTO jobs
                (source, source_id, company, title, location, remote, url,
                 posted_at, raw_text, matched_keywords, fetched_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "hn",
                    source_id,
                    parsed["company"],
                    parsed["title"],
                    parsed["location"],
                    parsed["remote"],
                    url,
                    c.get("created_at"),
                    text[:20000],
                    parsed["matched"],
                    now_iso(),
                ),
            )
            if cur.rowcount:
                inserted += 1

        cur.execute(
            "INSERT OR REPLACE INTO seen_threads(hn_story_id, title, fetched_at) VALUES (?,?,?)",
            (t["id"], t["title"], now_iso()),
        )
        conn.commit()
        print(f"    +{inserted} new jobs")
        total += inserted
        time.sleep(1)

    conn.close()
    print(f"done. {total} new HN jobs inserted.")


# ---------- Key Values scraper ----------

def scrape_key_values():
    """Seed the companies table with Key Values' culture-vetted directory."""
    headers = {"User-Agent": BROWSER_UA, "Accept": "text/html,application/xhtml+xml"}
    try:
        r = requests.get(KEY_VALUES_URL, headers=headers, timeout=30)
        r.raise_for_status()
    except requests.HTTPError as e:
        print(f"key values fetch failed: {e}", file=sys.stderr)
        print(
            "  (site may be blocking scrapers; try a browser-driven fetch or paste HTML manually)",
            file=sys.stderr,
        )
        return

    soup = BeautifulSoup(r.text, "html.parser")

    # Selectors are defensive: Key Values renders company cards as <a> tags
    # linking to /<slug>. Try a few patterns in order.
    candidates = []
    for selector in ("a.company", "a.card", "a.company-card", "a[href^='/']"):
        candidates = soup.select(selector)
        if candidates:
            break

    skip_slugs = {
        "", "about", "contact", "pricing", "login", "signup", "terms",
        "privacy", "press", "blog", "values", "recognized-for", "jobs",
        "careers", "companies", "hiring-developers", "remote",
    }

    companies = {}
    for a in candidates:
        href = (a.get("href") or "").strip()
        if not href.startswith("/") or href.count("/") != 1:
            continue
        slug = href.strip("/").split("?")[0].split("#")[0]
        if slug in skip_slugs or "/" in slug or len(slug) > 80:
            continue
        # Name = any meaningful text on the card; prefer an h2/h3 if present.
        name_el = a.find(["h1", "h2", "h3", "h4"]) or a
        name = name_el.get_text(" ", strip=True)
        if not name or len(name) > 120:
            name = slug.replace("-", " ").title()
        companies[slug] = {
            "name": name,
            "url": urljoin(KEY_VALUES_URL, href),
        }

    if not companies:
        print(
            "key values: no companies matched. The page DOM may have changed — "
            "inspect the HTML and tune the selector in scrape_key_values().",
            file=sys.stderr,
        )
        return

    conn = db_connect()
    cur = conn.cursor()
    inserted = 0
    now = now_iso()
    for slug, c in companies.items():
        cur.execute(
            """
            INSERT OR IGNORE INTO companies(name, source, url, tags, notes, first_seen)
            VALUES (?,?,?,?,?,?)
            """,
            (c["name"], "key_values", c["url"], None, f"slug={slug}", now),
        )
        if cur.rowcount:
            inserted += 1
    conn.commit()
    conn.close()
    print(f"key values: {inserted} new companies (of {len(companies)} scraped).")


# ---------- Query commands ----------

def cmd_list(source=None, limit=20):
    conn = db_connect()
    cur = conn.cursor()
    q = "SELECT id, source, company, title, location, remote, url, posted_at FROM jobs"
    args = []
    if source:
        q += " WHERE source = ?"
        args.append(source)
    q += " ORDER BY posted_at DESC LIMIT ?"
    args.append(limit)
    rows = cur.execute(q, args).fetchall()
    for r in rows:
        remote = "REMOTE" if r["remote"] else ""
        print(
            f"[{r['source']}] {r['company'] or '?'} | {r['title'] or '?'} | "
            f"{r['location'] or '?'} {remote}\n  {r['url']}\n  ({r['posted_at']})"
        )
    conn.close()


def cmd_search(term, limit=50):
    conn = db_connect()
    cur = conn.cursor()
    like = f"%{term}%"
    rows = cur.execute(
        """
        SELECT id, source, company, title, location, remote, url, posted_at
        FROM jobs
        WHERE raw_text LIKE ? COLLATE NOCASE
           OR company   LIKE ? COLLATE NOCASE
           OR title     LIKE ? COLLATE NOCASE
        ORDER BY posted_at DESC
        LIMIT ?
        """,
        (like, like, like, limit),
    ).fetchall()
    for r in rows:
        remote = "REMOTE" if r["remote"] else ""
        print(
            f"[{r['source']}] {r['company'] or '?'} | {r['title'] or '?'} | "
            f"{r['location'] or '?'} {remote}\n  {r['url']}"
        )
    print(f"\n{len(rows)} matches for '{term}'")
    conn.close()


def cmd_stats():
    conn = db_connect()
    cur = conn.cursor()
    print("jobs by source:")
    for row in cur.execute(
        "SELECT source, COUNT(*) AS n, SUM(remote) AS remote_n FROM jobs GROUP BY source"
    ):
        print(f"  {row['source']}: {row['n']} jobs ({row['remote_n'] or 0} remote)")
    print("companies by source:")
    for row in cur.execute(
        "SELECT source, COUNT(*) AS n FROM companies GROUP BY source"
    ):
        print(f"  {row['source']}: {row['n']} companies")
    threads = cur.execute("SELECT COUNT(*) AS n FROM seen_threads").fetchone()["n"]
    print(f"HN threads fetched: {threads}")
    conn.close()


# ---------- CLI ----------

def main():
    p = argparse.ArgumentParser(description="Jobs scraper → SQLite")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="create the sqlite schema")

    hn = sub.add_parser("scrape-hn", help="pull recent HN 'Who is Hiring' threads")
    hn.add_argument("--months", type=int, default=3, help="number of recent threads (default 3)")

    sub.add_parser("scrape-keyvalues", help="seed companies from keyvalues.com")

    ls = sub.add_parser("list", help="show recent jobs")
    ls.add_argument("--source")
    ls.add_argument("--limit", type=int, default=20)

    se = sub.add_parser("search", help="substring search across jobs.raw_text/company/title")
    se.add_argument("term")
    se.add_argument("--limit", type=int, default=50)

    sub.add_parser("stats", help="counts by source")

    args = p.parse_args()
    if args.cmd == "init":
        init_db()
    elif args.cmd == "scrape-hn":
        init_db()
        scrape_hn(months=args.months)
    elif args.cmd == "scrape-keyvalues":
        init_db()
        scrape_key_values()
    elif args.cmd == "list":
        cmd_list(source=args.source, limit=args.limit)
    elif args.cmd == "search":
        cmd_search(args.term, limit=args.limit)
    elif args.cmd == "stats":
        cmd_stats()


if __name__ == "__main__":
    main()
