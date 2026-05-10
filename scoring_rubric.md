# Job Scoring Rubric

For Claude Code to apply against rows in `jobs.db`. Read `job_search_context.md` first for full context. This file is the mechanical filter.

## How to apply

For each unreviewed job in the `jobs` table:

1. Check **hard disqualifiers**. If any hit, set `score = 0`, `reviewed = 1`, `interested = 0`, write the disqualifier in `notes`. Move on.
2. Otherwise start at **baseline 5**, then add and subtract per the factors below.
3. Clamp final score to integer range 1-10.
4. Set `reviewed = 1` for everything you score.
5. Set `interested = 1` if score >= 7. Set `interested = 0` otherwise.
6. Write the score and 1-2 sentence reasoning into `notes`. Format: `Score: 8. Modern stack (dbt+Snowflake), data team reports to CDO, remote, comp not stated. Worth a closer look.`

Lean toward scoring rather than disqualifying when uncertain. The 5-7 band is where Jordan does the second pass himself, so don't filter too aggressively.

## Hard disqualifiers (score = 0)

- **Not data engineering at all.** Pure data analyst, pure BI report writer, ML researcher with no engineering, frontend, backend-only with no data pipeline work, DevOps with no data scope.
- **Junior or entry-level.** Title contains "Junior", "Associate", "Intern", or JD lists 0-3 years required.
- **Onsite required and not in Wisconsin.** If the JD says onsite/in-office and the city isn't Appleton, Marshfield, Green Bay, Milwaukee, or Madison, skip. Hybrid with one of those cities as the hub is fine.
- **Comp clearly below band.** If a salary is stated and the max is below $120K, skip. (No salary stated is not a disqualifier.)
- **Staffing / consulting firm posting.** Apex Systems, Robert Half, TEKsystems, Insight Global, etc., posting on behalf of an undisclosed client. Exception: if the description explicitly names the end client and it looks legitimate.
- **Contract or contract-to-hire** unless the description specifically calls out conversion or it's a recognized name doing 6+ month contracts at high comp.
- **Stack is purely legacy with no modernization signal.** SSIS-only, SSRS-only, mainframe, COBOL, Informatica with no other tools mentioned.

## Positive factors (add to score)

### Strong positives (+2 each)

- **Remote** (US or US-friendly distributed). Single biggest unlock per the geography note.
- **Data team reports to business, not IT.** Look for CDO, Chief Data Officer, "Data org", "Analytics org", VP of Data. Bonus if data is its own function alongside Engineering and Product.
- **Modern stack signals.** Two or more of: dbt, Snowflake, Databricks, Redshift, Spark, Iceberg, Delta Lake, python, postgresql, datalake.
- **Senior IC title.** "Senior", "Staff", "Principal", "Lead Data Engineer". Indicates the level matches Jordan's experience and won't price-anchor low.
- **Owning the product. High level of impact on decisions. Wide scope of work. Being able to solve problems**
### Medium positives (+1 each)

- **Comp visible and at or above $140K base.** Or equity-heavy with a credible base.
- **Mid-size company (roughly 50-500 engineers).** Big enough for real systems, small enough for ownership and visibility. Series B through D startups, or independent mid-market. Estimate from team size or company headcount.
- **Engineering blog, talks, or open-source presence.** Sign of a culture that thinks in public.
- **Production ML or ML platform work mentioned.** Jordan has done this and it's a differentiator.
- **Direct stakeholder language.** "Partner with product / sales / ops / finance teams", "embedded in business unit", "work directly with customers". Not "support internal IT requests".
- **Positive mission**
- **Stated career path / tech lead track.** "Path to staff", "tech lead opportunities", "growth into management if interested".

### Mild positives (+0.5 each)

- Async-first or written-culture signals (Notion, Linear, well-documented onboarding).
- Stated values around outcomes over hours. "We measure impact, not time."
- Small data team (fewer than 8). More room to define scope.
- Healthcare, fintech, climate, education if not red-flagged elsewhere. Solid mid-stage industries with real data problems.

## Negative factors (subtract from score)

### Strong negatives (-2 each)

- **Time-tracking, butts-in-seats, or surveillance signals.** "Daily standup attendance required", "core hours 9-5 in office", "track time in [tool]".
- **Defense, weapons, surveillance contractors, gambling, MLM, crypto-only.** (Adjust if Jordan flags any of these as fine.)

### Medium negatives (-1 each)

- **Buried under IT.** Title is "BI Developer", "ETL Developer", "Database Developer", or JD describes the team as part of IT/Infrastructure with no business-facing scope. This is the structural pattern Jordan is leaving.
- **Hard "X years leading" requirement** stated as a must-have. Not a disqualifier per Jordan's "apply at 60-70% match" rule, but a signal the role may not stretch the right way.
- **Buzzword-heavy JD with no specifics.** "Rockstar", "ninja", "wear many hats", "fast-paced", "work hard play hard" with no actual scope description.
- **On-call rotation mentioned without compensation language.** Fine for senior IC if comped; problematic if expected as part of base.
- **Excessive required tooling list (15+ items).** Signal the team doesn't know what they actually need or expects an impossible polyglot.
- **Trucking-adjacent that's clearly Roehl's mirror image.** US Venture, Schneider, JB Hunt-tier IT/BI shops. Jordan has been here. Same structural risk.

### Mild negatives (-0.5 each)

- Vague comp ("competitive salary") with no other comp signal in the JD.
- Tiny startup (under 20 people total) without a strong product story. High variance, often no real data org yet.
- Pre-seed or seed stage with no revenue signal.

## Worked examples

**Example A:**
> "Senior Data Engineer, fully remote US. Join our 30-person data team reporting to the Chief Data Officer. Stack: dbt, Snowflake, Airflow, Python. You'll partner directly with product and finance teams to build the data platform. $160K-$190K base + equity."

Baseline 5
+ Remote (+2)
+ Data team under CDO (+2)
+ Modern stack: dbt + Snowflake + Airflow (+2)
+ Senior IC title (+2)
+ Comp visible and above $140K (+1)
+ Direct stakeholder language (+1)
= 15, clamp to **10**. interested = 1. Notes: "Remote, modern stack, CDO org, 160-190K, direct partner with product/finance. Strong A-tier candidate."

**Example B:**
> "BI Developer III, Hybrid (Milwaukee 3 days/week). Reports to IT Director. Build SSIS packages and Power BI dashboards for internal reporting. 5+ years SSIS required. $95-115K."

Hard disqualifier: comp max $115K, below $120K. Score = 0. Also would flag: BI Developer title under IT, legacy stack. Notes: "Skip. Comp below band; BI/IT structural pattern Jordan is leaving."

**Example C:**
> "Data Engineer at small fintech. Remote-friendly. Looking for someone to own our pipelines end-to-end. We use Python and Postgres. Series A, 25 people. Salary not stated."

Baseline 5
+ Remote (+2)
+ Stakeholder/ownership language (+1)
- Tiny startup, comp not stated (-0.5 mild)
- Stack is sparse (no medium positive triggered)
= 7.5, round to **8**. interested = 1. Notes: "Remote, ownership scope, small Series A. No comp signal and stack is light, but worth a conversation."

## Notes for Claude Code on edge cases

- HN comments often have messy parsing. If `company` or `title` is empty/garbled, read the full `description` field and use that. Don't disqualify just because the parsed fields are bad.
- Many HN posts list multiple roles in one comment. Score for the most senior data-relevant role mentioned.
- If the description is pure boilerplate with no real signal either way, score 5 and add notes "Insufficient signal, defer to manual review."
- Use polars for any aggregation queries against the resulting scored DB. No pandas.
