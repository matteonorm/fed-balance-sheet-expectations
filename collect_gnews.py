"""Collect ECB balance sheet news headlines from Google News RSS feeds.

Supports both current (undated) and historical (date-windowed) collection.
Uses quarterly windows with multiple query variants to maximize coverage.
"""

import time
import xml.etree.ElementTree as ET
from datetime import datetime

import duckdb
import requests

from config import DUCKDB_PATH
from schema import create_schema

QUERIES = [
    "ECB balance sheet",
    "ECB asset purchases",
    "ECB quantitative tightening",
    "ECB quantitative easing",
    "ECB bond purchases APP",
    "ECB PEPP purchases",
    "Eurosystem balance sheet bonds",
    "ECB bond holdings reduction",
    "ECB bond buying",
    "ECB tapering",
    "ECB reinvestment",
    "ECB APP programme",
    "ECB PEPP programme",
    "ECB sovereign bond",
    "ECB net asset purchases",
    "ECB balance sheet normalization",
    "ECB QE end",
    "ECB bond buying programme",
    "ECB portfolio runoff",
]

HISTORICAL_WINDOWS = []
for year in range(2014, 2027):
    HISTORICAL_WINDOWS.append((f"{year}-01-01", f"{year}-06-30"))
    HISTORICAL_WINDOWS.append((f"{year}-07-01", f"{year}-12-31"))

RSS_BASE = "https://news.google.com/rss/search?q={query}&hl=en&gl=US&ceid=US:en"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}


def fetch_rss(query, after=None, before=None):
    q = query.replace(" ", "+")
    if after and before:
        q += f"+after:{after}+before:{before}"
    url = RSS_BASE.format(query=q)
    try:
        resp = requests.get(url, timeout=15, headers=HEADERS)
        if resp.status_code != 200:
            return []
        return parse_rss(resp.text, query)
    except Exception:
        return []


def parse_rss(xml_text, query):
    articles = []
    try:
        root = ET.fromstring(xml_text)
        for item in root.findall(".//item"):
            title_el = item.find("title")
            link_el = item.find("link")
            pubdate_el = item.find("pubDate")
            source_el = item.find("source")

            if title_el is None or link_el is None:
                continue

            title = title_el.text or ""
            url = link_el.text or ""
            if not title or not url:
                continue

            pubdate = pubdate_el.text if pubdate_el is not None else ""
            domain = ""
            if source_el is not None:
                domain = source_el.get("url", "") or source_el.text or ""

            seen_dt = None
            if pubdate:
                for fmt in ["%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y %H:%M:%S %z"]:
                    try:
                        seen_dt = datetime.strptime(pubdate.strip(), fmt)
                        break
                    except ValueError:
                        continue

            seendate = seen_dt.strftime("%Y-%m-%d %H:%M:%S") if seen_dt else ""
            articles.append({
                "url": url,
                "title": title.strip(),
                "seendate": seendate,
                "domain": domain,
                "query": query,
            })
    except ET.ParseError:
        pass
    return articles


def insert_articles(con, articles):
    inserted = 0
    for art in articles:
        try:
            con.execute(
                """INSERT OR IGNORE INTO gdelt_articles
                   (url, title, seendate, domain, language, sourcecountry, query_keyword)
                   VALUES (?, ?, ?, ?, 'English', '', ?)""",
                [art["url"], art["title"], art["seendate"],
                 art["domain"], art["query"]],
            )
            inserted += 1
        except Exception:
            pass
    return inserted


def collect_gnews(db_path=DUCKDB_PATH, historical=True):
    create_schema()
    con = duckdb.connect(db_path)

    before_count = con.execute("SELECT COUNT(*) FROM gdelt_articles").fetchone()[0]
    total_fetched = 0

    # Current (undated) queries
    print("=== Current articles ===", flush=True)
    for query in QUERIES:
        articles = fetch_rss(query)
        n = insert_articles(con, articles)
        total_fetched += len(articles)
        if articles:
            print(f"  {query}: {len(articles)} found, {n} new", flush=True)
        time.sleep(1)

    # Historical backfill with date windows
    if historical:
        print("\n=== Historical backfill ===", flush=True)
        for start, end in HISTORICAL_WINDOWS:
            window_total = 0
            window_new = 0
            for query in QUERIES:
                articles = fetch_rss(query, after=start, before=end)
                n = insert_articles(con, articles)
                window_total += len(articles)
                window_new += n
                total_fetched += len(articles)
                time.sleep(0.5)

            half = "H1" if start.endswith("01-01") else "H2"
            year = start[:4]
            print(f"  {year} {half}: {window_total} fetched, {window_new} new",
                  flush=True)

    after_count = con.execute("SELECT COUNT(*) FROM gdelt_articles").fetchone()[0]
    net_new = after_count - before_count
    print(f"\nTotal articles in database: {after_count} (+{net_new} new)", flush=True)

    coverage = con.execute("""
        SELECT strftime(seendate, '%Y-%m') AS month, COUNT(*) AS n
        FROM gdelt_articles
        WHERE seendate IS NOT NULL AND length(CAST(seendate AS VARCHAR)) > 0
        GROUP BY month ORDER BY month
    """).fetchall()
    thin = sum(1 for _, n in coverage if n < 5)
    ok = sum(1 for _, n in coverage if n >= 10)
    print(f"\nCoverage: {len(coverage)} months, {ok} with 10+, {thin} with <5")

    con.close()
    return net_new


if __name__ == "__main__":
    collect_gnews()
