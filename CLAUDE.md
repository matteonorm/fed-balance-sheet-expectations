# Fed Balance Sheet Expectations

Bybee (2025) replication for the Fed: LLM-derived belief index from news vs NY Fed survey expectations.

## Stack

Python, DuckDB (`fed_bs.duckdb`), Claude Haiku (classification), matplotlib.

## Key files

- `config.py` — paths, regimes, API config
- `schema.py` — DuckDB tables
- `collect_nyfed_survey.py` — NY Fed Excel surveys (Jul 2023+)
- `extract_pdf_surveys.py` — NY Fed PDF surveys (2011–2023), Claude Haiku extraction
- `collect_fred.py` — FRED weekly actuals (WALCL, TREAST, WSHOMCB, WRESBAL)
- `collect_gdelt.py` — GDELT DOC 2.0 articles
- `collect_gnews.py` — Google News RSS articles
- `collect_nyt.py` — NYT Article Search API (backfills to 2011)
- `classify.py` — 4-class classifier, k=5 ensemble
- `aggregate.py` — monthly F_t
- `leadlag_analysis.py` — differenced cross-correlation with HAC SEs
- `visualize.py` — figures
- `run_pipeline.py` — end-to-end runner

## Rules

- API keys in `.env` (gitignored): `ANTHROPIC_API_KEY`, `NYT_API_KEY`
- DuckDB single-writer — don't run two write scripts simultaneously
- FRED data arrives in millions; collectors divide by 1e3 for billions
- Validate classifier on ~100-item sample before full runs
- NYT collector caches raw JSON to `data/nyt_cache/`, pulls without DB lock, ingests in batch
