import re
import logging
from typing import Any
from telegram.ext import ContextTypes
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from bot.config import BotConfig
from bot.state import ChatSession
from bot.clients.sarvam_chat import SarvamChatClient, MAX_CHAT_ATTEMPTS, CHAT_CONTEXT_BACKOFF_FACTOR

MIN_CHAT_CONTEXT_CHAR_LIMIT = 1200
FEATURE_OCR = "feature:ocr"
FEATURE_TLDR = "feature:tldr"
FEATURE_KEY_POINTS = "feature:key_points"
FEATURE_ACTION_ITEMS = "feature:action_items"
FEATURE_ASK = "feature:ask"

SYSTEM_PROMPT = (
    "You are a document analysis assistant. Use only the provided document text. "
    "Do not add outside facts. If information is missing, answer exactly: "
    "'Not found in document'."
)

def truncate_text(text: str, max_chars: int) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars], True

def is_prompt_too_long_error(error_text: str) -> bool:
    normalized = (error_text or "").lower()
    return "prompt is too long" in normalized or (
        "max length is" in normalized and "tokens" in normalized
    )

def extract_query_terms(text: str) -> list[str]:
    raw_terms = re.findall(r"[a-zA-Z0-9]{3,}", (text or "").lower())
    stop_words = {"the", "and", "that", "this", "for", "with", "from"}
    return [t for t in set(raw_terms) if t not in stop_words]

def select_relevant_context(document_text: str, question: str, max_chars: int) -> str:
    if len(document_text) <= max_chars:
        return document_text

    terms = extract_query_terms(question)
    if not terms:
        return document_text[:max_chars]

    paragraphs = [p.strip() for p in document_text.split("\n\n") if p.strip()]
    scored = []
    for p in paragraphs:
        p_lower = p.lower()
        score = sum(1 for t in terms if t in p_lower)
        scored.append((score, p))

    scored.sort(key=lambda x: x[0], reverse=True)
    selected = []
    current_length = 0
    for score, p in scored:
        if current_length + len(p) + 2 > max_chars:
            break
        selected.append(p)
        current_length += len(p) + 2

    return "\n\n".join(selected)

def build_prompt(feature: str, document_text: str, max_chars: int, question: str | None = None) -> str:
    clipped_text, was_truncated = truncate_text(document_text, max_chars=max_chars)
    truncation_note = (
        "\n\nNote: document text was truncated for context length limits."
        if was_truncated
        else ""
    )

    if feature == FEATURE_TLDR:
        task = (
            "Create a TL;DR in 5-7 short lines. Cover: purpose, key outcome, "
            "important numbers/dates, and why this matters."
        )
    elif feature == FEATURE_KEY_POINTS:
        task = (
            "Extract 8-12 key points as bullets. Keep only document-grounded facts."
        )
    elif feature == FEATURE_ACTION_ITEMS:
        task = (
            "Extract action items using one line per item in this format:\n"
            "Task | Owner (if found else Not specified) | "
            "Due date (if found else Not specified) | Priority.\n"
            "If no action items are present, answer exactly: Not found in document."
        )
    elif feature == FEATURE_ASK:
        task = (
            f"Question: {question}\n"
            "Answer from document text only. If the answer is absent, respond: "
            "Not found in document."
        )
    else:
        raise ValueError(f"Unsupported feature: {feature}")

    return (
        f"{task}{truncation_note}\n\n"
        "Document text:\n"
        "<<<DOCUMENT>>>\n"
        f"{clipped_text}\n"
        "<<<END DOCUMENT>>>"
    )

