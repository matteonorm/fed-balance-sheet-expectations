"""
Extract balance-sheet expectations from NY Fed SPD survey result PDFs (2011-2023)
using pdfplumber for text extraction and Claude Haiku for structured parsing.

Usage:
    python extract_pdf_surveys.py              # extract all PDFs
    python extract_pdf_surveys.py test         # test on 3 sample PDFs
    python extract_pdf_surveys.py report       # print coverage summary
"""

import os
import sys
import json
import re
import time
import duckdb
import pdfplumber
import anthropic
from config import DUCKDB_PATH, DATA_DIR, ANTHROPIC_API_KEY

PDF_DIR = os.path.join(DATA_DIR, "nyfed_pdfs")
CACHE_DIR = os.path.join(DATA_DIR, "pdf_extractions")
MODEL = "claude-haiku-4-5"

EXTRACTION_PROMPT = """You are extracting balance-sheet/SOMA-related survey data from a NY Fed Survey of Primary Dealers results document.

From the text below, extract ALL balance-sheet related data. This includes questions about:
- SOMA portfolio size (levels or changes)
- Asset purchase pace (Treasuries, Agency MBS)
- Balance sheet normalization timing
- Reinvestment policy expectations
- Reserve levels
- Any probability distributions for SOMA/portfolio size

Return a JSON object with this structure:
{
  "survey_date": "YYYY-MM-DD",  // the survey distribution date
  "survey_month": "YYYY-MM",    // year-month for matching
  "n_respondents": N,           // number of primary dealers
  "has_bs_questions": true/false,
  "bs_data": [
    {
      "question_type": "soma_change_path" | "purchase_pace" | "soma_size_dist" | "runoff_timing" | "runoff_size" | "reinvestment_timing" | "reserve_path" | "other_bs",
      "description": "brief description of what this question asks",
      "variable": "treasury" | "agency_mbs" | "total_soma" | "reserves" | "combined",
      "unit": "billions_usd" | "percent" | "date",
      "format": "percentiles" | "probability_distribution" | "single_value",
      "horizons": [
        {
          "period": "2022 Q3" or "Jul 2022" or "Year-end 2023" etc.,
          "pctl25": value_or_null,
          "pctl50": value_or_null,
          "pctl75": value_or_null,
          "average": value_or_null,
          "n_responses": N_or_null
        }
      ],
      "buckets": [
        {
          "range": "<=3000" or "3001-3500" etc.,
          "average_probability": 0.22
        }
      ]
    }
  ]
}

Rules:
- Only extract balance-sheet/SOMA/portfolio/purchase-related questions. Skip interest rate, GDP, inflation, recession questions.
- Preserve exact numbers as they appear (billions USD typically).
- For purchase pace questions (QE era), positive numbers mean purchases.
- For net change questions (QT era), negative numbers mean portfolio shrinking.
- If no balance-sheet questions exist in this survey, set has_bs_questions to false and bs_data to [].
- Include ALL horizons/periods shown in the tables.

Survey text:
"""


def extract_text_from_pdf(filepath):
    """Extract all text from a PDF using pdfplumber."""
    text_parts = []
    with pdfplumber.open(filepath) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                text_parts.append(text)
    return "\n\n".join(text_parts)


def _try_parse_json(text):
    """Try to parse JSON, with fallback repairs for common LLM mistakes."""
    json_match = re.search(r'\{[\s\S]*\}', text)
    if not json_match:
        return None
    raw = json_match.group()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    repaired = re.sub(r',\s*}', '}', raw)
    repaired = re.sub(r',\s*]', ']', repaired)
    repaired = re.sub(r'(\d)"(\d)', r'\1, \2', repaired)
    repaired = re.sub(r'}\s*{', '}, {', repaired)
    try:
        return json.loads(repaired)
    except json.JSONDecodeError:
        return None


def extract_bs_data_with_llm(text, filepath):
    """Use Claude Haiku to extract structured BS data from survey text."""
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    prompt = EXTRACTION_PROMPT + text[:25000]

    for attempt in range(2):
        response = client.messages.create(
            model=MODEL,
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )

        content = response.content[0].text
        result = _try_parse_json(content)
        if result:
            return result
        if attempt == 0:
            prompt = EXTRACTION_PROMPT + "IMPORTANT: Return ONLY valid JSON, no trailing commas.\n\n" + text[:25000]
            time.sleep(1)

    return None


