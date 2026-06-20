"""Collect ECB balance sheet data from two sources:

1. APP + PEPP holdings (monthly, from ECB published CSVs) — used for SMA comparison
2. Eurosystem policy balance sheet (weekly, from ECB Data Portal) — securities held
   for monetary policy (asset 7.1) + lending to credit institutions (asset 5, i.e. TLTROs)
"""

import calendar
import csv as csv_mod

import duckdb
import pandas as pd
import requests

from config import DUCKDB_PATH

ECB_BASE = "https://www.ecb.europa.eu"
APP_URL = f"{ECB_BASE}/mopo/pdf/APP_breakdown_history.csv"
PEPP_URL = f"{ECB_BASE}/mopo/pdf/PEPP_purchase_history.csv"

ECB_DATA_API = "https://data-api.ecb.europa.eu/service/data"
SECURITIES_URL = f"{ECB_DATA_API}/ILM/W.U2.C.A070100.U2.EUR?format=csvdata&startPeriod=2014-01&detail=dataonly"
LENDING_URL = f"{ECB_DATA_API}/ILM/W.U2.C.A050000.U2.EUR?format=csvdata&startPeriod=2014-01&detail=dataonly"

MONTHS = {
    "January": 1, "February": 2, "March": 3, "April": 4,
    "May": 5, "June": 6, "July": 7, "August": 8,
    "September": 9, "October": 10, "November": 11, "December": 12,
}


def parse_app_holdings(text):
    """Parse APP breakdown CSV. Returns list of (date, holdings_eur_millions)."""
    lines = list(csv_mod.reader(text.strip().split("\n")))
    results = []
    current_year = None

    for cols in lines:
        if len(cols) < 14:
            continue
        if cols[0].strip().isdigit():
            current_year = int(cols[0].strip())
        month_num = MONTHS.get(cols[1].strip())
        if not month_num or not current_year:
            continue
        try:
            holdings = []
            for i in range(10, 14):
                val = cols[i].strip().replace('"', '').replace(',', '')
                holdings.append(float(val) if val else 0.0)
            total_app = sum(holdings)
            if total_app == 0:
                continue
            last_day = calendar.monthrange(current_year, month_num)[1]
            results.append((f"{current_year}-{month_num:02d}-{last_day:02d}", total_app))
        except (ValueError, IndexError):
            continue
    return results


def parse_pepp_holdings(text):
    """Parse PEPP purchase history CSV. Returns list of (date, cumulative_eur_millions)."""
    lines = text.strip().split("\n")
    results = []
    current_year = None

    for line in lines:
        cols = line.split(",")
        if len(cols) < 4:
            continue
        if cols[0].strip().isdigit():
            current_year = int(cols[0].strip())
        month_num = MONTHS.get(cols[1].strip())
        if not month_num or not current_year:
            continue
        try:
            cumulative = float(cols[3].strip().replace('"', ''))
            last_day = calendar.monthrange(current_year, month_num)[1]
            results.append((f"{current_year}-{month_num:02d}-{last_day:02d}", cumulative))
        except (ValueError, IndexError):
            continue
    return results


def fetch_ecb_weekly_series(url):
    """Fetch a weekly ILM series. Returns list of (date_str, value_eur_millions)."""
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    lines = resp.text.strip().split("\n")
    results = []
    for line in lines[1:]:
        parts = line.split(",")
        time_period = parts[-2]
        value = float(parts[-1])
        year, week = time_period.split("-W")
        date = pd.Timestamp.fromisocalendar(int(year), int(week), 5)
        results.append((date.strftime("%Y-%m-%d"), value))
    return results


