import os
from dataclasses import dataclass

@dataclass(frozen=True)
class BotConfig:
    telegram_bot_token: str
    sarvam_api_key: str
    sarvam_base_url: str
    vision_language: str
    vision_output_format: str
    chat_model: str
    poll_interval_seconds: float
    poll_timeout_seconds: int
    chat_context_char_limit: int
    session_ttl_minutes: int

    @staticmethod
    def _require_env(name: str) -> str:
        value = os.getenv(name, "").strip()
        if not value or "YOUR_" in value or "<" in value:
            raise ValueError(f"Set a valid value for {name} in .env")
        return value

    @classmethod
    def from_env(cls) -> "BotConfig":
        return cls(
            telegram_bot_token=cls._require_env("TELEGRAM_BOT_TOKEN"),
            sarvam_api_key=cls._require_env("SARVAM_API_KEY"),
            sarvam_base_url=os.getenv("SARVAM_BASE_URL", "https://api.sarvam.ai").rstrip("/"),
            vision_language=os.getenv("SARVAM_VISION_LANGUAGE", "en-IN"),
            vision_output_format=os.getenv("SARVAM_VISION_OUTPUT_FORMAT", "md"),
            chat_model=os.getenv("SARVAM_CHAT_MODEL", "sarvam-m"),
            poll_interval_seconds=float(os.getenv("SARVAM_POLL_INTERVAL_SECONDS", "2.5")),
            poll_timeout_seconds=int(os.getenv("SARVAM_POLL_TIMEOUT_SECONDS", "480")),
            chat_context_char_limit=int(os.getenv("CHAT_CONTEXT_CHAR_LIMIT", "6000")),
            session_ttl_minutes=int(os.getenv("SESSION_TTL_MINUTES", "60")),
        )
