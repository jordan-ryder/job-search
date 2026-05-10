#!/usr/bin/env python3
"""
Pre-Haiku filters that mark obviously-irrelevant jobs without spending an LLM
call. score_jobs.py imports `prefilter_job(title, raw_text)`; rows that match
get score=0, interested=0, with `notes` indicating which rule fired.

Each rule is a (regex, reason) pair. Rules are intentionally narrow:
  - false negatives (missing some junk) are cheap — Haiku catches them.
  - false positives (skipping a real role) are expensive — the row stays
    score=0 forever unless you re-score manually. When in doubt, defer.

Ordering: title rules run first; if no match, description rules run on raw_text.

CLI usage:

    python prefilters.py "Senior Account Executive"
    python prefilters.py title "Sales Engineer"
    python prefilters.py raw_text "Active TS/SCI clearance required"
    python prefilters.py both "Software Engineer" "Top Secret clearance required"
    python prefilters.py company "Anduril Industries"
    python prefilters.py audit                  # show counts against jobs.db

Returns the matched reason on stdout, or 'no match'.
"""

from __future__ import annotations

import re
import sys

# ---------- Title rules ----------
# Applied to jobs.title only. Each entry is a compiled pattern + short bucket name.

TITLE_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(
        r"\b(account (?:exec|executive|manager|director)|sales (?:rep|representative|development|engineer|director|manager|specialist|trader)"
        r"|sdr|bdr|enterprise sales|inside sales|outbound sales|client partner|territory account|strategic accounts?)\b",
        re.I,
    ), "sales"),
    (re.compile(
        r"\b(recruit(?:er|ing)|talent acquisition|sourcer|people (?:ops|operations|business partner|partner)"
        r"|human resources?|hr (?:business partner|generalist|manager)"
        r"|compensation (?:partner|analyst|manager|specialist)|benefits (?:manager|specialist)|total rewards)\b",
        re.I,
    ), "recruiting/HR"),
    (re.compile(
        r"\b(market(?:er|ing)|brand (?:designer|manager|strategist)|seo|content writer|copywriter"
        r"|community manager|growth marketer|paid (?:media|search|social)|comms?(?: manager)?|public relations"
        r"|communications (?:manager|specialist|director|lead|associate)"
        r"|tradeshow|trade show|event marketing|event (?:manager|coordinator|planner))\b",
        re.I,
    ), "marketing/comms"),
    (re.compile(
        r"\b(customer (?:success|support)|support representative|technical support specialist"
        r"|customer engagement manager)\b",
        re.I,
    ), "customer-facing"),
    (re.compile(
        r"\b(accounts? payable|accounts? receivable|bookkeep(?:er|ing)|controller|fp&?a|treasury"
        r"|tax (?:manager|analyst|associate)|payroll|auditor|fund accountant|finance manager)\b",
        re.I,
    ), "finance/accounting"),
    (re.compile(
        r"\b(general counsel|associate counsel|senior counsel|legal counsel|legal operations|attorney|paralegal"
        r"|contracts? (?:manager|administrator|specialist|negotiator|lead))\b",
        re.I,
    ), "legal/contracts"),
    (re.compile(r"\b(nurse|physician|therapist|clinician|medical assistant|pharmacist)\b", re.I), "medical"),
    (re.compile(r"\b(reporter|journalist)\b|\beditor(?:[,\s]|$)", re.I), "editorial"),
    (re.compile(
        r"\b(warehouse|shipping|paperhandler|forklift|logistics coordinator|truck driver"
        r"|production operator|machinist|fabricator|welder|electrician"
        r"|fulfillment (?:associate|specialist|manager|coordinator)|material planner"
        r"|inventory (?:control|manager|analyst|coordinator))\b",
        re.I,
    ), "operations/trades"),
    (re.compile(r"\b(intern|internship)\b", re.I), "internship"),
    (re.compile(r"\b(executive assistant|administrative assistant|office manager|receptionist)\b", re.I), "admin"),
    (re.compile(
        r"\b((?:product|brand|graphic|ui|ux|visual) designer|senior designer|staff designer|design (?:lead|director))\b",
        re.I,
    ), "design"),
    (re.compile(r"\bproduct (manager|director|owner)\b", re.I), "product mgmt"),
    (re.compile(r"\b(technical|tech) writer\b|\bdocumentation lead\b", re.I), "tech writing"),
    (re.compile(r"\bbuyer\b|\bprocurement\b|\bpurchasing (?:manager|specialist|agent)\b", re.I), "procurement"),
    (re.compile(r"\bfacilities (manager|coordinator|specialist|engineer)\b|\bfacility manager\b", re.I), "facilities"),
    (re.compile(r"\bconstruction (?:project manager|manager|coordinator|superintendent)\b|\bproject superintendent\b", re.I), "construction"),
    (re.compile(r"\b(quality|building|code|safety|home) inspector\b", re.I), "inspector"),
    (re.compile(r"\b(real estate|realtor|leasing (?:agent|consultant))\b", re.I), "real estate"),
    (re.compile(r"\b((?:senior |sr\.? )?trader|(?:equity|sales|fx|commodity|forex) trader)\b", re.I), "trading"),
    (re.compile(r"\b((?:test |airline |commercial )?pilot|flight (?:test )?operator|flight attendant)\b", re.I), "aviation"),
]

