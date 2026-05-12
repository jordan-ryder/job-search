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

```
README.md, requirements.txt, .env, .gitignore   ← project metadata + secrets
bootstrap.sh, refresh.sh                        ← entry points
schema.sql                                      ← declarative DB schema
jobs.db                                         ← SQLite data store
search.ipynb                                    ← polars notebook for browsing results

db.py                                           ← schema diff/apply tool
scrape.py                                       ← HN "Who is Hiring" scraper
scrape_ats.py                                   ← Greenhouse/Lever/Ashby/Workable/Breezy scrapers
score_jobs.py                                   ← per-row Haiku scoring
size_companies.py                               ← headcount: regex → lookup → Haiku passes
populate_companies.py                           ← rolls jobs into per-company rows
prefilters.py                                   ← pre-Haiku regex/blocklist filters
import_apify.py                                 ← ingest Apify JSON exports
import_additional.py                            ← ingest one-off URLs

prompts/
  context.md                                    ← personal context for the LLM
  scoring_rubric.md                             ← scoring criteria

scrapes/
  additional_posts.txt                          ← one-off URLs to import
  *.json                                        ← Apify exports waiting to be ingested
  imported/                                     ← archived after ingestion
```

## Tuning

- Scoring criteria: `prompts/scoring_rubric.md`
- Personal context the LLM uses: `prompts/context.md`
- Pre-Haiku junk filters: `prefilters.py` (run `python prefilters.py audit` to see what's caught)
- Manually block a company from ever being scored: `UPDATE companies SET skip_reason = 'manual: <reason>' WHERE name = '...';`