def process_all_pdfs(test_mode=False):
    """Process all PDFs and extract balance-sheet data."""
    os.makedirs(CACHE_DIR, exist_ok=True)

    pdf_files = sorted([f for f in os.listdir(PDF_DIR) if f.endswith('.pdf')
                        and not f.startswith('.')])

    # Filter to only SPD results (not the sample ones we downloaded for inspection)
    pdf_files = [f for f in pdf_files if 'result' in f.lower() or 'Result' in f]

    if test_mode:
        test_samples = [
            f for f in pdf_files
            if any(k in f for k in ['2013_June', '2017_sep', '2022_jun'])
        ]
        if not test_samples:
            test_samples = pdf_files[:3]
        pdf_files = test_samples
        print(f"TEST MODE: processing {len(pdf_files)} sample PDFs")

    results = []
    for i, fname in enumerate(pdf_files):
        cache_file = os.path.join(CACHE_DIR, fname.replace('.pdf', '.json'))

        if os.path.exists(cache_file):
            with open(cache_file) as f:
                data = json.load(f)
            results.append(data)
            continue

        filepath = os.path.join(PDF_DIR, fname)
        print(f"  [{i+1}/{len(pdf_files)}] Processing {fname}...")

        try:
            text = extract_text_from_pdf(filepath)
            if not text or len(text) < 100:
                print(f"    WARNING: no text extracted from {fname}")
                continue

            data = extract_bs_data_with_llm(text, filepath)
            if data:
                data['source_file'] = fname
                with open(cache_file, 'w') as f:
                    json.dump(data, f, indent=2, default=str)
                results.append(data)
                bs_count = len(data.get('bs_data', []))
                print(f"    -> {bs_count} BS questions found, date={data.get('survey_date')}")
            else:
                print(f"    WARNING: failed to parse LLM response for {fname}")

            time.sleep(0.5)

        except Exception as e:
            print(f"    ERROR processing {fname}: {e}")

    return results


def ingest_pdf_extractions(results, db_path=DUCKDB_PATH):
    """Ingest PDF extraction results into DuckDB tables."""
    con = duckdb.connect(db_path)

    path_rows = []
    runoff_rows = []

    for result in results:
        if not result.get('has_bs_questions'):
            continue

        survey_date = result.get('survey_date', '')
        source_file = result.get('source_file', '')

        for item in result.get('bs_data', []):
            q_type = item.get('question_type', '')
            variable = item.get('variable', '')

            if q_type in ('soma_change_path', 'purchase_pace', 'reserve_path'):
                for h in item.get('horizons', []):
                    period = h.get('period', '')
                    horizon_date = _period_to_date(period)
                    if not horizon_date:
                        continue

                    var_name = f"{variable}_{q_type}"

                    path_rows.append({
                        'survey_date': survey_date,
                        'panel_type': 'SPD',
                        'variable': var_name,
                        'horizon_date': horizon_date,
                        'pctl25': h.get('pctl25'),
                        'pctl50': h.get('pctl50') or h.get('average'),
                        'pctl75': h.get('pctl75'),
                        'respondent_count': h.get('n_responses'),
                        'source_file': source_file,
                    })

            elif q_type in ('runoff_timing', 'reinvestment_timing'):
                for h in item.get('horizons', []):
                    runoff_rows.append({
                        'survey_date': survey_date,
                        'panel_type': 'SPD',
                        'variable': f"{variable}_{q_type}",
                        'pctl25': str(h.get('pctl25', '')),
                        'pctl50': str(h.get('pctl50', '') or h.get('average', '')),
                        'pctl75': str(h.get('pctl75', '')),
                        'respondent_count': h.get('n_responses'),
                        'source_file': source_file,
                    })

            elif q_type in ('runoff_size', 'soma_size_dist'):
                for h in item.get('horizons', []):
                    runoff_rows.append({
                        'survey_date': survey_date,
                        'panel_type': 'SPD',
                        'variable': f"{variable}_{q_type}",
                        'pctl25': str(h.get('pctl25', '')),
                        'pctl50': str(h.get('pctl50', '')),
                        'pctl75': str(h.get('pctl75', '')),
                        'respondent_count': h.get('n_responses'),
                        'source_file': source_file,
                    })

    for r in path_rows:
        try:
            con.execute("""
                INSERT OR REPLACE INTO nyfed_survey_bs
                (survey_date, panel_type, variable, horizon_date, pctl25, pctl50, pctl75, respondent_count, source_file)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, [r['survey_date'], r['panel_type'], r['variable'], r['horizon_date'],
                  r['pctl25'], r['pctl50'], r['pctl75'], r['respondent_count'], r['source_file']])
        except Exception:
            pass

    for r in runoff_rows:
        try:
            con.execute("""
                INSERT OR REPLACE INTO nyfed_survey_runoff
                (survey_date, panel_type, variable, pctl25, pctl50, pctl75, respondent_count, source_file)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, [r['survey_date'], r['panel_type'], r['variable'],
                  r['pctl25'], r['pctl50'], r['pctl75'], r['respondent_count'], r['source_file']])
        except Exception:
            pass

    con.close()
    print(f"  Ingested {len(path_rows)} path rows, {len(runoff_rows)} runoff rows from PDF extractions")


