"""
Collect NY Fed Survey of Primary Dealers / Market Participants / Market Expectations
balance-sheet data from Excel files (available July 2023 onwards).

Usage:
    python collect_nyfed_survey.py          # discover + download + ingest
    python collect_nyfed_survey.py report   # print coverage summary only
"""

import os
import sys
import re
import requests
import duckdb
import openpyxl
from datetime import datetime
from config import DUCKDB_PATH, DATA_DIR, NYFED_SURVEY_BASE

SURVEY_DIR = os.path.join(DATA_DIR, "nyfed_surveys")
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

INDEX_PAGES = [
    "https://www.newyorkfed.org/markets/primarydealer_survey_questions",
    "https://www.newyorkfed.org/markets/survey_market_participants",
    "https://www.newyorkfed.org/markets/market-intelligence/survey-of-market-expectations",
]

BS_THEME = "balance_sheet_policy_expectations"

BS_PATH_TAGS = {
    "fed_assets_total_assets_pathofmodes_levels": "total_assets",
    "fed_assets_tsy_pathofmodes_levels": "treasury_holdings",
    "fed_assets_tsy_pathofmodes_changes": "treasury_holdings_chg",
    "fed_assets_ambs_pathofmodes_levels": "agency_mbs",
    "fed_assets_ambs_pathofmodes_changes": "agency_mbs_chg",
    "fed_liabilities_reserves_pathofmodes_levels": "reserves",
    "fed_liabilities_onrrp_pathofmodes_levels": "onrrp",
    "fed_liabilities_currency_pathofmodes_levels": "currency",
    "fed_liabilities_tga_pathofmodes_levels": "tga",
    "fed_assets_soma_pathofmodes_rmpbills": "soma_rmp_bills",
    "fed_assets_soma_pathofmodes_rmpnotesandbonds": "soma_rmp_notesbonds",
}

BS_RUNOFF_TAGS = {
    "fed_assets_soma_dropdown_runoffendtiming": "soma_runoff_end_timing",
    "fed_assets_soma_dropdown_runoffendsize": "soma_runoff_end_size",
    "fed_assets_soma_dropdown_runoffslowtiming": "soma_runoff_slow_timing",
    "fed_assets_soma_dropdown_rmpstarttiming": "soma_rmp_start_timing",
    "fed_assets_soma_probdist_runoffendsize": "soma_runoff_end_size_dist",
    "fed_liabilities_reserves_dropdown_runoffendsize": "reserves_at_runoff_end",
    "fed_liabilities_onrrp_dropdown_runoffendsize": "onrrp_at_runoff_end",
    "fed_liabilities_reserves_dropdown_levelatrmpstart": "reserves_at_rmp_start",
}


def discover_excel_urls():
    """Scrape index pages for Excel data file URLs."""
    urls = set()
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT

    for page_url in INDEX_PAGES:
        try:
            resp = session.get(page_url, timeout=30)
            resp.raise_for_status()
            for match in re.finditer(r'href="([^"]*survey[^"]*\.xlsx)"', resp.text):
                path = match.group(1)
                if path.startswith("/"):
                    full_url = f"https://www.newyorkfed.org{path}"
                else:
                    full_url = path
                urls.add(full_url)
        except Exception as e:
            print(f"  Warning: failed to fetch {page_url}: {e}")

    return sorted(urls)


def download_excel_files(urls):
    """Download Excel files that don't already exist locally."""
    os.makedirs(SURVEY_DIR, exist_ok=True)
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT

    downloaded = []
    for url in urls:
        fname = url.split("/")[-1]
        local_path = os.path.join(SURVEY_DIR, fname)

        if os.path.exists(local_path) and os.path.getsize(local_path) > 1000:
            downloaded.append(local_path)
            continue

        try:
            resp = session.get(url, timeout=30)
            resp.raise_for_status()
            with open(local_path, "wb") as f:
                f.write(resp.content)
            downloaded.append(local_path)
            print(f"  Downloaded {fname} ({len(resp.content)} bytes)")
        except Exception as e:
            print(f"  Failed to download {fname}: {e}")

    return downloaded


