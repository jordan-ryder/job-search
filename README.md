# job-search

## What is this?

Clicking through job postings can be very tedious, depending on the job board. 
I created this to pull down the raw data from various sources, and score them based on my criteria. 


## Setup

```bash
. ./bootstrap.sh     # creates .venv with uv, installs requirements
```

Then paste your Anthropic API key into `.env`:

```
ANTHROPIC_API_KEY=sk-ant-...
```

Get one at <https://console.anthropic.com/settings/keys>.

## Refresh

```bash
./refresh.sh
```

Runs the full pipeline: scrape HN → import additional URLs → import Apify exports → score → size → roll-up → scrape ATS portfolios → score → size → roll-up.

## Layout

| File | Purpose |
|---|---|
| `schema.sql` | Source of truth for the DB schema. Edit this; `db.py migrate` reconciles. |
| `db.py` | Schema diff/apply tool. `python db.py status` to preview, `migrate` to apply. |
| `scrape.py` | HN "Who is Hiring" scraper. |
| `scrape_ats.py` | Direct ATS scrapers (Greenhouse, Lever, Ashby, Workable, Breezy). |
| `score_jobs.py` | Per-row scoring via Haiku. Reads `context.md` + `scoring_rubric.md` as cached system prompt. |
| `size_companies.py` | Headcount estimator: regex pass → lookup pass → Haiku pass. |
| `populate_companies.py` | Aggregates `jobs` into per-company rows. Auto-flags defense contractors. |
| `prefilters.py` | Regex/blocklist filters that skip Haiku for obvious junk. `python prefilters.py audit` shows coverage. |
| `import_apify.py` | Imports JSON exports dropped into `scrapes/`. Moves processed files to `scrapes/imported/`. |
| `import_additional.py` | Reads `additional_posts.txt` of one-off URLs, queues their companies' ATS portfolios. |
| `search.ipynb` | Query/browse the scored DB with polars. |

## Tuning

- Scoring criteria: `scoring_rubric.md`
- Personal context the LLM uses: `context.md`
- Pre-Haiku junk filters: `prefilters.py` (run `python prefilters.py audit` to see what's caught)
- Manually block a company from ever being scored: `UPDATE companies SET skip_reason = 'manual: <reason>' WHERE name = '...';`
