import os
import logging
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

from bot.config import BotConfig
from bot.clients.sarvam_vision import SarvamVisionClient
from bot.clients.sarvam_chat import SarvamChatClient
from bot.router import (
    start_handler, cancel_handler, module_selector_handler, document_handler,
    compare_level_handler, entity_mode_handler, text_handler, legacy_feature_handler
)
from bot.utils import SensitiveDataFilter

def configure_logging() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )

    root_logger = logging.getLogger()
    redaction_filter = SensitiveDataFilter()
    for handler in root_logger.handlers:
        handler.addFilter(redaction_filter)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)

async def post_init_handler(application: Application) -> None:
    from telegram import BotCommand
    commands = [
        BotCommand("start", "Start bot and select module"),
        BotCommand("cancel", "Cancel current workflow"),
    ]
    await application.bot.set_my_commands(commands)

async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logging.exception("Unhandled update error", exc_info=context.error)

def build_application(config: BotConfig) -> Application:
    application = (
        Application.builder()
        .token(config.telegram_bot_token)
        .post_init(post_init_handler)
        .build()
    )
    application.bot_data["config"] = config
    application.bot_data["vision_client"] = SarvamVisionClient(
        api_key=config.sarvam_api_key,
        base_url=config.sarvam_base_url,
    )
    application.bot_data["chat_client"] = SarvamChatClient(
        api_key=config.sarvam_api_key,
        base_url=config.sarvam_base_url,
    )
    application.bot_data["sessions"] = {}

    application.add_handler(CommandHandler("start", start_handler))
    application.add_handler(CommandHandler("cancel", cancel_handler))
    application.add_handler(CallbackQueryHandler(module_selector_handler, pattern=r"^module:"))
    application.add_handler(CallbackQueryHandler(compare_level_handler, pattern=r"^compare_level:"))
    application.add_handler(CallbackQueryHandler(entity_mode_handler, pattern=r"^entity_mode:"))
    application.add_handler(CallbackQueryHandler(legacy_feature_handler, pattern=r"^legacy_feature:"))
    application.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO, document_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    application.add_error_handler(global_error_handler)
    return application

def main() -> None:
    load_dotenv()
    configure_logging()

    config = BotConfig.from_env()
    app = build_application(config)
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