def parse_excel_file(filepath):
    """Parse a single Excel file and return balance-sheet rows."""
    wb = openpyxl.load_workbook(filepath, read_only=True)
    ws = wb["Sheet1"]
    fname = os.path.basename(filepath)

    path_rows = []
    runoff_rows = []

    for row in ws.iter_rows(min_row=2, values_only=True):
        theme = row[4] or ""
        if BS_THEME not in theme.lower():
            continue

        survey_date = row[0]
        panel_type = row[2] or "Combined"
        question_tag = row[10] or ""
        agg = row[19] or ""
        val = row[20]
        horizon_date = row[15]
        top_header = row[12]
        left_header = row[13]

        if question_tag in BS_PATH_TAGS:
            variable = BS_PATH_TAGS[question_tag]
            if horizon_date and agg in ("pctl25", "pctl50", "pctl75", "count"):
                path_rows.append({
                    "survey_date": _to_date(survey_date),
                    "panel_type": panel_type,
                    "variable": variable,
                    "horizon_date": _to_date(horizon_date),
                    "agg": agg,
                    "val": val,
                    "source_file": fname,
                })

        elif question_tag in BS_RUNOFF_TAGS:
            variable = BS_RUNOFF_TAGS[question_tag]
            if agg in ("pctl25", "pctl50", "pctl75", "count"):
                runoff_rows.append({
                    "survey_date": _to_date(survey_date),
                    "panel_type": panel_type,
                    "variable": variable,
                    "agg": agg,
                    "val": str(val) if val is not None else None,
                    "source_file": fname,
                })

    wb.close()
    return path_rows, runoff_rows


def _to_date(val):
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.strftime("%Y-%m-%d")
    return str(val)[:10]


def pivot_path_rows(rows):
    """Pivot from long (one row per agg) to wide (pctl25/50/75 + count per key)."""
    key_data = {}
    for r in rows:
        key = (r["survey_date"], r["panel_type"], r["variable"], r["horizon_date"], r["source_file"])
        if key not in key_data:
            key_data[key] = {"pctl25": None, "pctl50": None, "pctl75": None, "respondent_count": None}
        if r["agg"] == "count":
            key_data[key]["respondent_count"] = int(r["val"]) if r["val"] else None
        else:
            key_data[key][r["agg"]] = float(r["val"]) if r["val"] is not None else None

    result = []
    for (sd, pt, var, hd, sf), vals in key_data.items():
        result.append({
            "survey_date": sd,
            "panel_type": pt,
            "variable": var,
            "horizon_date": hd,
            "source_file": sf,
            **vals,
        })
    return result


def pivot_runoff_rows(rows):
    """Pivot runoff rows from long to wide."""
    key_data = {}
    for r in rows:
        key = (r["survey_date"], r["panel_type"], r["variable"], r["source_file"])
        if key not in key_data:
            key_data[key] = {"pctl25": None, "pctl50": None, "pctl75": None, "respondent_count": None}
        if r["agg"] == "count":
            key_data[key]["respondent_count"] = int(float(r["val"])) if r["val"] else None
        else:
            key_data[key][r["agg"]] = r["val"]

    result = []
    for (sd, pt, var, sf), vals in key_data.items():
        result.append({
            "survey_date": sd,
            "panel_type": pt,
            "variable": var,
            "source_file": sf,
            **vals,
        })
    return result


def ingest_to_duckdb(path_data, runoff_data, db_path=DUCKDB_PATH):
    """Write parsed survey data to DuckDB."""
    con = duckdb.connect(db_path)

    con.execute("DELETE FROM nyfed_survey_bs")
    for r in path_data:
        con.execute("""
            INSERT OR REPLACE INTO nyfed_survey_bs
            (survey_date, panel_type, variable, horizon_date, pctl25, pctl50, pctl75, respondent_count, source_file)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [r["survey_date"], r["panel_type"], r["variable"], r["horizon_date"],
              r["pctl25"], r["pctl50"], r["pctl75"], r["respondent_count"], r["source_file"]])

    con.execute("DELETE FROM nyfed_survey_runoff")
    for r in runoff_data:
        con.execute("""
            INSERT OR REPLACE INTO nyfed_survey_runoff
            (survey_date, panel_type, variable, pctl25, pctl50, pctl75, respondent_count, source_file)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, [r["survey_date"], r["panel_type"], r["variable"],
              r["pctl25"], r["pctl50"], r["pctl75"], r["respondent_count"], r["source_file"]])

    con.close()