# Title escape hatches — defer to Haiku when the title looks like an HN
# multi-role posting. These titles often contain a non-tech keyword
# alongside a real engineering role; we don't want to drop the post.
_HN_MULTIROLE_RE = re.compile(r"^(multiple|hiring|openings?|positions?)\b", re.I)
_TECH_KW_RE = re.compile(r"\b(engineer|developer|software|founding)\b", re.I)


# ---------- Description rules ----------
# Applied to jobs.raw_text only. Even narrower than title rules — description
# text is large and noisy, so anchor on hard signals only.

DESC_RULES: list[tuple[re.Pattern, str]] = [
    # Active US security clearance ⇒ defense/intelligence; out of scope per
    # context.md. Must be a clearance phrase, not a stray "secret" mention.
    (re.compile(
        r"\b(?:TS/SCI|TS\\SCI|TS-SCI|top\s+secret|secret\s+clearance|sci\s+clearance"
        r"|active\s+clearance|active\s+security\s+clearance)\b",
        re.I,
    ), "defense (clearance)"),
    # ITAR / EAR99 / export-controlled — defense supply chain.
    (re.compile(r"\b(ITAR|EAR99|export[- ]controlled)\b", re.I), "defense (export-controlled)"),
    # Polygraph requirement — intelligence community.
    (re.compile(r"\bpolygraph\b", re.I), "defense (polygraph)"),
]


# ---------- Company-level matching ----------

# Used to normalize jobs.company strings before looking them up in the
# `companies.skip_reason` blocklist. Mirrors populate_companies.normalize_name.
_NAME_SUFFIX_RE = re.compile(
    r"[\s,]+(?:inc\.?|llc|ltd\.?|gmbh|co\.?|corp\.?|corporation|company|technologies|labs)\.?$",
    re.I,
)


def normalize_company(name: str | None) -> str:
    if not name:
        return ""
    s = name.strip().lower()
    s = _NAME_SUFFIX_RE.sub("", s).strip()
    return re.sub(r"\s+", " ", s)


def load_company_blocklist(conn) -> dict[str, str]:
    """Build {normalized_company_name: reason} from companies.skip_reason."""
    out: dict[str, str] = {}
    for name, reason in conn.execute(
        "SELECT name, skip_reason FROM companies WHERE skip_reason IS NOT NULL"
    ):
        norm = normalize_company(name)
        if norm:
            out[norm] = reason
    return out


def company_match(company: str | None, blocklist: dict[str, str] | None) -> str | None:
    if not company or not blocklist:
        return None
    return blocklist.get(normalize_company(company))


# ---------- API ----------

