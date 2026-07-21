"""Safe application configuration defaults."""

import os
import secrets
from pathlib import Path


def _positive_int(name: str, default: int) -> int:
    """Read a positive integer without allowing unsafe configuration values."""

    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


def _log_level() -> str:
    candidate = os.getenv("APP_LOG_LEVEL", "INFO").upper()
    allowed = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}
    return candidate if candidate in allowed else "INFO"


def _ollama_model() -> str:
    candidate = os.getenv("APP_OLLAMA_MODEL", "llama3.2:latest").strip()
    return candidate if candidate and len(candidate) <= 128 else "llama3.2:latest"


class DefaultConfig:
    """Local-only defaults shared by application instances."""

    DEBUG = False
    TESTING = False

    HOST = "127.0.0.1"
    PORT = 5000
    OLLAMA_HOST = "http://127.0.0.1:11434"
    OLLAMA_MODEL = _ollama_model()
    OLLAMA_TIMEOUT_SECONDS = _positive_int("APP_OLLAMA_TIMEOUT_SECONDS", 120)
    TRUSTED_HOSTS = ["127.0.0.1", "localhost"]

    MAX_UPLOAD_BYTES = _positive_int("APP_MAX_UPLOAD_BYTES", 10 * 1024 * 1024)
    # Multipart requests are slightly larger than the file they contain. The
    # file itself is independently bounded by MAX_UPLOAD_BYTES while this caps
    # unreasonable request overhead before Flask parses it.
    MAX_CONTENT_LENGTH = MAX_UPLOAD_BYTES + 1024 * 1024
    MAX_CSV_ROWS = _positive_int("APP_MAX_CSV_ROWS", 5_000)
    MAX_CSV_COLUMNS = _positive_int("APP_MAX_CSV_COLUMNS", 200)
    CSV_PREVIEW_ROWS = _positive_int("APP_CSV_PREVIEW_ROWS", 5)

    SECRET_KEY = os.getenv("APP_SECRET_KEY") or secrets.token_hex(32)

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = False  # Local HTTP only; production requires HTTPS and True.

    LOG_LEVEL = _log_level()
    INSTANCE_PATH = Path(__file__).resolve().parents[2] / "instance"
