"""
Collect Fed balance-sheet actuals from FRED (Federal Reserve Economic Data).

Series:
    WALCL  - Total assets (weekly, millions)
    TREAST - Treasury securities held (weekly, millions)
    WSHOMCB - MBS held (weekly, millions)
    WRESBAL - Reserve balances (weekly, millions)

Usage:
    python collect_fred.py
"""

import os
import sys
import requests
import pandas as pd
import duckdb
from config import DUCKDB_PATH, DATE_START, DATE_END

FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"
FRED_API_KEY = os.environ.get("FRED_API_KEY", "")

SERIES = {
    "WALCL": "total_assets_bn",
    "TREAST": "treasury_bn",
    "WSHOMCB": "mbs_bn",
    "WRESBAL": "reserves_bn",
}


def fetch_fred_series(series_id, api_key=None):
    """Fetch a FRED series via API or CSV fallback."""
    if api_key:
        params = {
            "series_id": series_id,
            "api_key": api_key,
            "file_type": "json",
            "observation_start": DATE_START,
            "observation_end": DATE_END,
            "frequency": "w",
        }
        resp = requests.get(FRED_BASE, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        rows = []
        for obs in data.get("observations", []):
            if obs["value"] != ".":
                rows.append({
                    "date": obs["date"],
                    "value": float(obs["value"]),
                })
        return pd.DataFrame(rows)

    csv_url = (
        f"https://fred.stlouisfed.org/graph/fredgraph.csv"
        f"?id={series_id}"
        f"&cosd={DATE_START}&coed={DATE_END}"
        f"&fq=Weekly"
    )
    resp = requests.get(csv_url, timeout=30)
    resp.raise_for_status()

    from io import StringIO
    df = pd.read_csv(StringIO(resp.text))
    df.columns = ["date", "value"]
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df = df.dropna(subset=["value"])
    return df


def main():
    from schema import create_schema
    create_schema()

    api_key = FRED_API_KEY
    if not api_key:
        print("No FRED_API_KEY set, using CSV download fallback (no key needed)")

    con = duckdb.connect(DUCKDB_PATH)
    con.execute("DELETE FROM fed_balance_sheet")

    all_data = {}
    for series_id, col_name in SERIES.items():
        print(f"Fetching {series_id}...")
        try:
            df = fetch_fred_series(series_id, api_key if api_key else None)
            df["value_bn"] = df["value"] / 1e3  # FRED reports in millions
            all_data[col_name] = df.set_index("date")["value_bn"]
            print(f"  {len(df)} observations ({df['date'].min()} to {df['date'].max()})")
        except Exception as e:
            print(f"  ERROR: {e}")
            all_data[col_name] = pd.Series(dtype=float)

    combined = pd.DataFrame(all_data)
    combined.index.name = "date"
    combined = combined.dropna(how="all")

    print(f"\nCombined: {len(combined)} observations")

    for date, row in combined.iterrows():
        con.execute("""
            INSERT OR REPLACE INTO fed_balance_sheet
            (observation_date, total_assets_bn, treasury_bn, mbs_bn, reserves_bn)
            VALUES (?, ?, ?, ?, ?)
        """, [date, row.get("total_assets_bn"), row.get("treasury_bn"),
              row.get("mbs_bn"), row.get("reserves_bn")])

    count = con.execute("SELECT COUNT(*) FROM fed_balance_sheet").fetchone()[0]
    sample = con.execute("""
        SELECT observation_date, total_assets_bn, reserves_bn
        FROM fed_balance_sheet
        ORDER BY observation_date DESC
        LIMIT 5
    """).fetchdf()

    print(f"\nIngested {count} rows to fed_balance_sheet")
    print("\nLatest observations:")
    print(sample.to_string(index=False))

    con.close()


if __name__ == "__main__":
    main()
