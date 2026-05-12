# Job Scoring Rubric

For Claude Code to apply against rows in `jobs.db`. Read `context.md` first for full context. This file is the mechanical filter.

## How to apply

For each unreviewed job in the `jobs` table:

1. Check **hard disqualifiers**. If any hit, set `score = 0`, `reviewed = 1`, `interested = 0`, write the disqualifier in `notes`. Move on.
2. Otherwise start at **baseline 5**, then add and subtract per the factors below.
3. Clamp final score to integer range 1-10.
4. Set `reviewed = 1` for everything you score.
5. Set `interested = 1` if score >= 7. Set `interested = 0` otherwise.
6. Write the score and 1-2 sentence reasoning into `notes`. Format: `Score: 8. Modern stack (dbt+Snowflake), data team reports to CDO, remote, comp not stated. Worth a closer look.`

Lean toward scoring rather than disqualifying when uncertain. The 5-7 band is where Jordan does the second pass himself, so don't filter too aggressively. **And don't be stingy with 10s** — if a role is genuinely in Jordan's top tier (the kind he'd customize an application for the same day), give it a 10.

## Score tier definitions

Use these as the qualitative anchor — the math should land on the same tier the description implies.

- **10 — gold standard.** Combines a BIG plus (mission industry OR AI/ML/Data blend) with multiple strong positives (remote, senior IC, modern stack, stakeholder ownership). Roughly the top 2-5% of postings. Worth a same-day tailored application.
- **9 — excellent.** Strong across most dimensions with one notable gap (e.g., comp not stated, mid-stage company). Customize and apply.
- **8 — strong.** Clear fit; research the company before applying.
- **7 — worth applying.** Meets bar with some unknowns; standard application.
- **5-6 — manual review band.** Mixed signals; Jordan reads these himself.
- **3-4 — weak fit.** Specific issues outweigh positives, but not disqualifying.
- **1-2 — poor fit.** Multiple mismatches, sub-baseline.
- **0 — hard disqualifier.** See list below.

## Hard disqualifiers (score = 0)

- **Not data engineering at all.** Pure data analyst, pure BI report writer, ML researcher with no engineering, frontend-only with no data scope, DevOps with no data scope. Note: full-stack/product engineering *combined* with data work is NOT a disqualifier — see "Variety in scope" positive below.
- **Junior or entry-level.** Title contains "Junior", "Associate", "Intern", or JD lists 0-3 years required.
- **Onsite required and not in Wisconsin.** If the JD says onsite/in-office and the city isn't Appleton, Marshfield, Green Bay, Milwaukee, or Madison, skip. Hybrid with one of those cities as the hub is fine.
- **Comp clearly below band.** If a salary is stated and the max is below $120K, skip. (No salary stated is not a disqualifier.)
- **Staffing / consulting firm posting.** Apex Systems, Robert Half, TEKsystems, Insight Global, etc., posting on behalf of an undisclosed client. Exception: if the description explicitly names the end client and it looks legitimate.
- **Contract or contract-to-hire** unless the description specifically calls out conversion or it's a recognized name doing 6+ month contracts at high comp.
- **Stack is purely legacy with no modernization signal.** SSIS-only, SSRS-only, mainframe, COBOL, Informatica with no other tools mentioned.
- **Defense, weapons, or surveillance contractor.** (Industry-level disqualifier; per `context.md`.)

## Positive factors (add to score)

### BIG positives (+3 each)

These are the ones that should push a strong-otherwise role into 9-10 territory.

- **AI / ML / Data engineering blend.** Role explicitly combines ML or AI infrastructure with data engineering — e.g., "build the ML platform and data pipelines", "feature stores plus warehouse work", "ML observability and data quality", "AI model deployment alongside analytics infra". The key is that AI/ML is *complementary* to data engineering, not the entire job. A pure "AI Engineer" role with no data pipeline scope does NOT qualify (and may earn the 100%-AI-only negative below if the company is also AI-only).
- **Mission-aligned industry.** Healthcare, pharmaceuticals/biotech, non-profit, government aid, humanitarian work, public health. Companies that exist to solve a real-world problem, not because someone wanted a startup.

### Strong positives (+2 each)

