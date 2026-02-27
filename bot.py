import asyncio
import html
import json
import logging
import mimetypes
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

import httpx
from dotenv import load_dotenv
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, InputFile, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

SUPPORTED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg"}
MAX_SOURCE_FILE_BYTES = 200 * 1024 * 1024
MIN_CHAT_CONTEXT_CHAR_LIMIT = 1200
MAX_CHAT_ATTEMPTS = 4
CHAT_CONTEXT_BACKOFF_FACTOR = 0.6
VISION_MAX_RETRIES_PER_STRATEGY = 3
VISION_RETRY_DEFAULT_SECONDS = 65
VISION_RETRY_MAX_SECONDS = 180
VISION_STATUS_REPEAT_UPDATE_SECONDS = 20

FEATURE_OCR = "feature:ocr"
FEATURE_TLDR = "feature:tldr"
FEATURE_KEY_POINTS = "feature:key_points"
FEATURE_ACTION_ITEMS = "feature:action_items"
FEATURE_ASK = "feature:ask"

TERMINAL_STATES = {"Completed", "PartiallyCompleted", "Failed", "Cancelled", "Canceled"}
SYSTEM_PROMPT = (
    "You are a document analysis assistant. Use only the provided document text. "
    "Do not add outside facts. If information is missing, answer exactly: "
    "'Not found in document'."
)


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


@dataclass
class ChatSession:
    job_id: str
    document_name: str
    text: str
    awaiting_question: bool
    updated_at: float


