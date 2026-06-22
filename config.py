import os

PROJECT_DIR = os.path.dirname(__file__)
DUCKDB_PATH = os.path.join(PROJECT_DIR, "fed_bs.duckdb")
DATA_DIR = os.path.join(PROJECT_DIR, "data")
OUTPUT_DIR = os.path.join(PROJECT_DIR, "output")

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = "claude-haiku-4-5"
ENSEMBLE_K = 5

DATE_START = "2008-01-01"
DATE_END = "2026-06-21"

NYFED_SURVEY_BASE = "https://www.newyorkfed.org/medialibrary/media/markets/survey"
NYFED_INDEX_PAGES = [
    "https://www.newyorkfed.org/markets/primarydealer_survey_questions",
    "https://www.newyorkfed.org/markets/survey_market_participants",
    "https://www.newyorkfed.org/markets/market-intelligence/survey-of-market-expectations",
]

FRED_SERIES = {
    "WALCL": "Total assets",
    "TREAST": "Treasury holdings",
    "WSHOMCB": "MBS holdings",
    "WRESBAL": "Reserve balances",
}

GDELT_KEYWORDS = [
    '"Federal Reserve balance sheet"',
    '"Fed balance sheet"',
    '"quantitative tightening" OR "quantitative easing" Fed',
    '"SOMA portfolio"',
    '"Fed tapering" OR "taper tantrum"',
    '"Fed asset purchases"',
    '"Treasury runoff" OR "MBS runoff" Fed',
]
GDELT_API_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
GDELT_DELAY_SECONDS = 15.0
GDELT_MAX_RETRIES = 3

FED_REGIMES = {
    "pre_taper": ("2011-01-01", "2013-05-21"),
    "taper_tantrum": ("2013-05-22", "2014-10-28"),
    "reinvestment": ("2014-10-29", "2017-09-19"),
    "qt1_runoff": ("2017-09-20", "2019-07-30"),
    "qe_covid": ("2019-07-31", "2022-05-31"),
    "qt2": ("2022-06-01", "2026-06-21"),
}
