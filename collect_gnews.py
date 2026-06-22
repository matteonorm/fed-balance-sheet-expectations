"""
Collect news articles about the Fed balance sheet from Google News RSS.
Coverage goes back ~6-12 months from current date.

Usage:
    python collect_gnews.py
"""

import os
import sys
import re
import time
import xml.etree.ElementTree as ET
import requests
import duckdb
from datetime import datetime
from urllib.parse import quote
from config import DUCKDB_PATH

GNEWS_QUERIES = [
    "Federal Reserve balance sheet",
    "Fed balance sheet reduction",
    "quantitative tightening Fed",
    "SOMA portfolio Federal Reserve",
    "Fed tapering",
    "Fed asset purchases",
    "Treasury runoff Federal Reserve",
    "MBS runoff Fed",
    "Federal Reserve QT",
    "Federal Reserve QE",
    "Fed balance sheet normalization",
    "FOMC balance sheet",
    "Fed reserves ample",
    "SOMA holdings",
]

GNEWS_RSS = "https://news.google.com/rss/search"


def fetch_gnews_rss(query, when=None):
    """Fetch Google News RSS for a query."""
    params = {"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"}
    if when:
        params["q"] += f" when:{when}"

    try:
        resp = requests.get(GNEWS_RSS, params=params, timeout=15,
                           headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code != 200:
            return []

        root = ET.fromstring(resp.content)
        articles = []
        for item in root.findall('.//item'):
            title = item.findtext('title', '')
            link = item.findtext('link', '')
            pubdate = item.findtext('pubDate', '')
            source = item.findtext('source', '')

            if not title or not link:
                continue

            try:
                dt = datetime.strptime(pubdate, "%a, %d %b %Y %H:%M:%S %Z")
            except (ValueError, TypeError):
                dt = datetime.now()

            articles.append({
                'title': re.sub(r' - .*$', '', title),
                'url': link,
                'seendate': dt,
                'domain': source or '',
            })

        return articles

    except Exception as e:
        print(f"  Error fetching {query[:30]}: {e}")
        return []


def main():
    from schema import create_schema
    create_schema()

    con = duckdb.connect(DUCKDB_PATH)

    existing_urls = set()
    try:
        urls = con.execute("SELECT url FROM gdelt_articles").fetchall()
        existing_urls = {r[0] for r in urls}
    except Exception:
        pass

    total_new = 0
    for query in GNEWS_QUERIES:
        for when in [None, "7d", "1m", "6m", "1y"]:
            articles = fetch_gnews_rss(query, when)
            new_count = 0

            for art in articles:
                if art['url'] in existing_urls:
                    continue

                try:
                    con.execute("""
                        INSERT OR IGNORE INTO gdelt_articles
                        (url, title, seendate, domain, language, sourcecountry, query_keyword)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, [art['url'], art['title'], art['seendate'].isoformat(),
                          art['domain'], 'English', 'United States',
                          f"gnews:{query}"])
                    existing_urls.add(art['url'])
                    new_count += 1
                except Exception:
                    pass

            total_new += new_count
            time.sleep(1)

        if new_count > 0:
            print(f"  {query}: {new_count} new")

    total = con.execute("SELECT COUNT(*) FROM gdelt_articles").fetchone()[0]
    con.close()
    print(f"\nGoogle News: {total_new} new articles, {total} total in DB")


if __name__ == "__main__":
    main()
