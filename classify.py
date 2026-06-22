"""
LLM-based headline classifier for Fed balance-sheet news.

4-class relevance gate: increase / decrease / uncertain / not_relevant
k=5 ensemble: majority vote, confidence = vote share.

Usage:
    python classify.py validate    # classify 100-item sample for hand-labelling
    python classify.py run         # classify all articles (after validation)
    python classify.py report      # print classification stats
"""

import os
import sys
import json
import time
import duckdb
import anthropic
from collections import Counter
from config import DUCKDB_PATH, ANTHROPIC_API_KEY, CLAUDE_MODEL, ENSEMBLE_K

SYSTEM_PROMPT = """You are classifying news headlines about the US Federal Reserve's balance sheet.

Classify each headline into EXACTLY ONE of these four categories:

1. **increase** — The headline suggests the Fed's balance sheet / SOMA portfolio / asset holdings are GROWING or will grow. This includes: new asset purchases, QE expansion, slower runoff/tapering than expected, reinvestment of maturing securities, emergency lending facilities that expand the balance sheet.

2. **decrease** — The headline suggests the Fed's balance sheet / SOMA portfolio / asset holdings are SHRINKING or will shrink. This includes: quantitative tightening (QT), balance sheet runoff, allowing securities to mature without reinvestment, tapering of purchases, selling assets, faster-than-expected normalization.

3. **uncertain** — The headline is genuinely about the Fed's balance sheet direction but the direction is ambiguous, debated, or conditional. This includes: "Fed debates pace of QT", "markets unsure about balance sheet plans", "FOMC split on reinvestment policy".

4. **not_relevant** — The headline is NOT about the Fed's balance sheet, SOMA holdings, or asset purchase/runoff policy. This includes: interest rate decisions (without BS mention), inflation data, employment data, GDP, other central banks (ECB, BOJ, BOE), stock market commentary, fiscal policy, bank regulation, individual Fed officials' general comments, crypto, consumer spending. MOST headlines will be not_relevant.

Few-shot examples:

INCREASE examples:
- "Fed to buy $120 billion per month in Treasuries and MBS" → increase
- "Federal Reserve expands balance sheet to support markets" → increase
- "Fed slows pace of balance sheet reduction, easing QT" → increase

DECREASE examples:
- "Fed to accelerate balance sheet runoff starting June" → decrease
- "Federal Reserve shrinks holdings by $95 billion per month" → decrease
- "SOMA portfolio hits lowest level since 2020 as QT continues" → decrease

UNCERTAIN examples:
- "Fed officials debate when to end balance sheet reduction" → uncertain
- "Markets split on whether Fed will slow QT pace" → uncertain
- "FOMC minutes show disagreement on reinvestment policy" → uncertain

NOT_RELEVANT examples:
- "Fed raises interest rates by 25 basis points" → not_relevant
- "Powell says inflation remains too high" → not_relevant
- "US jobs report beats expectations" → not_relevant
- "ECB balance sheet shrinks as TLTRO matures" → not_relevant
- "Federal Reserve stress test results released" → not_relevant

Respond with ONLY the classification label (increase, decrease, uncertain, or not_relevant) and nothing else."""


def classify_single(client, title, model=CLAUDE_MODEL):
    """Classify a single headline. Returns the label."""
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=20,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": f"Classify: {title}"}],
        )
        label = resp.content[0].text.strip().lower()
        valid = {"increase", "decrease", "uncertain", "not_relevant"}
        if label in valid:
            return label
        for v in valid:
            if v in label:
                return v
        return "not_relevant"
    except Exception as e:
        print(f"    API error: {e}")
        return None


def classify_ensemble(client, title, k=ENSEMBLE_K, model=CLAUDE_MODEL):
    """Classify with k-sample ensemble. Returns (majority_label, confidence, vote_dist)."""
    votes = []
    for _ in range(k):
        label = classify_single(client, title, model)
        if label:
            votes.append(label)
        time.sleep(0.1)

    if not votes:
        return None, 0.0, {}

    counter = Counter(votes)
    majority = counter.most_common(1)[0][0]
    confidence = counter[majority] / len(votes)
    vote_dist = dict(counter)

    return majority, confidence, vote_dist


