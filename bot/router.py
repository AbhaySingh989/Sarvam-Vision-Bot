import logging
import mimetypes
from pathlib import Path
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

from bot.config import BotConfig
from bot.state import ChatSession, AppState, WorkflowModule
from bot.clients.sarvam_vision import SarvamVisionClient, IMAGE_EXTENSIONS
from bot.clients.sarvam_chat import SarvamChatClient
from bot.engines.ocr_parser import parse_ocr_to_artifact
from bot.workflows.comparison import run_comparison
from bot.workflows.entity import run_extraction
from bot.workflows.legacy import execute_feature, FEATURE_ASK
from bot.export.excel import create_comparison_workbook, create_entity_workbook
from bot.utils import get_session_for_chat, now_utc_timestamp

SUPPORTED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg"}
MAX_SOURCE_FILE_BYTES = 200 * 1024 * 1024

async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return

    chat_id = message.chat.id
    sessions: dict[int, ChatSession] = context.application.bot_data["sessions"]
    sessions[chat_id] = ChatSession(
        chat_id=chat_id,
        updated_at=now_utc_timestamp(),
        state=AppState.MODULE_SELECTION
    )

    keyboard = [
        [InlineKeyboardButton("📝 Complete Text Extraction", callback_data="module:extraction")],
        [InlineKeyboardButton("⚖️ Document Comparison", callback_data="module:comparison")],
        [InlineKeyboardButton("🔍 Entity Extraction", callback_data="module:entity")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await message.reply_text(
        "👋 Welcome! Please select a module to begin:\n\n"
        "You can use /cancel at any time to return to this menu.",
        reply_markup=reply_markup
    )

async def cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await start_handler(update, context)

async def module_selector_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.message:
        return
    await query.answer()

    chat_id = query.message.chat.id
    sessions: dict[int, ChatSession] = context.application.bot_data["sessions"]
    session = sessions.get(chat_id)

    if not session:
        session = ChatSession(chat_id=chat_id, updated_at=now_utc_timestamp())
        sessions[chat_id] = session

    module_data = query.data.split(":")[1]

    if module_data == "extraction":
        session.current_module = WorkflowModule.EXTRACTION
        session.state = AppState.EXTRACTION_AWAITING_DOC
        await query.message.reply_text("📝 **Complete Text Extraction**\nPlease upload a PDF or image.")
    elif module_data == "comparison":
        session.current_module = WorkflowModule.COMPARISON
        session.state = AppState.COMPARISON_AWAITING_DOC_A
        await query.message.reply_text("⚖️ **Document Comparison**\nPlease upload the FIRST document (Doc A).")
    elif module_data == "entity":
        session.current_module = WorkflowModule.ENTITY
        session.state = AppState.ENTITY_AWAITING_DOC
        await query.message.reply_text("🔍 **Entity Extraction**\nPlease upload a PDF or image.")

async def document_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message or not update.effective_chat:
        return

    chat_id = update.effective_chat.id
    sessions: dict[int, ChatSession] = context.application.bot_data["sessions"]
    session = sessions.get(chat_id)

    if not session or session.state == AppState.MODULE_SELECTION:
        await message.reply_text("Please select a module first by typing /start.")
        return

    # Standard file download logic
    is_photo = bool(message.photo and not message.document)
    if is_photo:
        telegram_file_id = message.photo[-1].file_id
        file_name = f"photo_{telegram_file_id}.jpg"
        mime_type = "image/jpeg"
    elif message.document:
        telegram_file_id = message.document.file_id
        file_name = message.document.file_name or f"document_{telegram_file_id}"
        mime_type = message.document.mime_type or mimetypes.guess_type(file_name)[0] or ""
    else:
        await message.reply_text("Please upload a PDF or image file.")
        return

    extension = Path(file_name).suffix.lower()
    if extension and extension not in SUPPORTED_EXTENSIONS and not is_photo:
        await message.reply_text("Unsupported file. Please upload PDF, PNG, JPG, or JPEG.")
        return

    status_message = await message.reply_text("📥 Downloading file from Telegram...")
    try:
        telegram_file = await context.bot.get_file(telegram_file_id)
        downloaded = await telegram_file.download_as_bytearray()
        file_bytes = bytes(downloaded)

        if len(file_bytes) > MAX_SOURCE_FILE_BYTES:
            raise RuntimeError("File is too large. Supports up to 200 MB.")

        config: BotConfig = context.application.bot_data["config"]
        vision_client: SarvamVisionClient = context.application.bot_data["vision_client"]

        # OCR Pipeline
        await status_message.edit_text("🔍 Analyzing document with Sarvam Vision...")
        job_id, extracted_text = await vision_client.extract_text(
            file_name=file_name,
            file_bytes=file_bytes,
            content_type=mime_type,
            language=config.vision_language,
            output_format=config.vision_output_format,
            poll_interval_seconds=config.poll_interval_seconds,
            poll_timeout_seconds=config.poll_timeout_seconds,
        )

        if not extracted_text.strip():
            raise RuntimeError("No extractable text was returned.")

        # Parse into artifact
        artifact = parse_ocr_to_artifact(extracted_text)

        # Route based on state
        if session.state == AppState.EXTRACTION_AWAITING_DOC:
            session.job_id = job_id
            session.document_name = file_name
            session.text = artifact.full_text
            session.state = AppState.EXTRACTION_AWAITING_QUESTION
            session.awaiting_question = True

            from bot.workflows.legacy import get_action_keyboard
            await status_message.edit_text(
                f"✅ Document ready: {file_name}\nChoose an action:",
                reply_markup=get_action_keyboard()
            )

        elif session.state == AppState.COMPARISON_AWAITING_DOC_A:
            session.doc_a_name = file_name
            session.doc_a_text = artifact.full_text
            session.state = AppState.COMPARISON_AWAITING_DOC_B
            await status_message.edit_text(f"✅ Doc A loaded: {file_name}\nNow upload the SECOND document (Doc B).")

        elif session.state == AppState.COMPARISON_AWAITING_DOC_B:
            session.doc_b_name = file_name
            session.doc_b_text = artifact.full_text
            session.state = AppState.COMPARISON_AWAITING_LEVEL

            keyboard = [
                [InlineKeyboardButton("High Level", callback_data="compare_level:high")],
                [InlineKeyboardButton("Section Level", callback_data="compare_level:section")],
                [InlineKeyboardButton("Subsection Level", callback_data="compare_level:subsection")],
                [InlineKeyboardButton("Line Level", callback_data="compare_level:line")]
            ]
            await status_message.edit_text(
                f"✅ Doc B loaded: {file_name}\nSelect comparison level:",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

        elif session.state == AppState.ENTITY_AWAITING_DOC:
            session.entity_doc_name = file_name
            session.entity_doc_text = artifact.full_text
            session.state = AppState.ENTITY_AWAITING_MODE

            keyboard = [
                [InlineKeyboardButton("Manual (Provide entities)", callback_data="entity_mode:manual")],
                [InlineKeyboardButton("AI Decide", callback_data="entity_mode:ai")]
            ]
            await status_message.edit_text(
                f"✅ Document loaded: {file_name}\nChoose entity extraction mode:",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

    except Exception as e:
        logging.exception("Document processing failed")
        await status_message.edit_text(f"❌ Processing failed: {e}")

async def compare_level_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.message:
        return
    await query.answer()

    chat_id = query.message.chat.id
    sessions: dict[int, ChatSession] = context.application.bot_data["sessions"]
    session = sessions.get(chat_id)

    if not session or session.state != AppState.COMPARISON_AWAITING_LEVEL:
        await query.message.reply_text("Invalid state for this action.")
        return

    level = query.data.split(":")[1]
    session.comparison_level = level

    status_message = await query.message.reply_text("⏳ Generating comparison report...")

    try:
        config: BotConfig = context.application.bot_data["config"]
        chat_client: SarvamChatClient = context.application.bot_data["chat_client"]

        report = await run_comparison(
            chat_client=chat_client,
            model=config.chat_model,
            doc_a_text=session.doc_a_text or "",
            doc_b_text=session.doc_b_text or "",
            level=level
        )

        excel_bytes = create_comparison_workbook(report)

        await context.bot.send_document(
            chat_id=chat_id,
            document=excel_bytes,
            filename=f"Comparison_Results_{level}.xlsx",
            caption="✅ Document comparison complete!\n\nWhat's next? /start to begin again."
        )

        # Reset state back to module selection after completion
        session.state = AppState.MODULE_SELECTION
        session.current_module = None
        await status_message.delete()

    except Exception as e:
        logging.exception("Comparison failed")
        await status_message.edit_text(f"❌ Comparison failed: {e}")

async def entity_mode_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.message:
        return
    await query.answer()

    chat_id = query.message.chat.id
    sessions: dict[int, ChatSession] = context.application.bot_data["sessions"]
    session = sessions.get(chat_id)

    if not session or session.state != AppState.ENTITY_AWAITING_MODE:
        await query.message.reply_text("Invalid state for this action.")
        return

    mode = query.data.split(":")[1]
    session.entity_mode = mode

    if mode == "manual":
        session.state = AppState.ENTITY_AWAITING_ENTITIES
        await query.message.reply_text(
            "Please type the entities you want to extract, separated by semicolons (;).\n"
            "Example: `Invoice Number; Date; Total Amount`"
        )
    elif mode == "ai":
        # Jump directly to extraction
        await execute_entity_extraction(query.message, session, context)

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message or not message.text or not update.effective_chat:
        return

    chat_id = update.effective_chat.id
    sessions: dict[int, ChatSession] = context.application.bot_data["sessions"]
    session = sessions.get(chat_id)

    if not session:
        return

    if session.state == AppState.ENTITY_AWAITING_ENTITIES:
        raw_entities = message.text.split(";")
        session.entities_list = [e.strip() for e in raw_entities if e.strip()]

        if not session.entities_list:
            await message.reply_text("No valid entities found. Please try again (separate by semicolon).")
            return

        await execute_entity_extraction(message, session, context)

    elif session.state == AppState.EXTRACTION_AWAITING_QUESTION and session.awaiting_question:
        config: BotConfig = context.application.bot_data["config"]
        chat_client: SarvamChatClient = context.application.bot_data["chat_client"]
        question = message.text.strip()
        await execute_feature(
            reply_target=message,
            session=session,
            feature=FEATURE_ASK,
            config=config,
            chat_client=chat_client,
            question=question
        )

async def execute_entity_extraction(message, session: ChatSession, context: ContextTypes.DEFAULT_TYPE) -> None:
    status_message = await message.reply_text("⏳ Extracting entities...")

    try:
        config: BotConfig = context.application.bot_data["config"]
        chat_client: SarvamChatClient = context.application.bot_data["chat_client"]

        report = await run_extraction(
            chat_client=chat_client,
            model=config.chat_model,
            text=session.entity_doc_text or "",
            entities=session.entities_list
        )

        excel_bytes = create_entity_workbook(report)

        await context.bot.send_document(
            chat_id=session.chat_id,
            document=excel_bytes,
            filename=f"Entity_Results.xlsx",
            caption="✅ Entity extraction complete!\n\nWhat's next? /start to begin again."
        )

        # Reset state
        session.state = AppState.MODULE_SELECTION
        session.current_module = None
        await status_message.delete()

    except Exception as e:
        logging.exception("Extraction failed")
        await status_message.edit_text(f"❌ Extraction failed: {e}")

async def legacy_feature_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()

    chat_id = query.message.chat.id
    sessions: dict[int, ChatSession] = context.application.bot_data["sessions"]
    session = sessions.get(chat_id)

    if not session or session.state != AppState.EXTRACTION_AWAITING_QUESTION:
        await query.message.reply_text("No active extraction context found.")
        return

    feature = query.data
    config: BotConfig = context.application.bot_data["config"]
    chat_client: SarvamChatClient = context.application.bot_data["chat_client"]

    await execute_feature(
        reply_target=query.message,
        session=session,
        feature=feature,
        config=config,
        chat_client=chat_client,
    )
