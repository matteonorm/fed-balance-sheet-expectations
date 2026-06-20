"""Visualization module following Kieran Healy's data visualization principles:
- Minimal, clean design; no chartjunk
- Muted, purposeful color palette
- Direct labeling over legends where possible
- Informative titles that state the finding
- Consistent typography and whitespace
"""

import os

import duckdb
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.ticker as mticker
import pandas as pd
import numpy as np

from config import DUCKDB_PATH, OUTPUT_DIR

COLORS = {
    "increase": "#2a9d8f",
    "decrease": "#e76f51",
    "uncertain": "#adb5bd",
    "ecb_assets": "#264653",
    "sma": "#e9c46a",
    "f_stat": "#264653",
    "light_grid": "#e9ecef",
    "annotation": "#6c757d",
}

plt.rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": ["Helvetica", "Arial", "DejaVu Sans"],
    "font.size": 11,
    "axes.titlesize": 13,
    "axes.titleweight": "bold",
    "axes.labelsize": 11,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.grid": False,
    "xtick.labelsize": 9,
    "ytick.labelsize": 9,
    "figure.facecolor": "white",
    "axes.facecolor": "white",
    "savefig.facecolor": "white",
    "savefig.bbox": "tight",
    "savefig.dpi": 200,
})


def load_all_data(db_path=DUCKDB_PATH):
    con = duckdb.connect(db_path, read_only=True)

    llm_df = con.execute("""
        SELECT period, n_total, n_increase, n_decrease, n_uncertain, f_statistic
        FROM llm_expectations ORDER BY period
    """).fetchdf()

    sma_df = con.execute("""
        SELECT vintage, vintage_date, forecast_date, measure,
               app_holdings_eur, pepp_holdings_eur, total_holdings_eur
        FROM sma_expectations WHERE measure = 'MEDIAN'
        ORDER BY vintage_date, forecast_date
    """).fetchdf()

    ecb_df = con.execute("""
        SELECT observation_date, total_assets_eur
        FROM ecb_balance_sheet ORDER BY observation_date
    """).fetchdf()

    article_counts = con.execute("""
        SELECT strftime(seendate, '%Y-%m') AS month, COUNT(*) AS n
        FROM gdelt_articles GROUP BY month ORDER BY month
    """).fetchdf()

    classification_detail = con.execute("""
        SELECT c.direction, c.confidence,
               strftime(g.seendate, '%Y-%m') AS month
        FROM llm_classifications c
        JOIN gdelt_articles g ON c.url = g.url
    """).fetchdf()

    con.close()
    return llm_df, sma_df, ecb_df, article_counts, classification_detail


def _add_light_grid(ax, axis="y"):
    ax.set_axisbelow(True)
    if axis in ("y", "both"):
        ax.yaxis.grid(True, color=COLORS["light_grid"], linewidth=0.5)
    if axis in ("x", "both"):
        ax.xaxis.grid(True, color=COLORS["light_grid"], linewidth=0.5)


def plot_main_comparison(llm_df, sma_df, ecb_df, output_dir):
    """Figure 1: The main result — LLM sentiment vs actual ECB balance sheet."""
    if llm_df.empty:
        return

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8), height_ratios=[2, 1],
                                    sharex=True)
    fig.subplots_adjust(hspace=0.08)

    llm = llm_df.copy()
    llm["date"] = pd.to_datetime(llm["period"] + "-15")
    llm_valid = llm.dropna(subset=["f_statistic"])

    # Top panel: ECB total assets + SMA
    if not ecb_df.empty:
        ecb = ecb_df.copy()
        ecb["observation_date"] = pd.to_datetime(ecb["observation_date"])
        ax1.plot(ecb["observation_date"], ecb["total_assets_eur"] / 1e6,
                 color=COLORS["ecb_assets"], linewidth=2, label="Eurosystem total assets")

    if not sma_df.empty:
        sma = sma_df.copy()
        sma["vintage_date"] = pd.to_datetime(sma["vintage_date"])
        sma["forecast_date"] = pd.to_datetime(sma["forecast_date"])
        first_fc = sma.groupby("vintage").first().reset_index()
        ax1.scatter(first_fc["vintage_date"], first_fc["total_holdings_eur"] / 1e3,
                    color=COLORS["sma"], edgecolors="#b8860b", s=35, zorder=5,
                    linewidths=0.5, label="SMA median (next quarter)")

    ax1.set_ylabel("EUR trillion")
    _add_light_grid(ax1)
    ax1.legend(loc="upper left", frameon=False, fontsize=9)
    ax1.set_title("ECB balance sheet: actual path and survey expectations", loc="left")

    # Bottom panel: LLM F_t
    ax2.bar(llm_valid["date"], llm_valid["f_statistic"],
            width=25,
            color=[COLORS["increase"] if v > 0 else COLORS["decrease"]
                   for v in llm_valid["f_statistic"]],
            alpha=0.8)
    ax2.axhline(0, color="#dee2e6", linewidth=0.8)
    ax2.set_ylabel("F_t")
    ax2.set_ylim(-1.15, 1.15)
    _add_light_grid(ax2)

    ax2.text(0.01, 0.92, "LLM balance statistic (Bybee method)",
             transform=ax2.transAxes, fontsize=9, color=COLORS["annotation"],
             verticalalignment="top")
    ax2.text(0.01, 0.78, "F_t = (n_increase − n_decrease) / (n_increase + n_decrease)",
             transform=ax2.transAxes, fontsize=8, color=COLORS["annotation"],
             verticalalignment="top", style="italic")

    # Key events
    events = [
        ("2019-09-12", "QE\nrestarted", 0.85),
        ("2020-03-18", "PEPP\nlaunched", 0.60),
        ("2022-07-01", "Net purchases\nended", -0.60),
        ("2023-03-01", "QT\nstarted", -0.85),
    ]
    for date_str, label, y_pos in events:
        d = pd.Timestamp(date_str)
        ax2.annotate(label, xy=(d, y_pos),
                     fontsize=7, color=COLORS["annotation"],
                     ha="center", va="center",
                     bbox=dict(boxstyle="round,pad=0.2", facecolor="white",
                               edgecolor=COLORS["light_grid"], linewidth=0.5))

    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    ax2.xaxis.set_major_locator(mdates.YearLocator())

    path = os.path.join(output_dir, "fig1_main_comparison.png")
    fig.savefig(path)
    plt.close()
    print(f"Saved: {path}")


