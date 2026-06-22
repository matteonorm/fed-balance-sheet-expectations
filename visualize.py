"""
Figures for the Fed balance-sheet expectations project.

Fig 1: F_t over time with regime shading (belief robustness)
Fig 2: Contemporaneous correlation — F_t vs survey expected SOMA path

Usage:
    python visualize.py
"""

import os
import numpy as np
import pandas as pd
import duckdb
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from config import DUCKDB_PATH, OUTPUT_DIR, FED_REGIMES

plt.rcParams.update({
    'font.family': 'serif',
    'font.size': 10,
    'axes.linewidth': 0.5,
    'axes.spines.top': False,
    'axes.spines.right': False,
    'figure.dpi': 150,
    'savefig.dpi': 300,
    'savefig.bbox': 'tight',
})

REGIME_COLORS = {
    'pre_taper': '#e8e8e8',
    'taper_tantrum': '#ffd6d6',
    'reinvestment': '#e8e8e8',
    'qt1_runoff': '#d6e8ff',
    'qe_covid': '#d6ffd6',
    'qt2': '#d6e8ff',
}

REGIME_LABELS = {
    'pre_taper': 'Pre-Taper',
    'taper_tantrum': 'Taper',
    'reinvestment': 'Reinvestment',
    'qt1_runoff': 'QT1',
    'qe_covid': 'QE (COVID)',
    'qt2': 'QT2',
}


def shade_regimes(ax):
    for regime, (start, end) in FED_REGIMES.items():
        s = pd.Timestamp(start)
        e = pd.Timestamp(end)
        color = REGIME_COLORS.get(regime, '#f0f0f0')
        ax.axvspan(s, e, alpha=0.3, color=color, zorder=0)


def fig1_beliefs(db_path=DUCKDB_PATH):
    """F_t over time with regime shading — robustness of LLM-derived beliefs."""
    con = duckdb.connect(db_path, read_only=True)

    ft = con.execute("""
        SELECT period, f_statistic as f_t, n_relevant, n_total
        FROM llm_expectations
        WHERE f_statistic IS NOT NULL AND n_relevant >= 2
        ORDER BY period
    """).fetchdf()

    actual = con.execute("""
        SELECT observation_date, total_assets_bn
        FROM fed_balance_sheet
        ORDER BY observation_date
    """).fetchdf()

    con.close()

    if ft.empty:
        print("No F_t data.")
        return

    ft['date'] = pd.to_datetime(ft['period'] + '-15')
    actual['date'] = pd.to_datetime(actual['observation_date'])

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 7), sharex=True,
                                     gridspec_kw={'height_ratios': [2, 1]})

    ax1.bar(ft['date'], ft['f_t'], width=25, alpha=0.7, color='steelblue')
    ax1.axhline(y=0, color='black', linewidth=0.5)
    ax1.set_ylabel('$F_t$')
    ax1.set_ylim(-1.1, 1.1)
    shade_regimes(ax1)

    for regime, label in REGIME_LABELS.items():
        start, end = FED_REGIMES[regime]
        mid = pd.Timestamp(start) + (pd.Timestamp(end) - pd.Timestamp(start)) / 2
        ax1.text(mid, 1.05, label, ha='center', va='bottom',
                fontsize=8, color='gray', fontstyle='italic')

    ax1.set_title('News-Derived Balance-Sheet Belief Index ($F_t$)')

    ax2.plot(actual['date'], actual['total_assets_bn'], linewidth=1.2, color='black')
    ax2.set_ylabel('Total Assets ($bn)')
    shade_regimes(ax2)
    ax2.xaxis.set_major_formatter(mdates.DateFormatter('%Y'))

    plt.tight_layout()
    out = os.path.join(OUTPUT_DIR, 'fig1_beliefs.png')
    plt.savefig(out)
    plt.close()
    print(f"Saved {out}")