def title_match(title: str | None) -> str | None:
    """Match a job title. Returns reason or None."""
    if not title:
        return None
    if _HN_MULTIROLE_RE.match(title.strip()):
        return None
    if title.count(",") >= 2 and _TECH_KW_RE.search(title):
        return None
    for pat, reason in TITLE_RULES:
        if pat.search(title):
            return reason
    return None


def desc_match(raw_text: str | None) -> str | None:
    """Match a description. Returns reason or None."""
    if not raw_text:
        return None
    for pat, reason in DESC_RULES:
        if pat.search(raw_text):
            return reason
    return None


def prefilter_job(
    title: str | None,
    raw_text: str | None,
    *,
    company: str | None = None,
    blocklist: dict[str, str] | None = None,
) -> str | None:
    """Combined check: company blocklist → title rules → description rules.
    Returns the matched reason or None. Pass `company` + `blocklist` to use
    the company-level skip flag from `companies.skip_reason`."""
    if blocklist and company:
        reason = company_match(company, blocklist)
        if reason:
            return f"company: {reason}"
    return title_match(title) or desc_match(raw_text)


# ---------- CLI ----------

def _audit() -> int:
    """Run all rules against jobs.db and report counts. Useful when tuning."""
    import sqlite3
    from collections import Counter
    from pathlib import Path

    db_path = Path(__file__).parent / "jobs.db"
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    rows = cur.execute(
        "SELECT company, title, raw_text FROM jobs WHERE reviewed = 0"
    ).fetchall()
    total = len(rows)

    blocklist = load_company_blocklist(conn)

    company_hits: Counter = Counter()
    title_hits: Counter = Counter()
    desc_hits: Counter = Counter()
    company_only = title_only = desc_only = 0
    for company, t, rt in rows:
        cr = company_match(company, blocklist)
        if cr:
            company_hits[cr] += 1
            company_only += 1
            continue
        tr = title_match(t)
        if tr:
            title_hits[tr] += 1
            title_only += 1
            continue
        dr = desc_match(rt)
        if dr:
            desc_hits[dr] += 1
            desc_only += 1

    combined = company_only + title_only + desc_only
    print(f"unscored: {total}")
    print(f"  company blocklist caught: {company_only:>5} ({company_only / total:.1%})")
    print(f"  +title rules added:       {title_only:>5} ({title_only / total:.1%})")
    print(f"  +description rules added: {desc_only:>5} ({desc_only / total:.1%})")
    print(f"  combined:                 {combined:>5} ({combined / total:.1%})")
    if company_hits:
        print()
        print("company-blocked breakdown:")
        for reason, n in company_hits.most_common():
            print(f"  {reason:<22} {n:>5}")
    print()
    print("title breakdown:")
    for reason, n in title_hits.most_common():
        print(f"  {reason:<22} {n:>5}")
    if desc_hits:
        print()
        print("description breakdown:")
        for reason, n in desc_hits.most_common():
            print(f"  {reason:<22} {n:>5}")
    conn.close()
    return 0


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print("usage: python prefilters.py [audit|title|raw_text|both] <text> [<text>]", file=sys.stderr)
        return 1

    if args[0] == "audit":
        return _audit()

    if args[0] in ("title", "raw_text", "both", "company"):
        kind, rest = args[0], args[1:]
    else:
        kind, rest = "title", args  # default: treat single arg as a title

    if kind == "title":
        print(title_match(" ".join(rest)) or "no match")
    elif kind == "raw_text":
        print(desc_match(" ".join(rest)) or "no match")
    elif kind == "company":
        import sqlite3
        from pathlib import Path
        conn = sqlite3.connect(Path(__file__).parent / "jobs.db")
        bl = load_company_blocklist(conn)
        conn.close()
        print(company_match(" ".join(rest), bl) or "no match")
    else:  # both
        if len(rest) < 2:
            print("usage: python prefilters.py both <title> <raw_text>", file=sys.stderr)
            return 1
        print(prefilter_job(rest[0], rest[1]) or "no match")
    return 0


if __name__ == "__main__":
    sys.exit(main())
