import logging
import re
from typing import Optional
from bot.state import ChatSession
from datetime import datetime, timezone

def now_utc_timestamp() -> float:
    return datetime.now(timezone.utc).timestamp()

class SensitiveDataFilter(logging.Filter):
    _telegram_bot_token_pattern = re.compile(r"bot\d+:[A-Za-z0-9_-]+", re.IGNORECASE)
    _query_secret_pattern = re.compile(
        r"([?&](?:sig|signature|token|access_token|api_key|apikey|key|api-subscription-key)=)([^&\s]+)",
        re.IGNORECASE,
    )
    _header_secret_pattern = re.compile(
        r"(api-subscription-key['\"]?\s*[:=]\s*['\"]?)([^'\",\s]+)",
        re.IGNORECASE,
    )
    _bearer_pattern = re.compile(r"(bearer\s+)([A-Za-z0-9._-]+)", re.IGNORECASE)

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        sanitized = sanitize_log_text(message)
        if sanitized != message:
            record.msg = sanitized
            record.args = ()
        return True

def sanitize_log_text(message: str) -> str:
    text = message or ""
    text = SensitiveDataFilter._telegram_bot_token_pattern.sub("bot<redacted>", text)
    text = SensitiveDataFilter._query_secret_pattern.sub(r"\1<redacted>", text)
    text = SensitiveDataFilter._header_secret_pattern.sub(r"\1<redacted>", text)
    text = SensitiveDataFilter._bearer_pattern.sub(r"\1<redacted>", text)
    return text

def get_session_for_chat(
    sessions: dict[int, ChatSession],
    chat_id: int,
    ttl_minutes: int,
) -> Optional[ChatSession]:
    session = sessions.get(chat_id)
    if not session:
        return None
    age_seconds = now_utc_timestamp() - session.updated_at
    if age_seconds > ttl_minutes * 60:
        del sessions[chat_id]
        return None
    return session