class SarvamVisionClient:
    def __init__(self, api_key: str, base_url: str, timeout_seconds: int = 60) -> None:
        self.api_key = api_key
        self.base_url = base_url
        self.timeout_seconds = timeout_seconds

    def _auth_headers(self) -> dict[str, str]:
        return {"api-subscription-key": self.api_key}

    async def extract_text(
        self,
        file_name: str,
        file_bytes: bytes,
        content_type: str,
        language: str,
        output_format: str,
        poll_interval_seconds: float,
        poll_timeout_seconds: int,
        progress_callback: Any = None,
    ) -> tuple[str, str]:
        candidates = build_upload_candidates(
            file_name=file_name,
            file_bytes=file_bytes,
            content_type=content_type,
        )

        attempt_errors: list[str] = []
        for index, candidate in enumerate(candidates, start=1):
            upload_name, upload_bytes, upload_content_type, strategy = candidate
            if progress_callback and len(candidates) > 1:
                await progress_callback(
                    f"📦 Upload strategy {index}/{len(candidates)}: {strategy}"
                )

            for retry_index in range(1, VISION_MAX_RETRIES_PER_STRATEGY + 1):
                try:
                    return await self._run_single_job(
                        language=language,
                        output_format=output_format,
                        upload_name=upload_name,
                        upload_bytes=upload_bytes,
                        upload_content_type=upload_content_type,
                        poll_interval_seconds=poll_interval_seconds,
                        poll_timeout_seconds=poll_timeout_seconds,
                        progress_callback=progress_callback,
                    )
                except Exception as exc:
                    message = str(exc)
                    can_retry_here = retry_index < VISION_MAX_RETRIES_PER_STRATEGY
                    if can_retry_here and self._is_retryable_vision_failure(message):
                        wait_seconds = self._extract_retry_after_seconds(message)
                        wait_seconds = max(
                            1,
                            min(wait_seconds or VISION_RETRY_DEFAULT_SECONDS, VISION_RETRY_MAX_SECONDS),
                        )
                        if progress_callback:
                            await progress_callback(
                                "⏳ Sarvam Vision is temporarily busy. "
                                f"Retrying {strategy} in {wait_seconds}s "
                                f"(attempt {retry_index + 1}/{VISION_MAX_RETRIES_PER_STRATEGY})..."
                            )
                        await asyncio.sleep(wait_seconds)
                        continue

                    error_label = f"{strategy} (attempt {retry_index}/{VISION_MAX_RETRIES_PER_STRATEGY})"
                    attempt_errors.append(f"{error_label}: {message}")
                    break

            has_more = index < len(candidates)
            if has_more and progress_callback:
                await progress_callback(
                    f"{strategy} failed, retrying with alternate input packaging..."
                )

        if attempt_errors:
            raise RuntimeError(f"Vision extraction failed. {' | '.join(attempt_errors)}")
        raise RuntimeError("Vision extraction failed with unknown error.")

    async def _run_single_job(
        self,
        language: str,
        output_format: str,
        upload_name: str,
        upload_bytes: bytes,
        upload_content_type: str,
        poll_interval_seconds: float,
        poll_timeout_seconds: int,
        progress_callback: Any = None,
    ) -> tuple[str, str]:
        async with httpx.AsyncClient(
            base_url=self.base_url,
            headers=self._auth_headers(),
            timeout=self.timeout_seconds,
        ) as api_client:
            if progress_callback:
                await progress_callback("🧾 Creating Vision job...")
            job_id = await self._create_job(
                api_client=api_client,
                language=language,
                output_format=output_format,
                progress_callback=progress_callback,
            )

            if progress_callback:
                await progress_callback("🔗 Getting upload URL...")
            upload_urls = await self._get_upload_urls(
                api_client=api_client,
                job_id=job_id,
                upload_file_name=upload_name,
            )

            if progress_callback:
                await progress_callback("⏫ Uploading file...")
            await self._upload_file(
                upload_urls=upload_urls,
                upload_file_name=upload_name,
                upload_bytes=upload_bytes,
                content_type=upload_content_type,
            )

            if progress_callback:
                await progress_callback("🚀 Starting OCR job...")
            await self._start_job(api_client=api_client, job_id=job_id)

            status_payload = await self._wait_for_completion(
                api_client=api_client,
                job_id=job_id,
                poll_interval_seconds=poll_interval_seconds,
                poll_timeout_seconds=poll_timeout_seconds,
                progress_callback=progress_callback,
            )

            final_state = str(status_payload.get("job_state", "Unknown"))
            if final_state == "Failed":
                raise RuntimeError(self._format_failed_status(status_payload))
            if final_state not in {"Completed", "PartiallyCompleted"}:
                raise RuntimeError(f"Vision job ended in unsupported state: {final_state}")

            if progress_callback:
                await progress_callback("📦 Preparing output download...")
            download_urls = await self._get_download_urls(api_client=api_client, job_id=job_id)

        if progress_callback:
            await progress_callback("⬇️ Downloading OCR output...")
        zip_bytes = await self._download_file(download_urls=download_urls)
        extracted_text = extract_text_from_output_zip(zip_bytes)
        return job_id, extracted_text

    async def _create_job(
        self,
        api_client: httpx.AsyncClient,
        language: str,
        output_format: str,
        progress_callback: Any = None,
    ) -> str:
        payload = {"job_parameters": {"language": language, "output_format": output_format}}
        response = await api_client.post("/doc-digitization/job/v1", json=payload)
        if response.is_error and output_format.lower() == "json":
            error_message = self._extract_error_message(response)
            if response.status_code == 400 and self._is_output_format_restricted(error_message):
                if progress_callback:
                    await progress_callback(
                        "⚠️ JSON output not enabled on this account. Falling back to md..."
                    )
                fallback_payload = {"job_parameters": {"language": language, "output_format": "md"}}
                response = await api_client.post("/doc-digitization/job/v1", json=fallback_payload)
        self._raise_for_status(response, "Create Vision job")
        data = response.json()
        job_id = data.get("job_id")
        if not job_id:
            raise RuntimeError(f"Create job response missing job_id: {data}")
        return str(job_id)

    async def _get_upload_urls(
        self,
        api_client: httpx.AsyncClient,
        job_id: str,
        upload_file_name: str,
    ) -> Any:
        payload = {"job_id": job_id, "files": [upload_file_name]}
        response = await api_client.post("/doc-digitization/job/v1/upload-files", json=payload)
        self._raise_for_status(response, "Get upload URLs")
        data = response.json()
        upload_urls = data.get("upload_urls")
        if not upload_urls:
            raise RuntimeError(f"Upload URLs missing in response: {data}")
        return upload_urls

    async def _start_job(self, api_client: httpx.AsyncClient, job_id: str) -> None:
        response = await api_client.post(f"/doc-digitization/job/v1/{job_id}/start")
        self._raise_for_status(response, "Start Vision job")

    async def _get_status(self, api_client: httpx.AsyncClient, job_id: str) -> dict[str, Any]:
        response = await api_client.get(f"/doc-digitization/job/v1/{job_id}/status")
        self._raise_for_status(response, "Get Vision job status")
        return response.json()

    async def _wait_for_completion(
        self,
        api_client: httpx.AsyncClient,
        job_id: str,
        poll_interval_seconds: float,
        poll_timeout_seconds: int,
        progress_callback: Any = None,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + poll_timeout_seconds
        last_progress_message = ""
        start_time = time.monotonic()
        last_emit_time = 0.0
        while True:
            status_payload = await self._get_status(api_client=api_client, job_id=job_id)
            state = str(status_payload.get("job_state", "Unknown"))
            processed, total = extract_page_progress(status_payload)
            state_icon = {
                "Accepted": "🟦",
                "Pending": "⏳",
                "Running": "⚙️",
                "Completed": "✅",
                "PartiallyCompleted": "☑️",
                "Failed": "❌",
                "Cancelled": "🛑",
                "Canceled": "🛑",
            }.get(state, "ℹ️")

            progress_message = f"{state_icon} Vision status: {state}"
            if total:
                progress_message += f" ({processed}/{total} pages)"
            elapsed = int(time.monotonic() - start_time)
            if state == "Pending" and elapsed >= 90:
                progress_message += f" (queued for {elapsed}s)"
            elif state in {"Running", "Pending"}:
                progress_message += f" (elapsed {elapsed}s)"

            now = time.monotonic()
            should_emit = (
                progress_message != last_progress_message
                or (now - last_emit_time) >= VISION_STATUS_REPEAT_UPDATE_SECONDS
            )
            if progress_callback and should_emit:
                await progress_callback(progress_message)
                last_progress_message = progress_message
                last_emit_time = now

            if state in TERMINAL_STATES:
                return status_payload

            if time.monotonic() > deadline:
                raise TimeoutError("Vision processing timed out.")

            await asyncio.sleep(poll_interval_seconds)

    async def _get_download_urls(self, api_client: httpx.AsyncClient, job_id: str) -> Any:
        response = await api_client.post(f"/doc-digitization/job/v1/{job_id}/download-files")
        self._raise_for_status(response, "Get download URLs")
        data = response.json()
        download_urls = data.get("download_urls")
        if not download_urls:
            raise RuntimeError(f"Download URLs missing in response: {data}")
        return download_urls

    async def _upload_file(
        self,
        upload_urls: Any,
        upload_file_name: str,
        upload_bytes: bytes,
        content_type: str,
    ) -> None:
        entry = pick_url_entry(url_payload=upload_urls, preferred_key=upload_file_name)
        url, method, headers, fields = parse_presigned_entry(entry, default_method="PUT")

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as storage_client:
            if fields:
                upload_method = method if method in {"POST", "PUT"} else "POST"
                files = {"file": (upload_file_name, upload_bytes, content_type)}
                response = await storage_client.request(
                    method=upload_method,
                    url=url,
                    data=fields,
                    files=files,
                    headers=headers,
                )
                self._raise_for_status(response, "Upload source file")
                return

            upload_headers = normalize_header_keys(headers)
            if "content-type" not in upload_headers:
                upload_headers["content-type"] = content_type

            upload_method = method if method in {"PUT", "POST"} else "PUT"
            if upload_method == "PUT" and "blob.core.windows.net" in url.lower():
                # Azure Blob SAS uploads require blob type on PUT.
                upload_headers.setdefault("x-ms-blob-type", "BlockBlob")

            response = await storage_client.request(
                method=upload_method,
                url=url,
                content=upload_bytes,
                headers=upload_headers,
            )

            self._raise_for_status(response, "Upload source file")

    async def _download_file(self, download_urls: Any) -> bytes:
        entry = pick_url_entry(url_payload=download_urls, preferred_key=None)
        url, method, headers, fields = parse_presigned_entry(entry, default_method="GET")
        download_method = method if method in {"GET", "POST"} else "GET"

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as storage_client:
            response = await storage_client.request(
                method=download_method,
                url=url,
                headers=headers,
                data=fields if fields else None,
            )
            self._raise_for_status(response, "Download OCR output")
            return response.content

    @staticmethod
    def _raise_for_status(response: httpx.Response, context: str) -> None:
        if not response.is_error:
            return

        details = ""
        try:
            payload = response.json()
            details = json.dumps(payload, ensure_ascii=False)
        except Exception:
            details = response.text

        raise RuntimeError(f"{context} failed ({response.status_code}): {details}")

    @staticmethod
    def _extract_error_message(response: httpx.Response) -> str:
        try:
            payload = response.json()
            error = payload.get("error")
            if isinstance(error, dict):
                message = error.get("message")
                if isinstance(message, str):
                    return message
        except Exception:
            pass
        return response.text

    @staticmethod
    def _is_output_format_restricted(error_message: str) -> bool:
        message = (error_message or "").lower()
        return "output_format" in message and "html" in message and "md" in message

    @staticmethod
    def _is_retryable_vision_failure(error_message: str) -> bool:
        message = (error_message or "").lower()
        retry_indicators = (
            "circuit_breaker_open",
            "circuit breaker",
            "retry after",
            "temporarily unavailable",
            "timeout",
            "rate limit",
            "too many requests",
            "service unavailable",
        )
        return any(indicator in message for indicator in retry_indicators)

    @staticmethod
    def _extract_retry_after_seconds(error_message: str) -> int | None:
        message = error_message or ""
        patterns = [
            r"retry after\s+(\d+)\s*s",
            r"retry after\s+(\d+)\s*seconds",
            r"after\s+(\d+)\s*s",
            r"after\s+(\d+)\s*seconds",
        ]
        for pattern in patterns:
            match = re.search(pattern, message, flags=re.IGNORECASE)
            if match:
                try:
                    return int(match.group(1))
                except Exception:
                    return None
        return None

    @staticmethod
    def _format_failed_status(status_payload: dict[str, Any]) -> str:
        details: list[str] = []
        job_error = str(status_payload.get("error_message") or "").strip()
        if job_error:
            details.append(f"job_error={job_error}")

        job_details = status_payload.get("job_details")
        if isinstance(job_details, list):
            for detail in job_details[:2]:
                if not isinstance(detail, dict):
                    continue
                detail_error = str(detail.get("error_message") or "").strip()
                detail_code = str(detail.get("error_code") or "").strip()
                if detail_error:
                    prefix = f"code={detail_code} " if detail_code else ""
                    details.append(f"file_error={prefix}{detail_error}".strip())

                page_errors = detail.get("page_errors")
                if isinstance(page_errors, list):
                    for page_error in page_errors[:3]:
                        if not isinstance(page_error, dict):
                            continue
                        page_number = page_error.get("page_number")
                        page_code = page_error.get("error_code")
                        page_message = page_error.get("error_message")
                        if page_message:
                            details.append(
                                f"page_error(page={page_number}, code={page_code}): {page_message}"
                            )

        if not details:
            payload_preview = json.dumps(status_payload, ensure_ascii=False)[:700]
            details.append(f"No structured error details. status_payload={payload_preview}")

        return f"Vision job failed. {' | '.join(details)}"


class SarvamChatClient:
    def __init__(self, api_key: str, base_url: str, timeout_seconds: int = 60) -> None:
        self.api_key = api_key
        self.base_url = base_url
        self.timeout_seconds = timeout_seconds

    def _auth_headers(self) -> dict[str, str]:
        return {"api-subscription-key": self.api_key}

    async def complete(
        self,
        model: str,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.2,
    ) -> str:
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
        }

        async with httpx.AsyncClient(
            base_url=self.base_url,
            headers=self._auth_headers(),
            timeout=self.timeout_seconds,
        ) as client:
            response = await client.post("/v1/chat/completions", json=payload)

        if response.is_error:
            details = response.text
            try:
                details = json.dumps(response.json(), ensure_ascii=False)
            except Exception:
                pass
            raise RuntimeError(f"Chat completion failed ({response.status_code}): {details}")

        data = response.json()
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError(f"Missing choices in chat response: {data}")

        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, str):
            return content.strip()

        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text")
                    if text:
                        parts.append(str(text))
            merged = "\n".join(parts).strip()
            if merged:
                return merged

        raise RuntimeError(f"Unsupported chat response shape: {data}")