- **Remote** (US or US-friendly distributed). Single biggest unlock per the geography note.
- **Senior IC title.** "Senior", "Staff", "Principal", "Lead Data Engineer". Indicates the level matches Jordan's experience and won't price-anchor low.
- **Modern stack signals.** Two or more of: dbt, Snowflake, Databricks, Redshift, Spark, Iceberg, Delta Lake, Python, Postgres, data-lake architecture.
- **Data team reports to business, not IT.** Look for CDO, Chief Data Officer, "Data org", "Analytics org", VP of Data. Bonus if data is its own function alongside Engineering and Product.
- **Owning the product.** Wide scope, real impact on direction, ability to engage with customers / end users on product decisions, not just internal IT requests.
- **Variety in scope.** Role mixes data engineering with full-stack/product/AI work. Variety is a feature for Jordan, not dilution. (Different from the AI/ML/Data BIG positive — that one requires explicit ML/AI infra. This one is about general breadth.)

### Medium positives (+1 each)

- **Comp visible and at or above $140K base.** Or equity-heavy with a credible base.
- **Mid-size company (roughly 50-500 engineers).** Big enough for real systems, small enough for ownership and visibility. Series B through D startups, or independent mid-market. Estimate from team size or company headcount.
- **Engineering blog, talks, or open-source presence.** Sign of a culture that thinks in public.
- **Production ML or ML platform work mentioned** (without the broader blend that would qualify for the BIG positive).
- **Direct stakeholder language.** "Partner with product / sales / ops / finance teams", "embedded in business unit", "work directly with customers". Not "support internal IT requests".
- **Stated career path / tech lead track.** "Path to staff", "tech lead opportunities", "growth into management if interested".
- **Education, climate, or civic-tech industry** (positive but a notch below the BIG mission-aligned list).

### Mild positives (+0.5 each)

- Async-first or written-culture signals (Notion, Linear, well-documented onboarding).
- Stated values around outcomes over hours. "We measure impact, not time." (Jordan strongly prefers being judged on output, not time.)
- Small data team (fewer than 8). More room to define scope.
- **Cultural-tone vocabulary.** JD uses words like "understanding", "empathy", "thoughtful", "kindness", "humility". Signals a team that thinks about how people work together, not just what they ship.

## Negative factors (subtract from score)

### Strong negatives (-2 each)

- **Time-tracking, butts-in-seats, or surveillance signals.** "Daily standup attendance required", "core hours 9-5 in office", "track time in [tool]".
- **Disliked industries.** Crypto-only, trading, hedge funds, MLM, gambling. (Defense/weapons is already a hard disqualifier above.)
- **100% AI-only product.** Company's entire product is an AI tool/model with no broader domain — foundation-model labs, "AI for X" startups whose only differentiator is the AI itself. Note: companies that *use* AI alongside another domain (e.g., AI in healthcare, AI in biotech, AI in government services) do NOT trigger this — those should benefit from the AI/ML/Data BIG positive instead.

### Medium negatives (-1 each)

- **Buried under IT.** Title is "BI Developer", "ETL Developer", "Database Developer", or JD describes the team as part of IT/Infrastructure with no business-facing scope. This is the structural pattern Jordan is leaving.
- **Generic financial services / banking** (not crypto/trading — those are -2). Banks, insurance carriers, ordinary FinServ shops. Not actively bad, just not exciting to Jordan.
- **AWS or GCP explicitly required.** Jordan's cloud experience is Azure. If the JD lists AWS or GCP as a hard requirement (not "nice to have", and not alongside Azure), that's a meaningful gap. Roles that say "any major cloud" or list multiple clouds with Azure included don't trigger this.
- **Volume-as-the-pitch.** JD centers the role on raw scale — "petabyte-scale", "billions of events/day", "trillions of rows", "ultra-high-throughput pipelines", heavy emphasis on micro-optimization or latency tuning as the core of the job. Jordan would rather have meaningful impact on millions of rows than squeeze tiny optimizations out of massive data. Just *mentioning* large data isn't enough — this is when scale is framed as the primary challenge or value prop.
- **Hard "X years leading" requirement** stated as a must-have. Not a disqualifier per Jordan's "apply at 60-70% match" rule, but a signal the role may not stretch the right way.
- **Buzzword-heavy JD with no specifics.** "Rockstar", "ninja", "wear many hats", "fast-paced", "work hard play hard" with no actual scope description.
- **On-call rotation mentioned without compensation language.** Fine for senior IC if comped; problematic if expected as part of base.
- **Excessive required tooling list (15+ items).** Signal the team doesn't know what they actually need or expects an impossible polyglot.
- **Trucking-adjacent that's clearly Roehl's mirror image.** US Venture, Schneider, JB Hunt-tier IT/BI shops. Jordan has been here. Same structural risk.

