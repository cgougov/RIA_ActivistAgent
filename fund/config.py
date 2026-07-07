"""Paths and model configuration. The only file that reads the environment."""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = Path(os.getenv("FUND_DB_PATH", str(DATA_DIR / "db" / "fund.db")))
PDF_DIR = DATA_DIR / "pdfs"
PAGE_IMAGE_DIR = DATA_DIR / "page_images"
SEED_DIR = DATA_DIR / "seed"
FACTSHEET_DIR = DATA_DIR / "factsheets"

EXTRACT_MODEL = os.getenv("OPENAI_MODEL", "gpt-5")
ANALYSIS_MODEL = os.getenv("OPENAI_ANALYSIS_MODEL", "gpt-5.2")
REASONING_EFFORT = os.getenv("OPENAI_REASONING_EFFORT", "medium")


def require_api_key():
    if not os.getenv("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY is not set (add it to .env).")
