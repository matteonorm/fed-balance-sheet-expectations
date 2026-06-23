# Balance Sheet Expectations — Handoff

Bybee (2025) "Ghost in the Machine" replication: LLM-derived belief index ($F_t$) from news vs NY Fed professional forecaster survey expectations. Current methodology: **revision-direction test** with **horizon-class split** (near-term primary, long-run secondary). Near-term test (N=9): Spearman rho=+0.37, sign agreement 71%, underpowered. Binding constraint is news corpus density.

## Current state

- **Working tree is clean**, all pushed to `origin/main` at `682fe9a`.
- **Repo**: `https://github.com/matteonorm/fed-balance-sheet-expectations`
- **Local dir**: `/Users/matteoangelonormanno/ecb-balance-sheet-expectations` (folder name still says "ecb" but the GitHub repo was renamed to `fed-balance-sheet-expectations`).

## Stack

Python, DuckDB (`fed_bs.duckdb`), Claude Haiku (`claude-haiku-4-5`) for classification, matplotlib, scipy.

## Key files

- `config.py` — paths, regimes, API config
- `schema.py` — DuckDB tables
- `collect_nyfed_survey.py` — NY Fed Excel surveys (Jul 2023+)
- `extract_pdf_surveys.py` — NY Fed PDF surveys (2011–2023), Claude Haiku extraction, cached JSONs in `data/pdf_extractions/`
- `collect_fred.py` — FRED weekly actuals (WALCL, TREAST, WSHOMCB, WRESBAL)
- `collect_gdelt.py` — GDELT DOC 2.0 articles
- `collect_gnews.py` — Google News RSS articles
- `collect_nyt.py` — NYT Article Search API (backfills to 2011), caches raw JSON to `data/nyt_cache/`
- `classify.py` — 4-class classifier (increase/decrease/uncertain/not_relevant), k=3 ensemble majority vote
- `aggregate.py` — monthly $F_t = (n_{increase} - n_{decrease}) / (n_{increase} + n_{decrease})$
- **`revision_direction.py`** — **current primary analysis**: 7 survey blocks, horizon-class split (near-term vs long-run), three tests (near-term primary, long-run secondary, pooled), figure, verdict
- `leadlag_analysis.py` — earlier iteration: differenced cross-correlation with HAC/Newey-West SEs (retained for reference)
- `contemporaneous_analysis.py` — earlier iteration: contemporaneous co-movement, collapsed to N=11 (retained for reference)
- `visualize.py` — fig1 (belief index + regime shading). fig2 (old F_t vs survey scatter) removed from output; do NOT reuse `fig2_correlation`
- `run_pipeline.py` — end-to-end runner

## Database (`fed_bs.duckdb`)

| Table | Rows | Notes |
|-------|------|-------|
| gdelt_articles | 4,749 | All news sources (gdelt/gnews/nyt) |
| llm_classifications | 4,749 | 4-class, k=3 ensemble |
| llm_expectations | 182 | Monthly F_t |
| nyfed_survey_bs | 4,999 | Excel (3,766) + PDF (1,233) |
| fed_balance_sheet | 964 | FRED weekly actuals |
| nyfed_survey_runoff | 410 | Runoff timing/size |

## Methodology: revision-direction with horizon-class split

The survey object is the **sign** of round-to-round revision in median (pctl50) expectations at a fixed horizon within each of 7 contiguous survey blocks (2011–2026, 46 pairs total). Signs are comparable across blocks even when units differ.

**Horizon-class split** (threshold: 1.5 years from last survey round):
- **Near-term** (A, D, E, F, G): 37 pairs, 10 survive F_t join (73% attrition). Primary test.
- **Long-run** (B, C): 9 pairs, 5 survive. All 5 are +1 (upward). Descriptive only.

Key design choices:
- Horizon selection: maximise `n_pairs × nonzero_fraction`, tiebreak toward nearer horizons
- F_t inner join requires `n_relevant >= 3` in both current and prior survey months
- Quarter-clustered SEs (CR1) because blocks C/D/E overlap in calendar time 2018–2020
- Blocks A and D are entirely lost to news sparsity

## Design principles (from prior ECB project)

1. Relevance gate (4-class classification, not binary)
2. Confidence = ensemble agreement (k=3 majority vote)
3. Few-shot anchoring in classifier prompt
4. Survey object is SIGN of revision (comparable across variable families)
5. Horizon-class split: near-term and long-run are separate belief objects
6. Report nulls as nulls; do not interpolate F_t

## Known issues and quirks

- **DuckDB single-writer constraint**: never run two write scripts simultaneously. GDELT collector holds the lock for entire run.
- **F_t is noisy** when n_relevant is small (many months have F_t = ±1 from just 2–3 articles).
- **PDF extraction**: all 86 PDFs cached in `data/pdf_extractions/*.json`. Re-extraction requires `ANTHROPIC_API_KEY`. Ingestion can be re-run from cache without the key (call `ingest_pdf_extractions()` directly).
- **NYT API**: 5 req/min rate limit (12s between calls). Key stored in `.env`. The `fq` source filter doesn't work (returns 0 results) — queries run unfiltered. The `hits` field is unreliable.
- **Classification**: 88.8% of articles classified as not_relevant. Full run takes ~1 hour with Haiku.
- **`.gitignore`**: `output/` is excluded; use `git add -f` for output files.
- **config.py says ENSEMBLE_K=5** but actual classifications in the DB were run with k=3. The DB is authoritative.

## Rules

- API keys in `.env` (gitignored): `ANTHROPIC_API_KEY`, `NYT_API_KEY`
- FRED data arrives in millions; collectors divide by 1e3 for billions
- Validate classifier on ~100-item sample before full runs (`python classify.py validate`)
- Do NOT reuse `visualize.py`'s `fig2_correlation` (spliced + regime artifact)
- Do NOT overwrite `leadlag_analysis.py` or `contemporaneous_analysis.py`
- Do NOT reuse the old contemporaneous rho=0.51 or any earlier headline
- Do NOT headline results; do NOT frame as success/failure
- Do NOT overclaim at this N
- User preference: descriptive and neutral framing; minimal charts, no unnecessary files
- Next phase: full-text news corpus (Factiva/DNA) to recover ~27 near-term observations. Necessary but not sufficient.

## Suggested skills

- `/commit` — for staging and committing changes
- `/pr` — if creating a pull request
- `/code-review` — for reviewing changes before committing
