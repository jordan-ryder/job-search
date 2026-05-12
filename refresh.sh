#!/usr/bin/env bash
# Refresh job listings: scrape new HN posts, score them, then size companies.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

# Load .env (e.g. ANTHROPIC_API_KEY) without exporting it from the parent shell.
set -a
# shellcheck disable=SC1091
source .env
set +a

# shellcheck disable=SC1091
source .venv/bin/activate

python scrape.py scrape-hn --months 3
# Pull in any one-off URLs from scrapes/additional_posts.txt — supported ATSes
# get their full portfolio queued for the scrape_ats step below.
[[ -f scrapes/additional_posts.txt ]] && python import_additional.py
# Ingest any Apify exports dropped into ./scrapes/ (moves them to ./imported/).
[[ -d scrapes ]] && python import_apify.py
python score_jobs.py
python size_companies.py
python populate_companies.py
python scrape_ats.py
# rescore/resize anything new the ATS pull just added
python score_jobs.py
python size_companies.py
python populate_companies.py
