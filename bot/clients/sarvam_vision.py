import asyncio
import json
import logging
import time
from typing import Any
from io import BytesIO
from zipfile import ZipFile, ZIP_DEFLATED
import re
from pathlib import Path

import httpx
from bot.utils import now_utc_timestamp

VISION_MAX_RETRIES_PER_STRATEGY = 3
VISION_RETRY_DEFAULT_SECONDS = 65
VISION_RETRY_MAX_SECONDS = 180
VISION_STATUS_REPEAT_UPDATE_SECONDS = 20
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg"}

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
        raise RuntimeError(f"Missing url field in presigned entry: {entry}")
    method = (entry.get("method") or default_method).upper()
    headers = entry.get("headers") or {}
    fields = entry.get("fields")
    return url, method, headers, fields

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
