"""
End-to-end pipeline for the Fed balance-sheet expectations project.

Usage:
    python run_pipeline.py              # run full pipeline
    python run_pipeline.py collect      # collect only (surveys, FRED, news)
    python run_pipeline.py classify     # classify + aggregate
    python run_pipeline.py analyze      # analysis + figures
"""

import sys
import os


def run_collect():
    print("=" * 60)
    print("STEP 1: Collect NY Fed survey data (Excel)")
    print("=" * 60)
    from collect_nyfed_survey import main as collect_survey
    collect_survey()

    print("\n" + "=" * 60)
    print("STEP 2: Collect FRED balance-sheet actuals")
    print("=" * 60)
    from collect_fred import main as collect_fred
    collect_fred()

    print("\n" + "=" * 60)
    print("STEP 3: Collect news articles (GDELT)")
    print("=" * 60)
    from collect_gdelt import main as collect_gdelt
    collect_gdelt()

    print("\n" + "=" * 60)
    print("STEP 3b: Collect news articles (Google News)")
    print("=" * 60)
    from collect_gnews import main as collect_gnews
    collect_gnews()


def run_classify():
    print("\n" + "=" * 60)
    print("STEP 4: Classify articles (4-class, k=5 ensemble)")
    print("=" * 60)
    sys.argv = ["classify.py", "run"]
    from classify import main as classify_main
    classify_main()

    print("\n" + "=" * 60)
    print("STEP 5: Aggregate to monthly F_t")
    print("=" * 60)
    from aggregate import build_ft
    build_ft()


def run_analyze():
    print("\n" + "=" * 60)
    print("STEP 6: Lead-lag analysis")
    print("=" * 60)
    from leadlag_analysis import main as leadlag_main
    leadlag_main()

    print("\n" + "=" * 60)
    print("STEP 7: Generate figures")
    print("=" * 60)
    from visualize import main as viz_main
    viz_main()


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"

    if cmd == "collect":
        run_collect()
    elif cmd == "classify":
        run_classify()
    elif cmd == "analyze":
        run_analyze()
    elif cmd == "all":
        run_collect()
        run_classify()
        run_analyze()
    else:
        print(f"Unknown command: {cmd}")
        print("Usage: python run_pipeline.py [collect|classify|analyze|all]")

    print("\n" + "=" * 60)
    print("PIPELINE COMPLETE")
    print("=" * 60)
    print(f"\nFiles in output/:")
    output_dir = os.path.join(os.path.dirname(__file__), "output")
    if os.path.exists(output_dir):
        for f in sorted(os.listdir(output_dir)):
            path = os.path.join(output_dir, f)
            sz = os.path.getsize(path)
            print(f"  {f}: {sz:,} bytes")


if __name__ == "__main__":
    main()
