"""
config.py
=========
Centralized configuration for MeetingMind AI.

Responsibilities
-----------------
- Load environment variables from `.env` (never hardcode secrets).
- Configure application-wide logging.
- Provide lazily-initialized, singleton clients for external services
  (Groq, Supabase) so the rest of the codebase never touches os.environ
  directly.

Design notes
------------
Using a single `Settings` object (loaded once at import time) keeps
configuration in one place and makes it trivial to unit test other
modules by monkeypatching `config.settings`.
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"

# ----------------------------------------------------------------------
# Load environment variables as early as possible.
# ----------------------------------------------------------------------
load_dotenv(dotenv_path=ENV_FILE, override=False)

UPLOAD_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "outputs"
TEMP_DIR = BASE_DIR / "temp"

for _dir in (UPLOAD_DIR, OUTPUT_DIR, TEMP_DIR):
    _dir.mkdir(parents=True, exist_ok=True)

SUPPORTED_MEDIA_EXTENSIONS = {".mp4", ".mov", ".avi", ".wav", ".mp3", ".m4a"}


# ----------------------------------------------------------------------
# Logging
# ----------------------------------------------------------------------
def _configure_logging() -> logging.Logger:
    """Configure a root logger that writes to stdout with a consistent
    format across every module in the project."""
    logger = logging.getLogger("meetingmind")
    if logger.handlers:
        # Avoid duplicate handlers if this module is imported multiple times
        # (e.g. by Streamlit's script re-run model).
        return logger

    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return logger


logger = _configure_logging()


# ----------------------------------------------------------------------
# Settings
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class Settings:
    """Typed, immutable view over required environment variables.

    Missing *required* variables are collected and reported together so
    a developer sees every problem in one pass instead of fixing one
    `KeyError` at a time.
    """

    groq_api_key: Optional[str] = field(default=None)
    supabase_url: Optional[str] = field(default=None)
    supabase_key: Optional[str] = field(default=None)
    google_client_id: Optional[str] = field(default=None)
    google_client_secret: Optional[str] = field(default=None)
    google_project_id: Optional[str] = field(default=None)
    google_redirect_uri: str = field(default="http://localhost:8501")
    google_auth_uri: str = field(default="https://accounts.google.com/o/oauth2/auth")
    google_token_uri: str = field(default="https://oauth2.googleapis.com/token")
    google_auth_provider_x509_cert_url: str = field(default="https://www.googleapis.com/oauth2/v1/certs")
    google_userinfo_uri: str = field(default="https://www.googleapis.com/oauth2/v2/userinfo")

    whisper_model_size: str = field(default="base")
    whisper_device: str = field(default="cpu")
    whisper_compute_type: str = field(default="int8")

    groq_model: str = field(default="llama-3.3-70b-versatile")

    @classmethod
    def load(cls) -> "Settings":
        return cls(
            groq_api_key=os.getenv("GROQ_API_KEY"),
            supabase_url=os.getenv("SUPABASE_URL"),
            supabase_key=os.getenv("SUPABASE_KEY"),
            google_client_id=os.getenv("GOOGLE_CLIENT_ID"),
            google_client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
            google_project_id=os.getenv("GOOGLE_PROJECT_ID"),
            google_redirect_uri=os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8501"),
            google_auth_uri=os.getenv("GOOGLE_AUTH_URI", "https://accounts.google.com/o/oauth2/auth"),
            google_token_uri=os.getenv("GOOGLE_TOKEN_URI", "https://oauth2.googleapis.com/token"),
            google_auth_provider_x509_cert_url=os.getenv("GOOGLE_AUTH_PROVIDER_X509_CERT_URL", "https://www.googleapis.com/oauth2/v1/certs"),
            google_userinfo_uri=os.getenv("GOOGLE_USERINFO_URI", "https://www.googleapis.com/oauth2/v2/userinfo"),
            whisper_model_size=os.getenv("WHISPER_MODEL_SIZE", "base"),
            whisper_device=os.getenv("WHISPER_DEVICE", "cpu"),
            whisper_compute_type=os.getenv("WHISPER_COMPUTE_TYPE", "int8"),
            groq_model=os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
        )

    def missing_ai_pipeline_vars(self) -> list[str]:
        """Variables required for the core transcription -> analysis
        pipeline. Auth/Calendar are optional (guest mode exists)."""
        required = {"GROQ_API_KEY": self.groq_api_key}
        return [name for name, value in required.items() if not value]

    def missing_persistence_vars(self) -> list[str]:
        required = {
            "SUPABASE_URL": self.supabase_url,
            "SUPABASE_KEY": self.supabase_key,
        }
        return [name for name, value in required.items() if not value]

    def google_oauth_configured(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret)


settings = Settings.load()

if settings.missing_ai_pipeline_vars():
    logger.warning(
        "Missing required environment variables for the AI pipeline: %s. "
        "Set these in your .env file before analyzing meetings.",
        ", ".join(settings.missing_ai_pipeline_vars()),
    )

if settings.missing_persistence_vars():
    logger.warning(
        "Missing Supabase configuration: %s. Meeting history/persistence "
        "will be unavailable until these are set.",
        ", ".join(settings.missing_persistence_vars()),
    )

if not settings.google_oauth_configured():
    logger.info(
        "Google OAuth credentials not configured. Users will be limited to Guest Mode."
    )


# ----------------------------------------------------------------------
# Lazy singleton clients
# ----------------------------------------------------------------------
_groq_client = None
_supabase_client = None


def get_groq_client():
    """Return a lazily-initialized Groq SDK client.

    Raises
    ------
    RuntimeError
        If GROQ_API_KEY is not configured.
    """
    global _groq_client
    if _groq_client is None:
        if not settings.groq_api_key:
            raise RuntimeError(
                "GROQ_API_KEY is not set. Add it to your .env file."
            )
        from groq import Groq

        _groq_client = Groq(api_key=settings.groq_api_key)
        logger.info("Groq client initialized (model=%s).", settings.groq_model)
    return _groq_client


def get_supabase_client():
    """Return a lazily-initialized Supabase client.

    Raises
    ------
    RuntimeError
        If SUPABASE_URL / SUPABASE_KEY are not configured.
    """
    global _supabase_client
    if _supabase_client is None:
        missing = settings.missing_persistence_vars()
        if missing:
            raise RuntimeError(
                f"Missing Supabase configuration: {', '.join(missing)}."
            )
        from supabase import create_client

        _supabase_client = create_client(
            settings.supabase_url, settings.supabase_key
        )
        logger.info("Supabase client initialized.")
    return _supabase_client