def is_image_input(file_name: str, content_type: str) -> bool:
    extension = Path(file_name).suffix.lower()
    mime = (content_type or "").lower()
    return extension in IMAGE_EXTENSIONS or mime.startswith("image/")


def build_upload_candidates(
    file_name: str,
    file_bytes: bytes,
    content_type: str,
) -> list[tuple[str, bytes, str, str]]:
    safe_name = sanitize_filename(file_name, default_name="document.pdf")
    if not is_image_input(safe_name, content_type):
        detected_type = content_type or "application/pdf"
        return [(safe_name, file_bytes, detected_type, "direct-pdf")]

    image_name = ensure_image_extension(
        file_name=safe_name,
        content_type=content_type,
    )
    direct_content_type = (
        content_type
        if (content_type or "").lower().startswith("image/")
        else guess_image_mime(image_name)
    )
    zip_name, zip_bytes, zip_content_type = build_image_zip_payload(
        image_name=image_name,
        image_bytes=file_bytes,
    )

    return [
        (image_name, file_bytes, direct_content_type, "direct-image"),
        (zip_name, zip_bytes, zip_content_type, "zipped-image"),
    ]


def ensure_image_extension(file_name: str, content_type: str) -> str:
    extension = Path(file_name).suffix.lower()
    if extension in IMAGE_EXTENSIONS:
        return file_name
    if (content_type or "").lower() == "image/png":
        return sanitize_filename(f"{Path(file_name).stem or 'image'}.png", "image.png")
    return sanitize_filename(f"{Path(file_name).stem or 'image'}.jpg", "image.jpg")