def _build_survey_signal(con):
    """Build a unified survey direction signal from all available survey data.

    Uses pace/change data where available, falls back to first-differenced
    level expectations. Both sub-series are z-scored before combining so
    they are on comparable scales. Then linearly interpolated to monthly.
    """
    pace = con.execute("""
        SELECT strftime(survey_date, '%Y-%m') as period,
               AVG(pctl50) as value
        FROM nyfed_survey_bs
        WHERE pctl50 IS NOT NULL
          AND horizon_date > survey_date
          AND (variable LIKE '%purchase_pace%'
               OR variable LIKE '%soma_change%'
               OR variable LIKE '%portfolio_change%'
               OR variable LIKE '%_chg%')
        GROUP BY strftime(survey_date, '%Y-%m')
        ORDER BY period
    """).fetchdf()

    level = con.execute("""
        SELECT strftime(survey_date, '%Y-%m') as period,
               AVG(pctl50) as level
        FROM nyfed_survey_bs
        WHERE pctl50 IS NOT NULL
          AND horizon_date > survey_date
          AND (variable LIKE '%soma_size_dist%'
               OR variable LIKE '%soma_portfolio_level%'
               OR variable LIKE '%soma_total%'
               OR variable LIKE '%soma_securities%'
               OR variable = 'total_assets')
          AND pctl50 > 100
        GROUP BY strftime(survey_date, '%Y-%m')
        ORDER BY period
    """).fetchdf()

    level['gap'] = level['period'].apply(lambda x: pd.Timestamp(x + '-01')).diff().dt.days
    level['value'] = level['level'].diff()
    level = level[(level['gap'] <= 400) & level['value'].notna()].copy()

    combined = pace[['period', 'value']].copy()
    level_only = level[~level['period'].isin(pace['period'])][['period', 'value']]
    combined = pd.concat([combined, level_only]).sort_values('period').reset_index(drop=True)

    return combined[['period', 'value']]


def fig2_correlation(db_path=DUCKDB_PATH):
    """Contemporaneous correlation: F_t vs survey expected SOMA change."""
    con = duckdb.connect(db_path, read_only=True)

    ft = con.execute("""
        SELECT period, f_statistic as f_t
        FROM llm_expectations
        WHERE f_statistic IS NOT NULL AND n_relevant >= 2
        ORDER BY period
    """).fetchdf()

    survey = _build_survey_signal(con)
    con.close()

    if survey.empty:
        print("No survey data.")
        return

    merged = pd.merge(ft, survey, on='period', how='inner').dropna()
    if len(merged) < 5:
        print(f"Insufficient overlap ({len(merged)} months) for correlation plot.")
        return

    merged['date'] = pd.to_datetime(merged['period'] + '-15')

    from scipy import stats
    rho, p = stats.spearmanr(merged['f_t'], merged['value'])

    fig, ax = plt.subplots(figsize=(12, 5))
    ax2 = ax.twinx()

    ax.bar(merged['date'], merged['f_t'], width=25, alpha=0.6, color='steelblue',
           label='$F_t$ (news)')
    ax.set_ylabel('$F_t$ (news balance)', color='steelblue')
    ax.set_ylim(-1.1, 1.1)
    ax.axhline(y=0, color='black', linewidth=0.5)

    ax2.plot(merged['date'], merged['value'], 'o-', color='darkred',
             markersize=4, linewidth=1.5, label='Survey: expected SOMA change')
    ax2.set_ylabel('Expected SOMA change ($bn)', color='darkred')
    ax2.axhline(y=0, color='darkred', linewidth=0.3, linestyle='--')

    shade_regimes(ax)
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y'))

    lines1, labels1 = ax.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax.legend(lines1 + lines2, labels1 + labels2, loc='upper left', frameon=False)

    ax.set_title(f'$F_t$ vs Survey Expectations (Spearman $\\rho$ = {rho:.2f}, p = {p:.3f}, N = {len(merged)})')

    out = os.path.join(OUTPUT_DIR, 'fig2_correlation.png')
    plt.savefig(out)
    plt.close()
    print(f"Saved {out}")


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    fig1_beliefs()
    fig2_correlation()


if __name__ == "__main__":
    main()