def print_coverage_report(db_path=DUCKDB_PATH):
    """Print a detailed coverage summary."""
    con = duckdb.connect(db_path, read_only=True)

    print("\n" + "=" * 80)
    print("NY FED SURVEY BALANCE-SHEET COVERAGE REPORT")
    print("=" * 80)

    # Rounds
    rounds = con.execute("""
        SELECT DISTINCT survey_date FROM nyfed_survey_bs
        UNION
        SELECT DISTINCT survey_date FROM nyfed_survey_runoff
        ORDER BY survey_date
    """).fetchall()
    print(f"\nTotal survey rounds with Excel data: {len(rounds)}")
    print(f"Date range: {rounds[0][0]} to {rounds[-1][0]}")
    print("\nRounds by year:")
    for r in rounds:
        d = str(r[0])
        print(f"  {d}")

    # Path variables coverage
    print("\n--- SOMA PATH VARIABLES (levels/changes by horizon) ---")
    path_vars = con.execute("""
        SELECT variable, COUNT(DISTINCT survey_date) as n_rounds,
               MIN(survey_date) as first, MAX(survey_date) as last,
               COUNT(*) as n_datapoints
        FROM nyfed_survey_bs
        WHERE panel_type = 'Combined'
        GROUP BY variable
        ORDER BY variable
    """).fetchall()
    print(f"{'Variable':<25} {'Rounds':>7} {'First':<12} {'Last':<12} {'Datapoints':>10}")
    print("-" * 70)
    for var, n, first, last, dp in path_vars:
        print(f"{var:<25} {n:>7} {str(first):<12} {str(last):<12} {dp:>10}")

    # Runoff variables coverage
    print("\n--- RUNOFF / TERMINAL SIZE VARIABLES ---")
    runoff_vars = con.execute("""
        SELECT variable, COUNT(DISTINCT survey_date) as n_rounds,
               MIN(survey_date) as first, MAX(survey_date) as last
        FROM nyfed_survey_runoff
        WHERE panel_type = 'Combined'
        GROUP BY variable
        ORDER BY variable
    """).fetchall()
    print(f"{'Variable':<30} {'Rounds':>7} {'First':<12} {'Last':<12}")
    print("-" * 65)
    for var, n, first, last in runoff_vars:
        print(f"{var:<30} {n:>7} {str(first):<12} {str(last):<12}")

    # Sample of median Total Assets path from latest round
    print("\n--- SAMPLE: Latest round Total Assets median path ---")
    sample = con.execute("""
        SELECT survey_date, horizon_date, pctl50, respondent_count
        FROM nyfed_survey_bs
        WHERE variable = 'total_assets'
          AND panel_type = 'Combined'
          AND survey_date = (SELECT MAX(survey_date) FROM nyfed_survey_bs WHERE variable = 'total_assets')
        ORDER BY horizon_date
    """).fetchall()
    if sample:
        print(f"Survey date: {sample[0][0]}")
        print(f"{'Horizon':<12} {'Median ($bn)':>14} {'N':>5}")
        for _, hd, med, n in sample:
            print(f"{str(hd):<12} {med:>14,.0f} {n:>5}")

    # Sample of runoff end timing from latest available round
    print("\n--- SAMPLE: Latest SOMA runoff end timing ---")
    timing = con.execute("""
        SELECT survey_date, pctl25, pctl50, pctl75, respondent_count
        FROM nyfed_survey_runoff
        WHERE variable = 'soma_runoff_end_timing'
          AND panel_type = 'Combined'
        ORDER BY survey_date DESC
        LIMIT 3
    """).fetchall()
    if timing:
        print(f"{'Survey Date':<14} {'p25':<14} {'p50':<14} {'p75':<14} {'N':>5}")
        for sd, p25, p50, p75, n in timing:
            print(f"{str(sd):<14} {str(p25):<14} {str(p50):<14} {str(p75):<14} {n:>5}")

    # Sample of runoff end size
    print("\n--- SAMPLE: Latest SOMA runoff end size ($bn) ---")
    size = con.execute("""
        SELECT survey_date, pctl25, pctl50, pctl75, respondent_count
        FROM nyfed_survey_runoff
        WHERE variable = 'soma_runoff_end_size'
          AND panel_type = 'Combined'
        ORDER BY survey_date DESC
        LIMIT 3
    """).fetchall()
    if size:
        print(f"{'Survey Date':<14} {'p25':>10} {'p50':>10} {'p75':>10} {'N':>5}")
        for sd, p25, p50, p75, n in size:
            print(f"{str(sd):<14} {str(p25):>10} {str(p50):>10} {str(p75):>10} {n:>5}")

    con.close()


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "report":
        print_coverage_report()
        return

    from schema import create_schema
    create_schema()

    print("Step 1: Discovering Excel data files from NY Fed...")
    urls = discover_excel_urls()
    print(f"  Found {len(urls)} Excel files")

    print("\nStep 2: Downloading...")
    files = download_excel_files(urls)
    print(f"  {len(files)} files ready")

    print("\nStep 3: Parsing balance-sheet data...")
    all_path = []
    all_runoff = []
    for f in sorted(files):
        path_rows, runoff_rows = parse_excel_file(f)
        all_path.extend(path_rows)
        all_runoff.extend(runoff_rows)
        fname = os.path.basename(f)
        print(f"  {fname}: {len(path_rows)} path rows, {len(runoff_rows)} runoff rows")

    print("\nStep 4: Pivoting to wide format...")
    path_data = pivot_path_rows(all_path)
    runoff_data = pivot_runoff_rows(all_runoff)
    print(f"  {len(path_data)} path observations, {len(runoff_data)} runoff observations")

    print("\nStep 5: Ingesting to DuckDB...")
    ingest_to_duckdb(path_data, runoff_data)
    print("  Done")

    print_coverage_report()


if __name__ == "__main__":
    main()
