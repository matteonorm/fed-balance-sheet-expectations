"""
Build monthly F_t (relevance-gated LLM balance statistic) from classified articles.

F_t = (n_increase - n_decrease) / (n_increase + n_decrease)
Only uses articles classified as increase/decrease/uncertain (excludes not_relevant).

Usage:
    python aggregate.py
"""

import duckdb
from config import DUCKDB_PATH


def build_ft(db_path=DUCKDB_PATH):
    con = duckdb.connect(db_path)

    con.execute("DELETE FROM llm_expectations")

    con.execute("""
        INSERT INTO llm_expectations
        SELECT
            strftime(g.seendate, '%Y-%m') as period,
            SUM(CASE WHEN c.direction = 'increase' THEN 1 ELSE 0 END) as n_increase,
            SUM(CASE WHEN c.direction = 'decrease' THEN 1 ELSE 0 END) as n_decrease,
            SUM(CASE WHEN c.direction = 'uncertain' THEN 1 ELSE 0 END) as n_uncertain,
            SUM(CASE WHEN c.direction = 'not_relevant' THEN 1 ELSE 0 END) as n_not_relevant,
            COUNT(*) as n_total,
            SUM(CASE WHEN c.direction IN ('increase', 'decrease', 'uncertain') THEN 1 ELSE 0 END) as n_relevant,
            CASE
                WHEN SUM(CASE WHEN c.direction IN ('increase', 'decrease') THEN 1 ELSE 0 END) > 0
                THEN (
                    SUM(CASE WHEN c.direction = 'increase' THEN 1.0 ELSE 0 END)
                    - SUM(CASE WHEN c.direction = 'decrease' THEN 1.0 ELSE 0 END)
                ) / SUM(CASE WHEN c.direction IN ('increase', 'decrease') THEN 1.0 ELSE 0 END)
                ELSE NULL
            END as f_statistic
        FROM gdelt_articles g
        JOIN llm_classifications c ON g.url = c.url
        GROUP BY strftime(g.seendate, '%Y-%m')
        ORDER BY period
    """)

    stats = con.execute("""
        SELECT COUNT(*) as n_months,
               SUM(n_total) as total_articles,
               SUM(n_relevant) as relevant_articles,
               AVG(f_statistic) as avg_f,
               MIN(period) as first,
               MAX(period) as last
        FROM llm_expectations
    """).fetchone()

    print(f"Built F_t for {stats[0]} months ({stats[4]} to {stats[5]})")
    print(f"Total articles: {stats[1]}, Relevant: {stats[2]} ({stats[2]/stats[1]*100:.1f}%)")
    print(f"Mean F_t: {stats[3]:.3f}" if stats[3] else "Mean F_t: N/A")

    sample = con.execute("""
        SELECT period, n_increase, n_decrease, n_uncertain, n_not_relevant,
               n_relevant, n_total, ROUND(f_statistic, 3) as f_t
        FROM llm_expectations
        ORDER BY period DESC
        LIMIT 10
    """).fetchdf()
    print(f"\nLatest months:\n{sample.to_string(index=False)}")

    con.close()


if __name__ == "__main__":
    build_ft()