def guess_image_mime(file_name: str) -> str:
    extension = Path(file_name).suffix.lower()
    if extension == ".png":
        return "image/png"
    return "image/jpeg"


def build_image_zip_payload(image_name: str, image_bytes: bytes) -> tuple[str, bytes, str]:
    zip_name = sanitize_filename(f"{Path(image_name).stem}.zip", "image.zip")
    buffer = BytesIO()
    with ZipFile(buffer, mode="w", compression=ZIP_DEFLATED) as archive:
        archive.writestr(image_name, image_bytes)
    return zip_name, buffer.getvalue(), "application/zip"


def sanitize_filename(name: str, default_name: str) -> str:
    candidate = (name or "").strip()
    if not candidate:
        return default_name
    candidate = candidate.replace("\\", "_").replace("/", "_")
    return re.sub(r"[^A-Za-z0-9._-]", "_", candidate)


def pick_url_entry(url_payload: Any, preferred_key: str | None) -> Any:
    if isinstance(url_payload, str):
        return url_payload

    if isinstance(url_payload, list) and url_payload:
        return url_payload[0]

    if isinstance(url_payload, dict):
        if preferred_key and preferred_key in url_payload:
            return url_payload[preferred_key]

        if preferred_key:
            preferred_base = Path(preferred_key).name
            for key, value in url_payload.items():
                if Path(str(key)).name == preferred_base:
                    return value

        for value in url_payload.values():
            return value

    raise RuntimeError(f"Could not resolve URL entry from payload: {url_payload}")