async def generate_chat_response(
    chat_client: SarvamChatClient,
    config: BotConfig,
    feature: str,
    document_text: str,
    question: str | None = None,
) -> str:
    context_limit = max(config.chat_context_char_limit, MIN_CHAT_CONTEXT_CHAR_LIMIT)
    prompt_source_text = document_text
    if feature == FEATURE_ASK and question:
        prompt_source_text = select_relevant_context(
            document_text=document_text,
            question=question,
            max_chars=max(context_limit * 2, context_limit),
        )

    last_error: Exception | None = None
    for _ in range(MAX_CHAT_ATTEMPTS):
        prompt = build_prompt(
            feature=feature,
            document_text=prompt_source_text,
            max_chars=context_limit,
            question=question,
        )
        try:
            return await chat_client.complete(
                model=config.chat_model,
                system_prompt=SYSTEM_PROMPT,
                user_prompt=prompt,
            )
        except Exception as exc:
            last_error = exc
            if is_prompt_too_long_error(str(exc)) and context_limit > MIN_CHAT_CONTEXT_CHAR_LIMIT:
                next_limit = max(
                    MIN_CHAT_CONTEXT_CHAR_LIMIT,
                    int(context_limit * CHAT_CONTEXT_BACKOFF_FACTOR),
                )
                if next_limit == context_limit:
                    break
                logging.warning(
                    "Chat prompt too long. Retrying with reduced context limit: %s -> %s",
                    context_limit,
                    next_limit,
                )
                context_limit = next_limit
                continue
            raise

    raise RuntimeError(f"Chat request failed after retries: {last_error}")

async def send_long_text(reply_target: Any, text: str) -> None:
    max_msg_length = 4000
    if len(text) <= max_msg_length:
        await reply_target.reply_text(text)
        return

    parts = [text[i : i + max_msg_length] for i in range(0, len(text), max_msg_length)]
    for index, part in enumerate(parts):
        await reply_target.reply_text(f"[Part {index + 1}/{len(parts)}]\n{part}")

async def send_ocr_output(reply_target: Any, document_name: str, text: str) -> None:
    await send_long_text(
        reply_target=reply_target,
        text=f"📄 Extracted Text for {document_name}:\n\n{text}",
    )

def get_action_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("📄 View Full OCR", callback_data=FEATURE_OCR)],
        [InlineKeyboardButton("📝 Generate TL;DR", callback_data=FEATURE_TLDR)],
        [InlineKeyboardButton("🔑 Extract Key Points", callback_data=FEATURE_KEY_POINTS)],
        [InlineKeyboardButton("✅ Extract Action Items", callback_data=FEATURE_ACTION_ITEMS)],
        [InlineKeyboardButton("❓ Ask Question", callback_data=FEATURE_ASK)],
    ]
    return InlineKeyboardMarkup(keyboard)

async def send_action_menu(reply_target: Any, prompt: str = "Choose an action:") -> None:
    await reply_target.reply_text(
        prompt,
        reply_markup=get_action_keyboard(),
    )

async def execute_feature(
    reply_target: Any,
    session: ChatSession,
    feature: str,
    config: BotConfig,
    chat_client: SarvamChatClient,
    question: str | None = None,
) -> None:
    if feature == FEATURE_OCR:
        session.awaiting_question = False
        await send_ocr_output(
            reply_target=reply_target,
            document_name=session.document_name,
            text=session.text,
        )
        await send_action_menu(reply_target, prompt="🧭 What next?")
        return

    if feature not in {FEATURE_TLDR, FEATURE_KEY_POINTS, FEATURE_ACTION_ITEMS, FEATURE_ASK}:
        await reply_target.reply_text("Unknown action.")
        return

    if feature == FEATURE_ASK and not question:
        session.awaiting_question = True
        await reply_target.reply_text("❓ Send your question based on the latest uploaded document.")
        return

    session.awaiting_question = False
    working_label = "🤖 Generating response..." if feature != FEATURE_ASK else "🔎 Analyzing your question..."
    working_message = await reply_target.reply_text(working_label)
    try:
        response_text = await generate_chat_response(
            chat_client=chat_client,
            config=config,
            feature=feature,
            document_text=session.text,
            question=question,
        )
        await working_message.delete()
        await send_long_text(reply_target=reply_target, text=response_text)
        await send_action_menu(reply_target, prompt="🧭 What next?")
    except Exception as exc:
        logging.exception("Feature request failed")
        await working_message.edit_text(f"❌ Failed to generate response: {exc}")
