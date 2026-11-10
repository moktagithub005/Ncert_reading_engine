import json
from pathlib import Path

import pymupdf as fitz
import pytesseract
from PIL import Image

from app.ingestion.detectors.headings import chapter_heading, topic_heading

FIXTURES = Path(__file__).parents[1] / "fixtures"


def _lines(path: Path) -> list[dict]:
    document = fitz.open(path)
    lines = []
    for page in document:
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                spans = line.get("spans", [])
                text = "".join(span["text"] for span in spans).strip()
                if text:
                    lines.append(
                        {
                            "text": text,
                            "size": max(span["size"] for span in spans),
                            "bold": any(span["flags"] & 16 for span in spans),
                        }
                    )
    document.close()
    return lines


def test_golden_text_fixtures_detect_expected_chapter_and_topics() -> None:
    expected = json.loads((FIXTURES / "expected.json").read_text(encoding="utf-8"))
    for fixture in ("science_chapter", "maths_chapter", "hindi_chapter", "two_column_chapter"):
        lines = _lines(FIXTURES / f"{fixture}.pdf")
        assert any(chapter_heading(line["text"]) for line in lines)
        detected = [
            heading[1] for line in lines if (heading := topic_heading(line, 12)) is not None
        ]
        assert detected == expected[fixture]["topics"]


def test_scanned_golden_fixture_ocr_extracts_heading_and_text() -> None:
    document = fitz.open(FIXTURES / "scanned_page.pdf")
    page = document[0]
    assert not page.get_text().strip()
    pixmap = page.get_pixmap(matrix=fitz.Matrix(300 / 72, 300 / 72), alpha=False)
    image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
    text = pytesseract.image_to_string(image, lang="eng", timeout=30)
    document.close()
    assert "Plants" in text
    assert "roots" in text.lower()