def parse_presigned_entry(
    entry: Any,
    default_method: str,
) -> tuple[str, str, dict[str, str], dict[str, str] | None]:
    if isinstance(entry, str):
        return entry, default_method, {}, None

    if not isinstance(entry, dict):
        raise RuntimeError(f"Unsupported presigned entry format: {entry}")

    url = (
        entry.get("url")
        or entry.get("upload_url")
        or entry.get("download_url")
        or entry.get("presigned_url")
        or entry.get("signed_url")
    )
    if not url:
        for value in entry.values():
            if isinstance(value, str) and value.startswith("http"):
                url = value
                break
    if not url:
        raise RuntimeError(f"Missing URL in presigned entry: {entry}")

    method = str(entry.get("method") or entry.get("http_method") or default_method).upper()
    headers = entry.get("headers") or entry.get("request_headers") or {}
    fields = entry.get("fields") or entry.get("form_fields") or entry.get("formData")

    if not isinstance(headers, dict):
        headers = {}
    if fields is not None and not isinstance(fields, dict):
        fields = None

    normalized_headers = normalize_header_keys(headers)
    normalized_fields = {str(k): str(v) for k, v in fields.items()} if fields else None
    return str(url), method, normalized_headers, normalized_fields


def normalize_header_keys(headers: dict[str, Any]) -> dict[str, str]:
    return {str(key).lower(): str(value) for key, value in headers.items()}


def extract_page_progress(status_payload: dict[str, Any]) -> tuple[int, int]:
    details = status_payload.get("job_details")
    if not isinstance(details, list) or not details:
        return 0, 0
    first = details[0]
    if not isinstance(first, dict):
        return 0, 0
    processed = int(first.get("pages_processed") or 0)
    total = int(first.get("total_pages") or 0)
    return processed, total


def extract_text_from_output_zip(zip_bytes: bytes) -> str:
    try:
        with ZipFile(BytesIO(zip_bytes), mode="r") as archive:
            names = sorted(name for name in archive.namelist() if not name.endswith("/"))
            names = filter_output_files(names)
            if not names:
                raise RuntimeError("Output ZIP is empty.")

            sections: list[str] = []
            for name in names:
                raw = archive.read(name)
                ext = Path(name).suffix.lower()
                extracted = extract_text_from_output_file(ext, raw)
                if not extracted:
                    continue

                cleaned = normalize_text(extracted)
                if not cleaned:
                    continue

                sections.append(cleaned)

            if not sections:
                raise RuntimeError("Could not parse text from output ZIP.")

            merged = "\n\n".join(sections).strip()
            cleaned_output = clean_extracted_ocr_text(merged)
            if not cleaned_output:
                raise RuntimeError("OCR output only contained metadata/artifacts after cleanup.")
            return cleaned_output
    except Exception as exc:
        raise RuntimeError(f"Failed to parse OCR output ZIP: {exc}") from exc


def filter_output_files(names: list[str]) -> list[str]:
    has_non_json_text = any(
        Path(name).suffix.lower() in {".md", ".markdown", ".txt", ".html", ".htm"} for name in names
    )
    if has_non_json_text:
        names = [name for name in names if Path(name).suffix.lower() != ".json"]
    return names


def clean_extracted_ocr_text(text: str) -> str:
    cleaned = text
    cleaned = re.sub(r"!\[[^\]]*]\(data:image/[^)]+\)", "", cleaned, flags=re.IGNORECASE)

    filtered_lines: list[str] = []
    for line in cleaned.splitlines():
        stripped = line.strip()
        if not stripped:
            filtered_lines.append("")
            continue

        if re.fullmatch(r"\[[^\]]+\.(md|markdown|html|htm|json|txt)\]", stripped, re.IGNORECASE):
            continue

        lower = stripped.lower()
        if lower.startswith("the image displays "):
            continue
        if lower.startswith("*the image displays ") and lower.endswith("*"):
            continue
        if lower.startswith("*a four-pointed star") and lower.endswith("*"):
            continue

        filtered_lines.append(line)

    cleaned = "\n".join(filtered_lines)
    cleaned = normalize_text(cleaned)
    return cleaned


def extract_text_from_output_file(extension: str, file_bytes: bytes) -> str:
    decoded = file_bytes.decode("utf-8", errors="replace")

    if extension in {".md", ".markdown", ".txt"}:
        return decoded

    if extension in {".html", ".htm"}:
        return strip_html(decoded)

    if extension == ".json":
        try:
            payload = json.loads(decoded)
        except Exception:
            return decoded
        return extract_text_from_json_payload(payload)

    return decoded