def collect_ecb_balance_sheet(db_path=DUCKDB_PATH):
    con = duckdb.connect(db_path)

    con.execute("""
        CREATE TABLE IF NOT EXISTS ecb_app_pepp (
            observation_date DATE PRIMARY KEY,
            app_holdings_eur DOUBLE,
            pepp_holdings_eur DOUBLE,
            total_holdings_eur DOUBLE,
            collected_at TIMESTAMP DEFAULT current_timestamp
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS ecb_policy_bs (
            observation_date DATE PRIMARY KEY,
            securities_eur DOUBLE,
            lending_eur DOUBLE,
            total_policy_eur DOUBLE,
            collected_at TIMESTAMP DEFAULT current_timestamp
        )
    """)

    # --- APP + PEPP monthly holdings ---
    print("Downloading APP holdings from ECB...", flush=True)
    resp_app = requests.get(APP_URL, timeout=30)
    resp_app.raise_for_status()
    app_data = parse_app_holdings(resp_app.text)
    print(f"  Parsed {len(app_data)} monthly APP observations", flush=True)

    print("Downloading PEPP holdings from ECB...", flush=True)
    resp_pepp = requests.get(PEPP_URL, timeout=30)
    resp_pepp.raise_for_status()
    pepp_data = parse_pepp_holdings(resp_pepp.text)
    print(f"  Parsed {len(pepp_data)} monthly PEPP observations", flush=True)

    app_dict = dict(app_data)
    pepp_dict = dict(pepp_data)
    all_dates = sorted(set(list(app_dict.keys()) + list(pepp_dict.keys())))

    con.execute("DELETE FROM ecb_app_pepp")
    count_ap = 0
    for date_str in all_dates:
        app_val = app_dict.get(date_str, 0.0)
        pepp_val = pepp_dict.get(date_str, 0.0)
        con.execute(
            """INSERT OR IGNORE INTO ecb_app_pepp
               (observation_date, app_holdings_eur, pepp_holdings_eur, total_holdings_eur)
               VALUES (?, ?, ?, ?)""",
            [date_str, app_val, pepp_val, app_val + pepp_val],
        )
        count_ap += 1
    print(f"  Loaded {count_ap} observations into ecb_app_pepp", flush=True)

    # --- Weekly policy balance sheet (securities + lending) ---
    print("\nDownloading weekly securities (asset 7.1) from ECB...", flush=True)
    sec_data = fetch_ecb_weekly_series(SECURITIES_URL)
    print(f"  Parsed {len(sec_data)} weekly observations", flush=True)

    print("Downloading weekly lending (asset 5) from ECB...", flush=True)
    lend_data = fetch_ecb_weekly_series(LENDING_URL)
    print(f"  Parsed {len(lend_data)} weekly observations", flush=True)

    sec_dict = dict(sec_data)
    lend_dict = dict(lend_data)
    all_weeks = sorted(set(list(sec_dict.keys()) + list(lend_dict.keys())))

    con.execute("DELETE FROM ecb_policy_bs")
    count_pol = 0
    for date_str in all_weeks:
        sec_val = sec_dict.get(date_str, 0.0)
        lend_val = lend_dict.get(date_str, 0.0)
        con.execute(
            """INSERT OR IGNORE INTO ecb_policy_bs
               (observation_date, securities_eur, lending_eur, total_policy_eur)
               VALUES (?, ?, ?, ?)""",
            [date_str, sec_val, lend_val, sec_val + lend_val],
        )
        count_pol += 1
    print(f"  Loaded {count_pol} observations into ecb_policy_bs", flush=True)

    # Summary
    for table, label in [("ecb_app_pepp", "APP+PEPP"), ("ecb_policy_bs", "Policy BS")]:
        sample = con.execute(f"""
            SELECT * FROM {table} ORDER BY observation_date DESC LIMIT 3
        """).fetchall()
        print(f"\n{label} latest (EUR millions):")
        for row in sample:
            print(f"  {row[0]}: {row[1]:,.0f} | {row[2]:,.0f} | total={row[3]:,.0f}")

    con.close()
    return count_ap, count_pol


if __name__ == "__main__":
    from schema import create_schema
    create_schema()
    collect_ecb_balance_sheet()
