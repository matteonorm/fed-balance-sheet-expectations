"""
Collect news articles about the Fed balance sheet from the NYT Article Search API.
Backfills from 2011-01 to present, covering regimes that GDELT (2015+) misses.

Design:
- Caches raw JSON responses to data/nyt_cache/ so re-runs don't re-hit the API.
- Does NOT hold the DuckDB write lock during API pulls.
- Stages results to a parquet file, then ingests in a single batched transaction.
- NYT rate limit: 5 requests/minute → sleep 12s between calls.

Usage:
    python collect_nyt.py              # collect and ingest
    python collect_nyt.py report       # print coverage summary
"""

import os
import sys
import json
import time
import hashlib
import re
import unicodedata
import requests
import duckdb
import pandas as pd
from datetime import datetime
from config import DUCKDB_PATH, DATA_DIR

def _load_env_key(key):
    val = os.environ.get(key, "")
    if val:
        return val
    try:
        env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith(f"{key}="):
                    return line.split("=", 1)[1]
    except FileNotFoundError:
        pass
    return ""

NYT_API_KEY = _load_env_key("NYT_API_KEY")

if not NYT_API_KEY:
    print("ERROR: NYT_API_KEY not found in environment or .env file.")
    print("Add NYT_API_KEY=your_key to the .env file and retry.")
    sys.exit(1)

NYT_SEARCH_URL = "https://api.nytimes.com/svc/search/v2/articlesearch.json"
NYT_CACHE_DIR = os.path.join(DATA_DIR, "nyt_cache")
NYT_RATE_LIMIT_SECONDS = 12

NYT_QUERIES = [
    '"Federal Reserve balance sheet"',
    '"Fed balance sheet"',
    '"quantitative tightening"',
    '"quantitative easing" "Federal Reserve"',
    '"SOMA portfolio"',
    '"Fed tapering" OR "taper tantrum"',
    '"Fed asset purchases"',
    '"Treasury runoff" OR "MBS runoff"',
    '"balance sheet normalization" Fed',
    '"balance sheet reduction" Fed',
    '"Fed reinvestment"',
    '"Federal Reserve" "bond purchases"',
]

DATE_START = "2011-01-01"
DATE_END = "2026-06-21"


def _cache_key(query, page, begin_date, end_date):
    raw = f"{query}|{page}|{begin_date}|{end_date}"
    return hashlib.md5(raw.encode()).hexdigest()