def extract_text_from_json_payload(payload: Any) -> str:
    lines: list[str] = []
    text_like_keys = (
        "text",
        "content",
        "markdown",
        "md",
        "paragraph",
        "line",
        "value",
        "title",
        "header",
        "footer",
        "cell",
    )
    ignored_keys = {"job_id", "file_name", "error_message", "error_code"}

    def walk(node: Any, parent_key: str = "") -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                key_lower = str(key).lower()
                if isinstance(value, str):
                    if key_lower not in ignored_keys and (
                        key_lower in text_like_keys
                        or "text" in key_lower
                        or "content" in key_lower
                        or "markdown" in key_lower
                    ):
                        cleaned = value.strip()
                        if cleaned:
                            lines.append(cleaned)
                else:
                    walk(value, key_lower)
            return

        if isinstance(node, list):
            for item in node:
                walk(item, parent_key)
            return

        if isinstance(node, str):
            if parent_key and (
                parent_key in text_like_keys
                or "text" in parent_key
                or "content" in parent_key
                or "markdown" in parent_key
            ):
                cleaned = node.strip()
                if cleaned:
                    lines.append(cleaned)

    walk(payload)
    deduped = dedupe_preserve_order(lines)
    if deduped:
        return "\n".join(deduped)
    return json.dumps(payload, ensure_ascii=False, indent=2)


def dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def strip_html(source: str) -> str:
    without_scripts = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", source)
    without_tags = re.sub(r"(?s)<[^>]+>", " ", without_scripts)
    unescaped = html.unescape(without_tags)
    return normalize_text(unescaped)


def normalize_text(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(r"[ \t]+\n", "\n", normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    normalized = re.sub(r"[ \t]{2,}", " ", normalized)
    return normalized.strip()


def get_action_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📄 Complete OCR", callback_data=FEATURE_OCR),
                InlineKeyboardButton("🧠 TL;DR", callback_data=FEATURE_TLDR),
            ],
            [
                InlineKeyboardButton("🔑 Key Points", callback_data=FEATURE_KEY_POINTS),
                InlineKeyboardButton("✅ Action Items", callback_data=FEATURE_ACTION_ITEMS),
            ],
            [InlineKeyboardButton("❓ Ask Question", callback_data=FEATURE_ASK)],
        ]
    )


async def send_action_menu(reply_target: Any, prompt: str = "Choose an action:") -> None:
    await reply_target.reply_text(prompt, reply_markup=get_action_keyboard())


def now_utc_timestamp() -> float:
    return datetime.now(timezone.utc).timestamp()


def cleanup_sessions(sessions: dict[int, ChatSession], ttl_minutes: int) -> None:
    cutoff = now_utc_timestamp() - (ttl_minutes * 60)
    to_delete = [chat_id for chat_id, session in sessions.items() if session.updated_at < cutoff]
    for chat_id in to_delete:
        sessions.pop(chat_id, None)


def classify_upload(file_name: str, mime_type: str, is_photo: bool) -> str | None:
    if is_photo:
        return "image"
    extension = Path(file_name).suffix.lower()
    mime = (mime_type or "").lower()
    if extension == ".pdf" or mime == "application/pdf":
        return "pdf"
    if extension in IMAGE_EXTENSIONS or mime.startswith("image/"):
        return "image"
    return None


def truncate_text(text: str, max_chars: int) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars], True


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


def is_prompt_too_long_error(error_text: str) -> bool:
    normalized = (error_text or "").lower()
    return "prompt is too long" in normalized or (
        "max length is" in normalized and "tokens" in normalized
    )


def extract_query_terms(text: str) -> list[str]:
    raw_terms = re.findall(r"[a-zA-Z0-9]{3,}", (text or "").lower())
    stop_words = {
        "the",
        "and",
        "for",
        "with",
        "this",
        "that",
        "from",
        "what",
        "when",
        "where",
        "which",
        "about",
        "into",
        "your",
        "their",
        "have",
        "does",
        "please",
        "show",
        "tell",
        "document",
        "image",
    }
    deduped: list[str] = []
    seen: set[str] = set()
    for term in raw_terms:
        if term in stop_words or term in seen:
            continue
        seen.add(term)
        deduped.append(term)
    return deduped


def select_relevant_context(document_text: str, question: str, max_chars: int) -> str:
    terms = extract_query_terms(question)
    if not terms:
        return document_text

    blocks = [block.strip() for block in re.split(r"\n{2,}", document_text) if block.strip()]
    if not blocks:
        return document_text

    scored_blocks: list[tuple[int, int, str]] = []
    for index, block in enumerate(blocks):
        lowered = block.lower()
        score = 0
        for term in terms:
            if term in lowered:
                score += 1
        if score > 0:
            scored_blocks.append((score, index, block))

    if not scored_blocks:
        return document_text

    scored_blocks.sort(key=lambda item: (-item[0], item[1]))
    selected: list[str] = []
    total = 0
    for _, _, block in scored_blocks:
        extra = len(block) + 2
        if total + extra > max_chars:
            continue
        selected.append(block)
        total += extra
        if total >= max_chars:
            break

    if not selected:
        return document_text
    return "\n\n".join(selected)


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


def split_text(text: str, max_chunk_size: int) -> list[str]:
    if len(text) <= max_chunk_size:
        return [text]

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + max_chunk_size, len(text))
        if end < len(text):
            split_at = text.rfind("\n", start, end)
            if split_at > start + int(max_chunk_size * 0.4):
                end = split_at
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start = end
    return chunks


