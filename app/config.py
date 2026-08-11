import os
import logging
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Root directory of the project
ROOT_DIR = Path(__file__).resolve().parent.parent

# Storage configuration
DATA_DIR = ROOT_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
CHROMA_DB_PATH = os.getenv("CHROMA_DB_PATH", str(DATA_DIR / "chroma_db"))
SQLITE_DB_PATH = os.getenv("SQLITE_DB_PATH", str(DATA_DIR / "metadata.db"))
MOCK_EMBEDDINGS = os.getenv("MOCK_EMBEDDINGS", "false").lower() == "true"
RETRIEVAL_THRESHOLD = float(os.getenv("RETRIEVAL_THRESHOLD", "0.8"))

# JWT & CORS configurations
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "fallback_unsafe_key_for_development_change_it")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
ALLOWED_ORIGINS_RAW = os.getenv("ALLOWED_ORIGINS", "")
ALLOWED_ORIGINS = [origin.strip() for origin in ALLOWED_ORIGINS_RAW.split(",") if origin.strip()]

PORT = int(os.getenv("PORT", "8000"))
HOST = os.getenv("HOST", "127.0.0.1")

# Configure logging
LOG_FILE = DATA_DIR / "ingestion.log"
AUDIT_LOG_FILE = DATA_DIR / "audit.log"

# Main application logger
logger = logging.getLogger("rag_ingestion")
logger.setLevel(logging.INFO)
if logger.hasHandlers():
    logger.handlers.clear()

c_handler = logging.StreamHandler()
f_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
c_handler.setLevel(logging.INFO)
f_handler.setLevel(logging.INFO)

log_format = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
c_handler.setFormatter(log_format)
f_handler.setFormatter(log_format)

logger.addHandler(c_handler)
logger.addHandler(f_handler)
logger.info("Logging configured. Data directory initialized at: %s", DATA_DIR)

# Audit logger
audit_logger = logging.getLogger("audit_log")
audit_logger.setLevel(logging.INFO)
if audit_logger.hasHandlers():
    audit_logger.handlers.clear()

audit_f_handler = logging.FileHandler(AUDIT_LOG_FILE, encoding="utf-8")
audit_f_handler.setLevel(logging.INFO)
audit_format = logging.Formatter('%(asctime)s - %(message)s')
audit_f_handler.setFormatter(audit_format)
audit_logger.addHandler(audit_f_handler)