def plot_classification_shares(llm_df, output_dir):
    """Figure 2: Classification shares over time as area chart."""
    if llm_df.empty:
        return

    df = llm_df.copy()
    df["date"] = pd.to_datetime(df["period"] + "-15")
    df = df[df["n_total"] >= 3].copy()

    if df.empty:
        return

    df["share_increase"] = df["n_increase"] / df["n_total"]
    df["share_decrease"] = df["n_decrease"] / df["n_total"]
    df["share_uncertain"] = df["n_uncertain"] / df["n_total"]

    fig, ax = plt.subplots(figsize=(12, 4.5))

    ax.stackplot(df["date"],
                 df["share_increase"], df["share_uncertain"], df["share_decrease"],
                 colors=[COLORS["increase"], COLORS["uncertain"], COLORS["decrease"]],
                 alpha=0.85,
                 labels=["Increase", "Uncertain", "Decrease"])

    ax.set_ylim(0, 1)
    ax.yaxis.set_major_formatter(mticker.PercentFormatter(1.0))
    ax.set_ylabel("Share of articles")
    ax.set_title("How news coverage shifted from expansion to contraction", loc="left")
    _add_light_grid(ax)

    ax.legend(loc="upper right", frameon=False, fontsize=9, ncol=3)

    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    ax.xaxis.set_major_locator(mdates.YearLocator())

    path = os.path.join(output_dir, "fig2_classification_shares.png")
    fig.savefig(path)
    plt.close()
    print(f"Saved: {path}")


def plot_article_coverage(article_counts, output_dir):
    """Figure 3: Article coverage — where the data is thin."""
    if article_counts.empty:
        return

    df = article_counts.copy()
    df["date"] = pd.to_datetime(df["month"] + "-15")

    fig, ax = plt.subplots(figsize=(12, 3.5))

    ax.bar(df["date"], df["n"], width=25, color=COLORS["ecb_assets"], alpha=0.6)
    ax.axhline(10, color=COLORS["decrease"], linewidth=0.8, linestyle="--", alpha=0.6)
    ax.text(df["date"].max() + pd.Timedelta(days=15), 10, "min. 10",
            fontsize=8, color=COLORS["decrease"], va="center")

    ax.set_ylabel("Articles per month")
    ax.set_title("News article coverage by month", loc="left")
    _add_light_grid(ax)

    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    ax.xaxis.set_major_locator(mdates.YearLocator())

    path = os.path.join(output_dir, "fig3_article_coverage.png")
    fig.savefig(path)
    plt.close()
    print(f"Saved: {path}")


