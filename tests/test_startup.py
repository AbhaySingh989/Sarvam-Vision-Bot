import importlib

from bot.config import BotConfig
from bot.main import build_application


def test_main_import_smoke() -> None:
    importlib.import_module("main")


def test_build_application_registers_runtime_dependencies() -> None:
    config = BotConfig(
        telegram_bot_token="123456:TESTTOKEN",
        sarvam_api_key="test-api-key",
        sarvam_base_url="https://api.sarvam.ai",
        vision_language="en-IN",
        vision_output_format="md",
        chat_model="sarvam-m",
        poll_interval_seconds=2.5,
        poll_timeout_seconds=480,
        chat_context_char_limit=6000,
        session_ttl_minutes=60,
    )

    application = build_application(config)

    assert application.bot_data["config"] == config
    assert "vision_client" in application.bot_data
    assert "chat_client" in application.bot_data
    assert application.bot_data["sessions"] == {}