def markdownish_to_html(text: str) -> str:
    lines: list[str] = []
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            lines.append("")
            continue

        heading_match = re.match(r"^\s*#{1,6}\s+(.*)$", raw_line)
        if heading_match:
            lines.append(f"<b>{html.escape(heading_match.group(1).strip())}</b>")
            continue

        bullet_match = re.match(r"^\s*[-*]\s+(.*)$", raw_line)
        if bullet_match:
            lines.append(f"• {html.escape(bullet_match.group(1).strip())}")
            continue

        lines.append(html.escape(raw_line))

    rendered = "\n".join(lines)
    rendered = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", rendered)
    rendered = re.sub(r"__(.+?)__", r"<b>\1</b>", rendered)
    rendered = re.sub(r"`([^`]+)`", r"<code>\1</code>", rendered)
    rendered = re.sub(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)", r"<i>\1</i>", rendered)
    return rendered


async def send_long_text(reply_target: Any, text: str) -> None:
    for chunk in split_text(text, max_chunk_size=3400):
        formatted = markdownish_to_html(chunk)
        await reply_target.reply_text(formatted, parse_mode=ParseMode.HTML)


async def send_ocr_output(reply_target: Any, document_name: str, text: str) -> None:
    normalized = normalize_text(text)
    if not normalized:
        await reply_target.reply_text("OCR output is empty.")
        return

    if len(normalized) > 18000:
        preview = normalized[:2800]
        await reply_target.reply_text(
            "<b>Complete OCR (preview):</b>",
            parse_mode=ParseMode.HTML,
        )
        await reply_target.reply_text(
            f"<pre>{html.escape(preview)}</pre>",
            parse_mode=ParseMode.HTML,
        )

        filename = f"{Path(document_name).stem or 'ocr_output'}_ocr.txt"
        buffer = BytesIO(normalized.encode("utf-8"))
        buffer.seek(0)
        await reply_target.reply_document(
            document=InputFile(buffer, filename=filename),
            caption="Full OCR output attached as .txt",
        )
        return

    await reply_target.reply_text(
        "<b>Complete OCR:</b>",
        parse_mode=ParseMode.HTML,
    )
    for chunk in split_text(normalized, max_chunk_size=2800):
        await reply_target.reply_text(
            f"<pre>{html.escape(chunk)}</pre>",
            parse_mode=ParseMode.HTML,
        )


def get_session_for_chat(
    sessions: dict[int, ChatSession],
    chat_id: int,
    ttl_minutes: int,
) -> ChatSession | None:
    cleanup_sessions(sessions, ttl_minutes)
    session = sessions.get(chat_id)
    if session:
        session.updated_at = now_utc_timestamp()
    return session


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


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    if not update.effective_message:
        return

    message = (
        "👋 Upload a PDF or image (PNG/JPG/JPEG), then pick an action.\n\n"
        "You can use inline buttons or the Telegram menu (☰):\n"
        "/ocr, /tldr, /keypoints, /actions, /ask\n\n"
        "Supported input right now: PDF and images only."
    )
    await update.effective_message.reply_text(message, reply_markup=get_action_keyboard())


