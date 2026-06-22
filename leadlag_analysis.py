"""
Lead-lag analysis: Do news-derived beliefs (F_t) lead professional forecaster
expectations of the Fed's balance-sheet path?

Key design choices (baked in from ECB lessons):
1. All analysis in FIRST DIFFERENCES — level correlations are shared-trend mirages.
2. HAC/Newey-West standard errors — survey pace is autocorrelated.
3. Sign conventions stated explicitly.
4. Pre-specified test: positive sign, short positive lag (1-2 months).
5. Report nulls as nulls.
6. Regime-aware: test whether lead holds across taper/QT1/QE/QT2 or only at transitions.

Usage:
    python leadlag_analysis.py
"""

import numpy as np
import pandas as pd
import duckdb
from scipy import stats
from config import DUCKDB_PATH, FED_REGIMES, OUTPUT_DIR
import os

try:
    from statsmodels.regression.linear_model import OLS
    from statsmodels.tools.tools import add_constant
    HAS_STATSMODELS = True
except ImportError:
    HAS_STATSMODELS = False


def load_data(db_path=DUCKDB_PATH):
    """Load and merge F_t with survey pace measures."""
    con = duckdb.connect(db_path, read_only=True)

    ft = con.execute("""
        SELECT period, f_statistic as f_t, n_relevant
        FROM llm_expectations
        WHERE f_statistic IS NOT NULL
        ORDER BY period
    """).fetchdf()

    survey = con.execute("""
        SELECT
            strftime(survey_date, '%Y-%m') as period,
            AVG(CASE WHEN variable LIKE '%total%' OR variable LIKE '%soma%' THEN pctl50 END) as total_assets_median,
            AVG(CASE WHEN variable LIKE '%treasury%' THEN pctl50 END) as tsy_median,
            AVG(CASE WHEN variable LIKE '%mbs%' OR variable LIKE '%agency%' THEN pctl50 END) as mbs_median,
            AVG(CASE WHEN variable LIKE '%reserve%' THEN pctl50 END) as reserves_median
        FROM nyfed_survey_bs
        WHERE horizon_date > survey_date
          AND horizon_date <= survey_date + INTERVAL 365 DAY
        GROUP BY strftime(survey_date, '%Y-%m')
        ORDER BY period
    """).fetchdf()

    con.close()

    if ft.empty or survey.empty:
        print("WARNING: F_t or survey data is empty. Run collectors and classifier first.")
        return None

    merged = pd.merge(ft, survey, on="period", how="inner")
    merged = merged.sort_values("period").reset_index(drop=True)

    return merged