def plot_f_vs_sma_scatter(llm_df, sma_df, output_dir):
    """Figure 4: Scatter of LLM F_t vs SMA expected change."""
    if llm_df.empty or sma_df.empty:
        return

    from compare import compute_sma_change

    llm = llm_df.copy()
    llm = llm.dropna(subset=["f_statistic"])

    sma_changes = compute_sma_change(sma_df)
    if sma_changes.empty:
        return

    merged = pd.merge(llm, sma_changes,
                      left_on="period", right_on="vintage_month",
                      how="inner")

    if len(merged) < 3:
        return

    fig, ax = plt.subplots(figsize=(6, 6))

    ax.scatter(merged["f_statistic"], merged["expected_change_pct"],
               color=COLORS["ecb_assets"], s=50, alpha=0.7, edgecolors="white",
               linewidths=0.5)

    from scipy import stats as scipy_stats
    r, p = scipy_stats.pearsonr(merged["f_statistic"], merged["expected_change_pct"])

    z = np.polyfit(merged["f_statistic"], merged["expected_change_pct"], 1)
    x_line = np.linspace(merged["f_statistic"].min(), merged["f_statistic"].max(), 100)
    ax.plot(x_line, np.polyval(z, x_line), color=COLORS["decrease"],
            linewidth=1.5, linestyle="--", alpha=0.6)

    ax.set_xlabel("LLM balance statistic (F_t)")
    ax.set_ylabel("SMA expected change (%)")
    ax.set_title(f"LLM sentiment vs survey expectations (r = {r:.2f}, p = {p:.2f})",
                 loc="left")
    _add_light_grid(ax, axis="both")

    ax.axhline(0, color="#dee2e6", linewidth=0.5)
    ax.axvline(0, color="#dee2e6", linewidth=0.5)

    path = os.path.join(output_dir, "fig4_scatter_f_vs_sma.png")
    fig.savefig(path)
    plt.close()
    print(f"Saved: {path}")


def plot_confidence_distribution(classification_detail, output_dir):
    """Figure 5: Distribution of classification confidence by direction."""
    if classification_detail.empty:
        return

    fig, ax = plt.subplots(figsize=(8, 4.5))

    for direction, color in [("increase", COLORS["increase"]),
                             ("decrease", COLORS["decrease"]),
                             ("uncertain", COLORS["uncertain"])]:
        subset = classification_detail[classification_detail["direction"] == direction]
        if subset.empty:
            continue
        ax.hist(subset["confidence"], bins=20, range=(0, 1),
                alpha=0.6, color=color, label=direction.capitalize(),
                edgecolor="white", linewidth=0.5)

    ax.set_xlabel("Classification confidence")
    ax.set_ylabel("Number of articles")
    ax.set_title("LLM confidence varies by classification direction", loc="left")
    ax.legend(frameon=False, fontsize=9)
    _add_light_grid(ax)

    path = os.path.join(output_dir, "fig5_confidence_distribution.png")
    fig.savefig(path)
    plt.close()
    print(f"Saved: {path}")


def plot_ecb_bs_with_regimes(ecb_df, output_dir):
    """Figure 6: ECB balance sheet with policy regime annotations."""
    if ecb_df.empty:
        return

    ecb = ecb_df.copy()
    ecb["observation_date"] = pd.to_datetime(ecb["observation_date"])
    ecb["total_tn"] = ecb["total_assets_eur"] / 1e6

    fig, ax = plt.subplots(figsize=(12, 5))

    ax.plot(ecb["observation_date"], ecb["total_tn"],
            color=COLORS["ecb_assets"], linewidth=2)

    regimes = [
        ("2019-11-01", "2022-06-30", "QE restart + PEPP", COLORS["increase"]),
        ("2023-03-01", "2026-06-15", "Quantitative tightening", COLORS["decrease"]),
    ]
    for start, end, label, color in regimes:
        s, e = pd.Timestamp(start), pd.Timestamp(end)
        ax.axvspan(s, e, alpha=0.08, color=color)
        mid = s + (e - s) / 2
        y_pos = ax.get_ylim()[1] * 0.95
        ax.text(mid, y_pos, label, ha="center", va="top",
                fontsize=8, color=COLORS["annotation"], style="italic")

    ax.set_ylabel("EUR trillion")
    ax.set_title("Eurosystem total assets and monetary policy regimes", loc="left")
    _add_light_grid(ax)

    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    ax.xaxis.set_major_locator(mdates.YearLocator())

    path = os.path.join(output_dir, "fig6_ecb_bs_regimes.png")
    fig.savefig(path)
    plt.close()
    print(f"Saved: {path}")


def visualize(db_path=DUCKDB_PATH):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    llm_df, sma_df, ecb_df, article_counts, classification_detail = load_all_data(db_path)

    print(f"Data: {len(llm_df)} LLM months, {len(sma_df)} SMA rows, "
          f"{len(ecb_df)} ECB obs, {len(article_counts)} article months")

    plot_main_comparison(llm_df, sma_df, ecb_df, OUTPUT_DIR)
    plot_classification_shares(llm_df, OUTPUT_DIR)
    plot_article_coverage(article_counts, OUTPUT_DIR)
    plot_f_vs_sma_scatter(llm_df, sma_df, OUTPUT_DIR)
    plot_confidence_distribution(classification_detail, OUTPUT_DIR)
    plot_ecb_bs_with_regimes(ecb_df, OUTPUT_DIR)

    print(f"\nAll figures saved to {OUTPUT_DIR}/")


if __name__ == "__main__":
    visualize()