async def document_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message or not update.effective_chat:
        return

    config: BotConfig = context.application.bot_data["config"]
    vision_client: SarvamVisionClient = context.application.bot_data["vision_client"]
    sessions: dict[int, ChatSession] = context.application.bot_data["sessions"]

    cleanup_sessions(sessions, config.session_ttl_minutes)

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

    file_type = classify_upload(file_name=file_name, mime_type=mime_type, is_photo=is_photo)
    extension = Path(file_name).suffix.lower()
    if not file_type or (extension and extension not in SUPPORTED_EXTENSIONS and not is_photo):
        await message.reply_text("Unsupported file. Please upload PDF, PNG, JPG, or JPEG.")
        return

    status_message = await message.reply_text("📥 Downloading file from Telegram...")
    try:
        telegram_file = await context.bot.get_file(telegram_file_id)
        downloaded = await telegram_file.download_as_bytearray()
        file_bytes = bytes(downloaded)
        if not file_bytes:
            raise RuntimeError("Downloaded file is empty.")
        if len(file_bytes) > MAX_SOURCE_FILE_BYTES:
            raise RuntimeError(
                "File is too large. Sarvam Vision supports up to 200 MB per file."
            )

        last_status = {"text": ""}

        async def progress_callback(text: str) -> None:
            if text == last_status["text"]:
                return
            last_status["text"] = text
            try:
                await status_message.edit_text(text)
            except BadRequest:
                pass

        job_id, extracted_text = await vision_client.extract_text(
            file_name=file_name,
            file_bytes=file_bytes,
            content_type=mime_type,
            language=config.vision_language,
            output_format=config.vision_output_format,
            poll_interval_seconds=config.poll_interval_seconds,
            poll_timeout_seconds=config.poll_timeout_seconds,
            progress_callback=progress_callback,
        )

        if not extracted_text.strip():
            raise RuntimeError("No extractable text was returned from Vision output.")

        chat_id = update.effective_chat.id
        sessions[chat_id] = ChatSession(
            job_id=job_id,
            document_name=file_name,
            text=extracted_text,
            awaiting_question=False,
            updated_at=now_utc_timestamp(),
        )

        await status_message.edit_text(
            f"✅ Document ready: {file_name}\n🆔 Job ID: {job_id}\nChoose an action:",
            reply_markup=get_action_keyboard(),
        )
    except Exception as exc:
        logging.exception("Document processing failed")
        await status_message.edit_text(f"Processing failed: {exc}")


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.message:
        return

    await query.answer()
    config: BotConfig = context.application.bot_data["config"]
    chat_client: SarvamChatClient = context.application.bot_data["chat_client"]
    sessions: dict[int, ChatSession] = context.application.bot_data["sessions"]

    session = get_session_for_chat(
        sessions=sessions,
        chat_id=query.message.chat.id,
        ttl_minutes=config.session_ttl_minutes,
    )
    if not session:
        await query.message.reply_text("No document context found. Upload a PDF/image first.")
        return

    await execute_feature(
        reply_target=query.message,
        session=session,
        feature=query.data or "",
        config=config,
        chat_client=chat_client,
    )


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat
    if not message or not chat or not message.text:
        return

    config: BotConfig = context.application.bot_data["config"]
    chat_client: SarvamChatClient = context.application.bot_data["chat_client"]
    sessions: dict[int, ChatSession] = context.application.bot_data["sessions"]
    session = get_session_for_chat(
        sessions=sessions,
        chat_id=chat.id,
        ttl_minutes=config.session_ttl_minutes,
    )
    if not session or not session.awaiting_question:
        return

    question = message.text.strip()
    if not question:
        await message.reply_text("Send a non-empty question.")
        return

    await execute_feature(
        reply_target=message,
        session=session,
        feature=FEATURE_ASK,
        config=config,
        chat_client=chat_client,
        question=question,
    )


async def feature_command_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    feature: str,
) -> None:
    message = update.effective_message
    chat = update.effective_chat
    if not message or not chat:
        return

    config: BotConfig = context.application.bot_data["config"]
    chat_client: SarvamChatClient = context.application.bot_data["chat_client"]
    sessions: dict[int, ChatSession] = context.application.bot_data["sessions"]
    session = get_session_for_chat(
        sessions=sessions,
        chat_id=chat.id,
        ttl_minutes=config.session_ttl_minutes,
    )
    if not session:
        await message.reply_text("No document context found. Upload a PDF/image first.")
        return

    question = " ".join(context.args).strip() if feature == FEATURE_ASK else None
    await execute_feature(
        reply_target=message,
        session=session,
        feature=feature,
        config=config,
        chat_client=chat_client,
        question=question,
    )


async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    if not update.effective_message:
        return
    await update.effective_message.reply_text(
        "🧭 Quick actions:",
        reply_markup=get_action_keyboard(),
    )


async def ocr_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await feature_command_handler(update, context, FEATURE_OCR)


async def tldr_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await feature_command_handler(update, context, FEATURE_TLDR)


async def keypoints_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await feature_command_handler(update, context, FEATURE_KEY_POINTS)


async def actions_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await feature_command_handler(update, context, FEATURE_ACTION_ITEMS)


async def ask_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await feature_command_handler(update, context, FEATURE_ASK)


async def post_init_handler(application: Application) -> None:
    commands = [
        BotCommand("start", "Start bot"),
        BotCommand("menu", "Show quick action buttons"),
        BotCommand("ocr", "Show complete OCR from latest document"),
        BotCommand("tldr", "Generate TL;DR"),
        BotCommand("keypoints", "Extract key points"),
        BotCommand("actions", "Extract action items"),
        BotCommand("ask", "Ask question on latest document"),
        BotCommand("help", "Show help"),
    ]
    await application.bot.set_my_commands(commands)


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
    application.add_handler(CommandHandler("help", start_handler))
    application.add_handler(CommandHandler("menu", menu_handler))
    application.add_handler(CommandHandler("ocr", ocr_command_handler))
    application.add_handler(CommandHandler("tldr", tldr_command_handler))
    application.add_handler(CommandHandler("keypoints", keypoints_command_handler))
    application.add_handler(CommandHandler("actions", actions_command_handler))
    application.add_handler(CommandHandler("ask", ask_command_handler))
    application.add_handler(CallbackQueryHandler(callback_handler, pattern=r"^feature:"))
    application.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO, document_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    return application


def configure_logging() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )

    root_logger = logging.getLogger()
    redaction_filter = SensitiveDataFilter()
    for handler in root_logger.handlers:
        handler.addFilter(redaction_filter)

    # Suppress per-request transport logs that can include signed URLs/tokens.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def main() -> None:
    load_dotenv()
    configure_logging()

    config = BotConfig.from_env()
    app = build_application(config)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
