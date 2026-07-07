from pathlib import Path
import os

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

if load_dotenv:
    load_dotenv()


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_DB_PATH = DATA_DIR / "db" / "activist_funds.db"
DB_PATH = Path(os.getenv("ACTIVIST_DB_PATH", str(DEFAULT_DB_PATH)))
BACKUP_DIR = DB_PATH.parent / "backups"
PDF_ROOT = DATA_DIR / "pdfs"
PAGE_IMAGE_ROOT = DATA_DIR / "page_images"
SEED_DIR = DATA_DIR / "seed"
EXPORT_DIR = PROJECT_ROOT / "exports"

DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "gpt-5")
DEFAULT_ANALYSIS_MODEL = os.getenv("OPENAI_ANALYSIS_MODEL", "gpt-5.2")
DEFAULT_REASONING_EFFORT = os.getenv("OPENAI_REASONING_EFFORT", "medium")
PROMPT_VERSION = "fund_characteristics_v1"