def validate_sample(n=100, db_path=DUCKDB_PATH):
    """Classify a random sample for hand-labelling."""
    if not ANTHROPIC_API_KEY:
        print("ERROR: Set ANTHROPIC_API_KEY")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    con = duckdb.connect(db_path)

    articles = con.execute(f"""
        SELECT url, title, seendate
        FROM gdelt_articles
        ORDER BY RANDOM()
        LIMIT {n}
    """).fetchall()

    print(f"Classifying {len(articles)} articles with k={ENSEMBLE_K} ensemble...")

    results = []
    for i, (url, title, seendate) in enumerate(articles):
        label, conf, votes = classify_ensemble(client, title)
        results.append({
            "url": url,
            "title": title,
            "seendate": str(seendate),
            "llm_label": label,
            "ensemble_confidence": conf,
            "vote_distribution": json.dumps(votes),
            "hand_label": "",
        })

        if (i + 1) % 10 == 0:
            labels = Counter(r["llm_label"] for r in results)
            print(f"  [{i+1}/{len(articles)}] {dict(labels)}")

    import pandas as pd
    df = pd.DataFrame(results)
    out_path = os.path.join(os.path.dirname(db_path), "output", "validation_sample.csv")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    df.to_csv(out_path, index=False)

    labels = Counter(r["llm_label"] for r in results)
    print(f"\nValidation sample saved to {out_path}")
    print(f"Distribution: {dict(labels)}")
    print(f"Mean confidence: {sum(r['ensemble_confidence'] for r in results)/len(results):.2f}")
    print(f"\nFill the 'hand_label' column and run: python classify.py accuracy")

    con.close()


def classify_all(db_path=DUCKDB_PATH):
    """Classify all unclassified articles."""
    if not ANTHROPIC_API_KEY:
        print("ERROR: Set ANTHROPIC_API_KEY")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    con = duckdb.connect(db_path)

    already = set()
    try:
        done = con.execute("SELECT url FROM llm_classifications").fetchall()
        already = {r[0] for r in done}
    except Exception:
        pass

    articles = con.execute("""
        SELECT url, title FROM gdelt_articles ORDER BY seendate
    """).fetchall()

    to_classify = [(url, title) for url, title in articles if url not in already]
    print(f"Total articles: {len(articles)}, already classified: {len(already)}, to classify: {len(to_classify)}")

    for i, (url, title) in enumerate(to_classify):
        label, conf, votes = classify_ensemble(client, title)
        if label is None:
            continue

        try:
            con.execute("""
                INSERT OR REPLACE INTO llm_classifications
                (url, direction, ensemble_confidence, self_confidence, vote_distribution,
                 model_id, ensemble_k)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, [url, label, conf, None, json.dumps(votes), CLAUDE_MODEL, ENSEMBLE_K])
        except Exception as e:
            print(f"  DB error: {e}")

        if (i + 1) % 50 == 0:
            total = con.execute("SELECT COUNT(*) FROM llm_classifications").fetchone()[0]
            print(f"  [{i+1}/{len(to_classify)}] {total} total classified")

    total = con.execute("SELECT COUNT(*) FROM llm_classifications").fetchone()[0]
    con.close()
    print(f"\nDone. {total} total classifications.")


def print_report(db_path=DUCKDB_PATH):
    """Print classification statistics."""
    con = duckdb.connect(db_path, read_only=True)

    total = con.execute("SELECT COUNT(*) FROM llm_classifications").fetchone()[0]
    if total == 0:
        print("No classifications yet.")
        return

    print(f"\n{'='*60}")
    print("CLASSIFICATION REPORT")
    print(f"{'='*60}")
    print(f"Total classified: {total}")

    dist = con.execute("""
        SELECT direction, COUNT(*) as n,
               ROUND(AVG(ensemble_confidence), 3) as avg_conf
        FROM llm_classifications
        GROUP BY direction
        ORDER BY n DESC
    """).fetchdf()
    print(f"\n{dist.to_string(index=False)}")

    relevant = con.execute("""
        SELECT COUNT(*) FROM llm_classifications
        WHERE direction != 'not_relevant'
    """).fetchone()[0]
    print(f"\nRelevant articles: {relevant} ({relevant/total*100:.1f}%)")

    con.close()


def main():
    if len(sys.argv) < 2:
        print("Usage: python classify.py [validate|run|report|accuracy]")
        return

    cmd = sys.argv[1]
    if cmd == "validate":
        validate_sample()
    elif cmd == "run":
        classify_all()
    elif cmd == "report":
        print_report()
    elif cmd == "accuracy":
        check_accuracy()
    else:
        print(f"Unknown command: {cmd}")


def check_accuracy():
    """Compare hand labels with LLM labels from validation sample."""
    import pandas as pd
    csv_path = os.path.join(os.path.dirname(DUCKDB_PATH), "output", "validation_sample.csv")
    if not os.path.exists(csv_path):
        print("No validation sample found. Run: python classify.py validate")
        return

    df = pd.read_csv(csv_path)
    labeled = df[df["hand_label"].notna() & (df["hand_label"] != "")]
    if len(labeled) == 0:
        print("No hand labels filled in yet.")
        return

    correct = (labeled["llm_label"] == labeled["hand_label"]).sum()
    total = len(labeled)
    print(f"Accuracy: {correct}/{total} = {correct/total*100:.1f}%")

    from collections import Counter
    confusion = Counter(zip(labeled["llm_label"], labeled["hand_label"]))
    print("\nConfusion (predicted, actual):")
    for (pred, actual), count in sorted(confusion.items()):
        print(f"  {pred:15s} -> {actual:15s}: {count}")


if __name__ == "__main__":
    main()