def differenced_crosscorrelation(x, y, max_lag=6):
    """Compute cross-correlation of first differences with HAC SEs."""
    dx = np.diff(x)
    dy = np.diff(y)

    n = min(len(dx), len(dy))
    dx = dx[:n]
    dy = dy[:n]

    results = []
    for lag in range(-max_lag, max_lag + 1):
        if lag > 0:
            x_shifted = dx[:-lag] if lag < len(dx) else np.array([])
            y_aligned = dy[lag:] if lag < len(dy) else np.array([])
        elif lag < 0:
            x_shifted = dx[-lag:]
            y_aligned = dy[:lag]
        else:
            x_shifted = dx
            y_aligned = dy

        n_eff = min(len(x_shifted), len(y_aligned))
        if n_eff < 5:
            continue

        x_s = x_shifted[:n_eff]
        y_a = y_aligned[:n_eff]

        rho, p_naive = stats.spearmanr(x_s, y_a)

        p_hac = p_naive
        t_hac = None
        if HAS_STATSMODELS and n_eff > 10:
            try:
                X = add_constant(x_s)
                model = OLS(y_a, X).fit(cov_type='HAC', cov_kwds={'maxlags': min(4, n_eff // 4)})
                t_hac = model.tvalues[1]
                p_hac = model.pvalues[1]
            except Exception:
                pass

        results.append({
            "lag": lag,
            "n": n_eff,
            "rho": rho,
            "p_naive": p_naive,
            "t_hac": t_hac,
            "p_hac": p_hac,
        })

    return pd.DataFrame(results)


def regime_analysis(merged, regime_col="regime"):
    """Test lead-lag within each regime."""
    if regime_col not in merged.columns:
        return None

    results = []
    for regime in merged[regime_col].unique():
        sub = merged[merged[regime_col] == regime].copy()
        if len(sub) < 8:
            continue

        target_cols = [c for c in ["total_assets_median", "tsy_median"] if c in sub.columns and sub[c].notna().sum() > 5]

        for target in target_cols:
            valid = sub[["f_t", target]].dropna()
            if len(valid) < 5:
                continue

            cc = differenced_crosscorrelation(valid["f_t"].values, valid[target].values, max_lag=3)
            if cc.empty:
                continue

            lag1 = cc[cc["lag"] == 1]
            if not lag1.empty:
                row = lag1.iloc[0]
                results.append({
                    "regime": regime,
                    "target": target,
                    "lag1_rho": row["rho"],
                    "lag1_p_hac": row["p_hac"],
                    "lag1_n": row["n"],
                })

    return pd.DataFrame(results) if results else None


def assign_regime(period):
    """Assign a regime label to a YYYY-MM period."""
    for regime, (start, end) in FED_REGIMES.items():
        if start[:7] <= period <= end[:7]:
            return regime
    return "other"


def main():
    merged = load_data()
    if merged is None or len(merged) < 5:
        print("Insufficient data for lead-lag analysis.")
        print("Run the full pipeline first: collect -> classify -> aggregate")
        return

    merged["regime"] = merged["period"].apply(assign_regime)

    print(f"\n{'='*70}")
    print("LEAD-LAG ANALYSIS: dF_t vs Survey Pace (First Differences, HAC SEs)")
    print(f"{'='*70}")
    print(f"\nN observations: {len(merged)}")
    print(f"Period: {merged['period'].min()} to {merged['period'].max()}")

    print("\n--- SIGN CONVENTIONS ---")
    print("F_t > 0: net increase signal from news (more 'increase' than 'decrease' headlines)")
    print("F_t < 0: net decrease signal from news")
    print("dF_t > 0: shift toward expansion signal")
    print("Survey: median expected SOMA size ($bn). Positive change = expected growth.")
    print("Pre-specified test: positive rho at lag +1 (news leads survey by 1 period)")

    target_cols = [c for c in ["total_assets_median", "tsy_median", "mbs_median"]
                   if c in merged.columns and merged[c].notna().sum() > 5]

    if not target_cols:
        print("\nNo valid survey targets with enough data.")
        return

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    for target in target_cols:
        valid = merged[["period", "f_t", target, "regime"]].dropna()
        if len(valid) < 5:
            continue

        print(f"\n--- dF_t vs d{target} ---")
        cc = differenced_crosscorrelation(valid["f_t"].values, valid[target].values)

        print(f"{'Lag':>5} {'N':>5} {'rho':>8} {'p(naive)':>10} {'t(HAC)':>8} {'p(HAC)':>10}")
        print("-" * 50)
        for _, row in cc.iterrows():
            t_str = f"{row['t_hac']:.2f}" if row['t_hac'] is not None else "N/A"
            print(f"{row['lag']:>5} {row['n']:>5} {row['rho']:>8.3f} {row['p_naive']:>10.4f} {t_str:>8} {row['p_hac']:>10.4f}")

        lag1 = cc[cc["lag"] == 1]
        if not lag1.empty:
            r = lag1.iloc[0]
            print(f"\n  Pre-specified test (lag=+1): rho={r['rho']:.3f}, p_HAC={r['p_hac']:.4f}, N={r['n']}")
            if r['rho'] > 0 and r['p_hac'] < 0.05:
                print("  RESULT: Positive and significant — news leads survey revisions.")
            elif r['rho'] > 0 and r['p_hac'] < 0.10:
                print("  RESULT: Positive but only marginally significant (p < 0.10).")
            elif r['rho'] > 0:
                print("  RESULT: Positive sign but NOT significant after HAC correction.")
            else:
                print("  RESULT: Wrong sign — no evidence that news leads survey.")

    regime_results = regime_analysis(merged)
    if regime_results is not None and not regime_results.empty:
        print(f"\n--- REGIME-SPECIFIC LAG-1 RESULTS ---")
        print(regime_results.to_string(index=False))
    else:
        print("\n  Insufficient within-regime data for regime-specific analysis.")


if __name__ == "__main__":
    main()
