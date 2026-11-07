"""Runtime configuration, read from environment variables / .env.

Settings are re-read on every call to get_settings() so tests (and the CLI) can
switch providers by changing os.environ without restarting the process.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# Sensible defaults per provider. Override with LLM_MODEL / QC_LLM_MODEL.
DEFAULT_MODELS = {
    "gemini": "gemini-2.5-flash",
    "groq": "llama-3.3-70b-versatile",
    "openai": "gpt-4.1-mini",
    "anthropic": "claude-sonnet-5-5",
    "openrouter": "meta-llama/llama-3.3-70b-instruct",
    "ollama": "llama3.1",
    "mock": "mock",
}

KEY_ENV = {
    "gemini": "GEMINI_API_KEY",
    "groq": "GROQ_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}


def _env(name: str, default: str = "") -> str:
    v = os.getenv(name)
    return v.strip() if v and v.strip() else default


@dataclass
class Settings:
    llm_provider: str
    llm_model: str
    qc_provider: str
    qc_model: str
    api_keys: dict = field(default_factory=dict)
    ollama_base_url: str = "http://localhost:11434"
    tavily_key: str = ""
    youtube_key: str = ""
    research_mode: str = "live"          # live | mock
    db_path: Path = ROOT / "data" / "lessonforge.db"
    lesson_minutes: int = 45
    reading_wpm: int = 180               # self-paced technical reading speed
    speaking_wpm: int = 130              # instructor delivery speed
    http_timeout: float = 20.0
    llm_timeout: float = 180.0
    check_links: bool = True
    fallbacks: list = field(default_factory=list)   # [(provider, model), ...] tried in order if the primary fails

    @property
    def fallback(self):
        return self.fallbacks[0] if self.fallbacks else None

    @property
    def chunked(self) -> bool:
        """Generate outline first, then sections in small batches (robust on free tiers)."""
        return _env("GENERATION_MODE", "chunked").lower() != "single"

    @property
    def compact(self) -> bool:
        """Small-context / low-TPM providers get a smaller evidence pack."""
        return self.llm_provider == "groq" or _env("COMPACT_PROMPTS", "").lower() in ("1", "true", "yes")

    def key_for(self, provider: str) -> str:
        return self.api_keys.get(provider, "")


def get_settings() -> Settings:
    provider = _env("LLM_PROVIDER", "gemini").lower()
    model = _env("LLM_MODEL", DEFAULT_MODELS.get(provider, ""))
    qc_provider = _env("QC_LLM_PROVIDER", provider).lower()
    qc_model = _env("QC_LLM_MODEL", model if qc_provider == provider else DEFAULT_MODELS.get(qc_provider, ""))
    db = _env("DB_PATH", "")
    fallbacks = []
    for item in _env("FALLBACK_LLM", "").split(","):
        if item.strip():
            fp, _, fm = item.strip().partition(":")
            fp = fp.strip().lower()
            fallbacks.append((fp, fm.strip() or DEFAULT_MODELS.get(fp, "")))
    return Settings(
        fallbacks=fallbacks,
        llm_provider=provider,
        llm_model=model,
        qc_provider=qc_provider,
        qc_model=qc_model,
        api_keys={p: _env(k) for p, k in KEY_ENV.items()},
        ollama_base_url=_env("OLLAMA_BASE_URL", "http://localhost:11434"),
        tavily_key=_env("TAVILY_API_KEY"),
        youtube_key=_env("YOUTUBE_API_KEY"),
        research_mode=_env("RESEARCH_MODE", "mock" if provider == "mock" else "live").lower(),
        db_path=Path(db) if db else ROOT / "data" / "lessonforge.db",
        lesson_minutes=int(_env("LESSON_MINUTES", "45")),
        reading_wpm=int(_env("READING_WPM", "180")),
        speaking_wpm=int(_env("SPEAKING_WPM", "130")),
        check_links=_env("CHECK_LINKS", "true").lower() in ("1", "true", "yes"),
    )
