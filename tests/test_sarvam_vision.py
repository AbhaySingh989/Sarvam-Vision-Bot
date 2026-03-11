import json
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

from bot.clients.sarvam_vision import (
    extract_text_from_output_zip,
    parse_presigned_entry,
    pick_url_entry,
)


def test_pick_url_entry_preserves_single_presigned_entry_dict() -> None:
    payload = {
        "url": "https://example.com/upload",
        "headers": {"X-Test": "1"},
    }

    entry = pick_url_entry(payload, preferred_key=None)

    assert entry == payload


def test_parse_presigned_entry_accepts_file_url_shape() -> None:
    entry = {
        "file_url": "https://example.com/upload",
        "file_metadata": None,
    }

    url, method, headers, fields = parse_presigned_entry(entry, default_method="PUT")

    assert url == "https://example.com/upload"
    assert method == "PUT"
    assert headers == {}
    assert fields is None


def test_extract_text_from_output_zip_prefers_markdown_over_json_metadata() -> None:
    buffer = BytesIO()
    with ZipFile(buffer, mode="w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("result.json", json.dumps({"text": "This should be ignored"}))
        archive.writestr("result.md", "# Offer Letter\n\nCandidate Name: Yagya Dev Meshram")

    extracted = extract_text_from_output_zip(buffer.getvalue())

    assert "Offer Letter" in extracted
    assert "Candidate Name: Yagya Dev Meshram" in extracted
    assert "This should be ignored" not in extracted