### Mild negatives (-0.5 each)

- Vague comp ("competitive salary") with no other comp signal in the JD.
- Tiny startup (under 20 people total) without a strong product story. High variance, often no real data org yet.
- Pre-seed or seed stage with no revenue signal.
- **Microsoft Fabric is the centerpiece.** JD emphasizes Fabric as the platform Jordan would be working in (rather than mentioning it as one of several tools). It's an Azure-native fit but it's the part of the Microsoft data stack Jordan is least excited by.

## Worked examples

**Example A — gold standard:**
> "Senior Data Engineer + ML Platform, fully remote US. We're a Series C health-tech company building an oncology data platform. You'll own pipelines from clinical-trial ingestion through ML feature stores, and partner directly with our oncology informatics team. Stack: dbt, Snowflake, Spark, MLflow, Python. $170K-$200K base + equity."

Baseline 5
+ AI/ML/Data blend (+3)
+ Mission-aligned industry: health-tech (+3)
+ Remote (+2)
+ Senior IC title (+2)
+ Modern stack: dbt + Snowflake + Spark + MLflow (+2)
+ Owning the product, customer/stakeholder partnership (+2)
+ Comp visible above $140K (+1)
= 20, clamp to **10**. interested = 1. Notes: "Gold standard: remote health-tech ML+data role with full ownership, modern stack, comp at $170-200K. Apply today."

**Example B — strong:**
> "Senior Data Engineer, fully remote US. Join our 30-person data team reporting to the Chief Data Officer. Stack: dbt, Snowflake, Airflow, Python. You'll partner directly with product and finance teams to build the data platform. $160K-$190K base + equity."

Baseline 5
+ Remote (+2)
+ Senior IC title (+2)
+ Modern stack (+2)
+ Data team under CDO (+2)
+ Stakeholder language (+1) (medium positive — stakeholder partnership but not full product ownership)
+ Comp above $140K (+1)
= 15, clamp to **10**. interested = 1. Notes: "Remote, modern stack, CDO org, $160-190K. Strong A-tier even without an explicit AI/ML or mission-industry signal."

**Example C — disqualifier:**
> "BI Developer III, Hybrid (Milwaukee 3 days/week). Reports to IT Director. Build SSIS packages and Power BI dashboards for internal reporting. 5+ years SSIS required. $95-115K."

Hard disqualifier: comp max $115K, below $120K. Score = 0. Also would flag: BI Developer title under IT, legacy stack. Notes: "Skip. Comp below band; BI/IT structural pattern Jordan is leaving."

**Example D — small Series A:**
> "Data Engineer at small fintech. Remote-friendly. Looking for someone to own our pipelines end-to-end. We use Python and Postgres. Series A, 25 people. Salary not stated."

Baseline 5
+ Remote (+2)
+ Owning the product (+2)
- Generic financial services (-1)
- Tiny startup, comp not stated (-0.5)
= 7.5, round to **8**. interested = 1. Notes: "Remote, ownership scope, Series A. FinServ industry tag and no comp signal soften it, but worth a conversation."

**Example E — 100% AI startup:**
> "Senior Data Engineer at an AI agent platform. We're building autonomous agents for general-purpose use. Series B, fully remote, $160K base + equity. Stack: Python, Postgres, vector DBs."

Baseline 5
+ Remote (+2)
+ Senior IC (+2)
+ Comp visible above $140K (+1)
- 100% AI-only product (-2)
- Stack signal is light (modern but not 2+ from the warehouse list) (no positive triggered)
= 8. interested = 1, but borderline. Notes: "Remote senior IC at AI-agent startup. Comp solid; deducted for AI-only product (Jordan prefers AI as part of a domain mix). Worth a closer look."

## Notes for Claude Code on edge cases

- HN comments often have messy parsing. If `company` or `title` is empty/garbled, read the full `description` field and use that. Don't disqualify just because the parsed fields are bad.
- Many HN posts list multiple roles in one comment. Score for the most senior data-relevant role mentioned.
- If the description is pure boilerplate with no real signal either way, score 5 and add notes "Insufficient signal, defer to manual review."
- The two BIG positives (AI/ML/Data blend, mission industry) are not mutually exclusive — a health-tech ML platform role earns both.
- Don't confuse "uses AI" with "100% AI product". A pharma company using ML for drug discovery is a positive, not a negative.
- Use polars for any aggregation queries against the resulting scored DB. No pandas.
