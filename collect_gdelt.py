"""
Collect news articles about the Fed balance sheet from GDELT DOC 2.0 API.
GDELT coverage starts ~2015, no API key needed.

Usage:
    python collect_gdelt.py          # collect articles
    python collect_gdelt.py report   # print coverage summary
"""

import os
import sys
import time
import json
import requests
import duckdb
from datetime import datetime, timedelta
from config import (DUCKDB_PATH, GDELT_KEYWORDS, GDELT_API_URL,
                    GDELT_DELAY_SECONDS, GDELT_MAX_RETRIES, DATE_START, DATE_END)


def query_gdelt(keyword, start_date, end_date, max_records=250):
    """Query GDELT DOC 2.0 API for articles matching keyword."""
    params = {
        "query": keyword,
        "mode": "artlist",
        "maxrecords": max_records,
        "format": "json",
        "startdatetime": start_date.strftime("%Y%m%d%H%M%S"),
        "enddatetime": end_date.strftime("%Y%m%d%H%M%S"),
        "sort": "datedesc",
    }

    for attempt in range(GDELT_MAX_RETRIES):
        try:
            resp = requests.get(GDELT_API_URL, params=params, timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                return data.get("articles", [])
            elif resp.status_code == 429:
                wait = GDELT_DELAY_SECONDS * (attempt + 1)
                print(f"    Rate limited, waiting {wait}s...")
                time.sleep(wait)
            else:
                print(f"    HTTP {resp.status_code} for {keyword[:30]}")
                return []
        except Exception as e:
            print(f"    Error: {e}")
            time.sleep(GDELT_DELAY_SECONDS)

    return []


def collect_articles(db_path=DUCKDB_PATH):
    """Collect articles across all keywords and time windows."""
    con = duckdb.connect(db_path)

    existing = con.execute("SELECT COUNT(*) FROM gdelt_articles").fetchone()[0]
    existing_urls = set()
    if existing > 0:
        urls = con.execute("SELECT url FROM gdelt_articles").fetchall()
        existing_urls = {r[0] for r in urls}
    print(f"Existing articles: {existing}")

    start = datetime.strptime("2015-01-01", "%Y-%m-%d")
    end = datetime.strptime(DATE_END, "%Y-%m-%d")

    windows = []
    current = start
    while current < end:
        window_end = min(current + timedelta(days=180), end)
        windows.append((current, window_end))
        current = window_end

    total_new = 0
    for keyword in GDELT_KEYWORDS:
        print(f"\nKeyword: {keyword}")
        kw_new = 0

        for w_start, w_end in windows:
            articles = query_gdelt(keyword, w_start, w_end)
            new_count = 0

            for art in articles:
                url = art.get("url", "")
                if not url or url in existing_urls:
                    continue

                title = art.get("title", "")
                if not title:
                    continue

                seendate = art.get("seendate", "")
                try:
                    seen_dt = datetime.strptime(seendate[:14], "%Y%m%dT%H%M%S")
                except (ValueError, IndexError):
                    seen_dt = w_start

                try:
                    con.execute("""
                        INSERT OR IGNORE INTO gdelt_articles
                        (url, title, seendate, domain, language, sourcecountry, query_keyword)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, [url, title, seen_dt.isoformat(),
                          art.get("domain", ""), art.get("language", ""),
                          art.get("sourcecountry", ""), keyword])
                    existing_urls.add(url)
                    new_count += 1
                except Exception:
                    pass

            kw_new += new_count
            time.sleep(GDELT_DELAY_SECONDS)

        print(f"  {kw_new} new articles")
        total_new += kw_new

    total = con.execute("SELECT COUNT(*) FROM gdelt_articles").fetchone()[0]
    con.close()
    print(f"\nTotal new: {total_new}, Total in DB: {total}")


def print_report(db_path=DUCKDB_PATH):
    """Print coverage summary."""
    con = duckdb.connect(db_path, read_only=True)

    total = con.execute("SELECT COUNT(*) FROM gdelt_articles").fetchone()[0]
    if total == 0:
        print("No articles collected yet.")
        return

    print(f"\n{'='*60}")
    print("GDELT ARTICLE COVERAGE")
    print(f"{'='*60}")
    print(f"Total articles: {total}")

    by_year = con.execute("""
        SELECT EXTRACT(YEAR FROM seendate) as year,
               COUNT(*) as n,
               COUNT(DISTINCT domain) as domains
        FROM gdelt_articles
        GROUP BY year
        ORDER BY year
    """).fetchdf()
    print(f"\n{by_year.to_string(index=False)}")

    by_kw = con.execute("""
        SELECT query_keyword, COUNT(*) as n
        FROM gdelt_articles
        GROUP BY query_keyword
        ORDER BY n DESC
    """).fetchdf()
    print(f"\nBy keyword:\n{by_kw.to_string(index=False)}")

    con.close()


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "report":
        print_report()
        return

    from schema import create_schema
    create_schema()
    collect_articles()
    print_report()


if __name__ == "__main__":
    main()
