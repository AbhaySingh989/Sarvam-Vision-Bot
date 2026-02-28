import re
from dataclasses import dataclass
from typing import Optional

@dataclass
class PageArtifact:
    page_number: int
    text: str

@dataclass
class DocumentArtifact:
    full_text: str
    pages: list[PageArtifact]

def parse_ocr_to_artifact(text: str) -> DocumentArtifact:
    """
    Splits the given OCR text into pages based on standard pagination markers.
    If no markers are found, chunks the text by character limit.
    """
    # Regex to find standard Markdown headers denoting a page, like:
    # ---- Page 1 ----
    # ==== Page 2 ====
    # Page 3

    # Simple split heuristic
    page_splits = re.split(r'(?i)^\s*(?:[-=]{3,}\s*)?page\s+(\d+)\s*(?:[-=]{3,})?\s*$', text, flags=re.MULTILINE)

    pages = []

    if len(page_splits) > 1:
        # First element is preamble (before first page)
        preamble = page_splits[0].strip()
        if preamble:
            pages.append(PageArtifact(page_number=0, text=preamble))

        for i in range(1, len(page_splits), 2):
            page_num_str = page_splits[i]
            page_content = page_splits[i+1].strip() if i+1 < len(page_splits) else ""

            try:
                page_number = int(page_num_str)
            except ValueError:
                page_number = len(pages) + 1

            pages.append(PageArtifact(page_number=page_number, text=page_content))
    else:
        # Fallback: Treat the whole document as a single page or chunk it if needed
        # For our use cases, treating as 1 page is usually fine for short docs,
        # but chunking by size might be needed for very large docs without page markers.
        # Let's chunk every 3000 chars roughly as a fallback "page" for mapping.

        CHUNK_SIZE = 3000
        paragraphs = text.split('\n\n')
        current_page_text = ""
        current_page_num = 1

        for para in paragraphs:
            if len(current_page_text) + len(para) > CHUNK_SIZE and current_page_text:
                pages.append(PageArtifact(page_number=current_page_num, text=current_page_text.strip()))
                current_page_num += 1
                current_page_text = para
            else:
                current_page_text += "\n\n" + para if current_page_text else para

        if current_page_text:
            pages.append(PageArtifact(page_number=current_page_num, text=current_page_text.strip()))

    return DocumentArtifact(full_text=text, pages=pages)