def _period_to_date(period_str):
    """Convert period strings like '2022 Q3', 'Jul 2022', 'Year-end 2023' to dates."""
    if not period_str:
        return None

    s = str(period_str).strip()

    q_match = re.match(r'(\d{4})\s*Q(\d)', s)
    if q_match:
        year, q = int(q_match.group(1)), int(q_match.group(2))
        month = {1: 2, 2: 5, 3: 8, 4: 11}[q]
        return f"{year}-{month:02d}-15"

    q_match2 = re.match(r'Q(\d)\s+(\d{4})', s)
    if q_match2:
        q, year = int(q_match2.group(1)), int(q_match2.group(2))
        month = {1: 2, 2: 5, 3: 8, 4: 11}[q]
        return f"{year}-{month:02d}-15"

    h_match = re.match(r'(\d{4})\s*H(\d)', s)
    if h_match:
        year, h = int(h_match.group(1)), int(h_match.group(2))
        month = 3 if h == 1 else 9
        return f"{year}-{month:02d}-15"

    h_match2 = re.match(r'H(\d)\s+(\d{4})', s)
    if h_match2:
        h, year = int(h_match2.group(1)), int(h_match2.group(2))
        month = 3 if h == 1 else 9
        return f"{year}-{month:02d}-15"

    ye_match = re.match(r'(?:Year-?end|YE)\s+(\d{4})', s, re.I)
    if ye_match:
        return f"{ye_match.group(1)}-12-31"

    cy_match = re.match(r'(\d{4})\s*CY', s)
    if cy_match:
        return f"{cy_match.group(1)}-06-30"

    months = {'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
              'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12,
              'january': 1, 'february': 2, 'march': 3, 'april': 4,
              'june': 6, 'july': 7, 'august': 8, 'september': 9,
              'october': 10, 'november': 11, 'december': 12}

    for mname, mnum in months.items():
        m_match = re.search(rf'{mname}[.\s]*(\d{{4}})', s, re.I)
        if m_match:
            return f"{m_match.group(1)}-{mnum:02d}-15"
        m_match2 = re.search(rf'(\d{{4}})[.\s]*{mname}', s, re.I)
        if m_match2:
            return f"{m_match2.group(1)}-{mnum:02d}-15"

    # Date patterns like "June 18-19:" with year inferred from context
    for mname, mnum in months.items():
        m_match = re.search(rf'{mname}\s+\d{{1,2}}', s, re.I)
        if m_match:
            return None  # Can't determine year, skip

    year_match = re.match(r'^(\d{4})$', s.strip())
    if year_match:
        return f"{year_match.group(1)}-06-30"

    return None


def print_pdf_coverage(db_path=DUCKDB_PATH):
    """Print coverage from PDF extractions."""
    cache_files = [f for f in os.listdir(CACHE_DIR) if f.endswith('.json')]

    print(f"\n{'='*80}")
    print("PDF EXTRACTION COVERAGE REPORT")
    print(f"{'='*80}")

    has_bs = 0
    no_bs = 0
    by_year = {}

    for fname in sorted(cache_files):
        with open(os.path.join(CACHE_DIR, fname)) as f:
            data = json.load(f)

        date = data.get('survey_date', 'unknown')
        year = date[:4] if date else 'unknown'
        has = data.get('has_bs_questions', False)

        if year not in by_year:
            by_year[year] = {'total': 0, 'with_bs': 0}
        by_year[year]['total'] += 1
        if has:
            by_year[year]['with_bs'] += 1
            has_bs += 1
        else:
            no_bs += 1

    print(f"\nTotal PDFs processed: {len(cache_files)}")
    print(f"With BS questions: {has_bs}")
    print(f"Without BS questions: {no_bs}")

    print(f"\n{'Year':<8} {'Total':>6} {'With BS':>8}")
    print("-" * 25)
    for year in sorted(by_year.keys()):
        d = by_year[year]
        print(f"{year:<8} {d['total']:>6} {d['with_bs']:>8}")


def main():
    if len(sys.argv) > 1 and sys.argv[1] == 'report':
        print_pdf_coverage()
        return

    test_mode = len(sys.argv) > 1 and sys.argv[1] == 'test'

    if not ANTHROPIC_API_KEY:
        print("ERROR: ANTHROPIC_API_KEY not set. Run: export ANTHROPIC_API_KEY=...")
        sys.exit(1)

    print("Extracting balance-sheet data from SPD survey PDFs...")
    results = process_all_pdfs(test_mode=test_mode)

    if not test_mode:
        print("\nIngesting to DuckDB...")
        ingest_pdf_extractions(results)

    print(f"\nDone. {len(results)} PDFs processed.")

    if os.path.exists(CACHE_DIR):
        print_pdf_coverage()


if __name__ == "__main__":
    main()