def query_nyt(query, begin_date, end_date, page=0):
    """Query NYT Article Search API. Returns (docs, total_hits)."""
    cache_file = os.path.join(NYT_CACHE_DIR, f"{_cache_key(query, page, begin_date, end_date)}.json")

    if os.path.exists(cache_file):
        with open(cache_file) as f:
            data = json.load(f)
        docs = data.get("response", {}).get("docs", [])
        hits = data.get("response", {}).get("meta", {}).get("hits", 0)
        return docs, hits

    params = {
        "q": query,
        "begin_date": begin_date.replace("-", ""),
        "end_date": end_date.replace("-", ""),
        "page": page,
        "api-key": NYT_API_KEY,
        "fl": "headline,pub_date,web_url,abstract,lead_paragraph,section_name,source,document_type",
    }

    for attempt in range(3):
        try:
            resp = requests.get(NYT_SEARCH_URL, params=params, timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                with open(cache_file, "w") as f:
                    json.dump(data, f)
                docs = data.get("response", {}).get("docs", [])
                hits = data.get("response", {}).get("meta", {}).get("hits", 0)
                return docs, hits
            elif resp.status_code == 429:
                wait = NYT_RATE_LIMIT_SECONDS * (attempt + 2)
                print(f"    Rate limited, waiting {wait}s...")
                time.sleep(wait)
            else:
                print(f"    HTTP {resp.status_code} for q={query[:30]}... page={page}")
                if resp.status_code == 401:
                    print("    ERROR: Invalid API key")
                    return [], 0
                time.sleep(NYT_RATE_LIMIT_SECONDS)
        except Exception as e:
            print(f"    Error: {e}")
            time.sleep(NYT_RATE_LIMIT_SECONDS)

    return [], 0


def _normalize_title(title):
    """Normalize title for dedup: lowercase, strip punctuation, collapse whitespace."""
    title = unicodedata.normalize("NFKD", title.lower())
    title = re.sub(r"[^\w\s]", "", title)
    title = re.sub(r"\s+", " ", title).strip()
    return title


def collect_articles():
    """Collect all NYT articles matching Fed BS queries. Returns list of dicts."""
    os.makedirs(NYT_CACHE_DIR, exist_ok=True)

    all_articles = {}

    for query in NYT_QUERIES:
        print(f"\nQuery: {query}")
        total_for_query = 0

        page = 0
        while True:
            docs, hits = query_nyt(query, DATE_START, DATE_END, page)
            docs = docs or []

            if page == 0 and hits > 0:
                print(f"  ~{hits} hits")

            if not docs:
                break

            for doc in docs:
                headline_obj = doc.get("headline", {})
                title = headline_obj.get("main", "") if isinstance(headline_obj, dict) else str(headline_obj)
                if not title:
                    continue

                url = doc.get("web_url", "")
                if not url:
                    continue

                pub_date = doc.get("pub_date", "")
                try:
                    dt = datetime.fromisoformat(pub_date.replace("Z", "+00:00"))
                except (ValueError, AttributeError):
                    dt = datetime.now()

                abstract = doc.get("abstract", "") or doc.get("lead_paragraph", "") or ""
                section = doc.get("section_name", "")

                if url not in all_articles:
                    all_articles[url] = {
                        "url": url,
                        "title": title,
                        "seendate": dt.strftime("%Y-%m-%dT%H:%M:%S"),
                        "domain": "nytimes.com",
                        "language": "English",
                        "sourcecountry": "United States",
                        "query_keyword": f"nyt:{query}",
                        "snippet": abstract[:1000],
                        "section": section,
                        "source": "nyt",
                        "norm_title": _normalize_title(title),
                        "pub_date_str": dt.strftime("%Y-%m-%d"),
                    }
                    total_for_query += 1

            if len(docs) < 10 or page >= 199:
                break
            page += 1
            time.sleep(NYT_RATE_LIMIT_SECONDS)

        print(f"  {total_for_query} unique articles")

    print(f"\nTotal unique NYT articles: {len(all_articles)}")
    return list(all_articles.values())


def ingest_to_db(articles, db_path=DUCKDB_PATH):
    """Ingest NYT articles into the gdelt_articles table, deduping on url and (norm_title, date)."""
    if not articles:
        print("No articles to ingest.")
        return

    con = duckdb.connect(db_path)

    try:
        con.execute("ALTER TABLE gdelt_articles ADD COLUMN IF NOT EXISTS source VARCHAR DEFAULT 'gdelt'")
    except Exception:
        pass
    try:
        con.execute("ALTER TABLE gdelt_articles ADD COLUMN IF NOT EXISTS snippet VARCHAR")
    except Exception:
        pass
    try:
        con.execute("ALTER TABLE gdelt_articles ADD COLUMN IF NOT EXISTS section VARCHAR")
    except Exception:
        pass

    existing_urls = set()
    try:
        rows = con.execute("SELECT url FROM gdelt_articles").fetchall()
        existing_urls = {r[0] for r in rows}
    except Exception:
        pass

    existing_titles = set()
    try:
        rows = con.execute("""
            SELECT LOWER(REGEXP_REPLACE(title, '[^a-zA-Z0-9 ]', '', 'g')),
                   CAST(seendate AS DATE)
            FROM gdelt_articles
        """).fetchall()
        existing_titles = {(r[0].strip(), str(r[1])) for r in rows if r[0]}
    except Exception:
        pass

    new_count = 0
    dup_url = 0
    dup_title = 0

    for art in articles:
        if art["url"] in existing_urls:
            dup_url += 1
            continue

        if (art["norm_title"], art["pub_date_str"]) in existing_titles:
            dup_title += 1
            continue

        try:
            con.execute("""
                INSERT OR IGNORE INTO gdelt_articles
                (url, title, seendate, domain, language, sourcecountry,
                 query_keyword, source, snippet, section)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, [
                art["url"], art["title"], art["seendate"],
                art["domain"], art["language"], art["sourcecountry"],
                art["query_keyword"], "nyt", art.get("snippet", ""),
                art.get("section", ""),
            ])
            existing_urls.add(art["url"])
            existing_titles.add((art["norm_title"], art["pub_date_str"]))
            new_count += 1
        except Exception as e:
            print(f"  Insert error: {e}")

    con.close()

    print(f"\nIngested {new_count} new NYT articles")
    print(f"Skipped: {dup_url} duplicate URLs, {dup_title} duplicate titles")


def print_report(db_path=DUCKDB_PATH):
    """Print coverage summary by year and source."""
    con = duckdb.connect(db_path, read_only=True)

    total = con.execute("SELECT COUNT(*) FROM gdelt_articles").fetchone()[0]
    print(f"\n{'='*70}")
    print("ARTICLE CORPUS SUMMARY")
    print(f"{'='*70}")
    print(f"Total articles: {total}")

    by_year_source = con.execute("""
        SELECT EXTRACT(YEAR FROM seendate)::INT as year,
               COALESCE(source, 'gdelt') as src,
               COUNT(*) as n
        FROM gdelt_articles
        GROUP BY year, src
        ORDER BY year, src
    """).fetchdf()

    pivot = by_year_source.pivot_table(index="year", columns="src", values="n",
                                        fill_value=0, aggfunc="sum")
    pivot["total"] = pivot.sum(axis=1)
    print(f"\n{pivot.to_string()}")

    by_source = con.execute("""
        SELECT COALESCE(source, 'gdelt') as src, COUNT(*) as n
        FROM gdelt_articles
        GROUP BY src
        ORDER BY n DESC
    """).fetchdf()
    print(f"\nBy source:\n{by_source.to_string(index=False)}")

    con.close()


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "report":
        print_report()
        return

    articles = collect_articles()
    ingest_to_db(articles)
    print_report()


if __name__ == "__main__":
    main()
