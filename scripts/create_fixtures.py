"""Generate synthetic, non-NCERT PDFs that exercise common textbook layouts."""

import json
from pathlib import Path

import pymupdf as fitz

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
HINDI_FONTS = (
    Path("/System/Library/Fonts/Supplemental/Devanagari Sangam MN.ttc"),
    Path("/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf"),
)


def add_page(document: fitz.Document, entries: list[tuple[float, float, str, float, bool]]) -> None:
    page = document.new_page(width=595, height=842)
    for x, y, text, size, bold in entries:
        page.insert_text((x, y), text, fontsize=size, fontname="hebo" if bold else "helv")


def save_single_page(name: str, entries: list[tuple[float, float, str, float, bool]]) -> None:
    document = fitz.open()
    add_page(document, entries)
    document.save(FIXTURES / f"{name}.pdf")
    document.close()


def make_hindi() -> None:
    document = fitz.open()
    page = document.new_page(width=595, height=842)
    font = next((path for path in HINDI_FONTS if path.exists()), None)
    if font is None:
        raise RuntimeError("Set up a Devanagari font before generating the Hindi fixture")
    page.insert_font(fontname="hindi", fontfile=str(font))
    page.insert_text((60, 90), "अध्याय 2 प्रकाश और छाया", fontname="hindi", fontsize=19)
    page.insert_text((60, 145), "2.1 प्रकाश के स्रोत", fontname="hindi", fontsize=15)
    page.insert_textbox(
        fitz.Rect(60, 165, 530, 390),
        "प्रकाश हमें वस्तुओं को देखने में सहायता करता है। सूर्य प्राकृतिक प्रकाश का एक स्रोत है। "
        "कुछ वस्तुएँ स्वयं प्रकाश उत्पन्न करती हैं और कुछ वस्तुएँ प्रकाश को परावर्तित करती हैं।",
        fontname="hindi",
        fontsize=12,
    )
    document.save(FIXTURES / "hindi_chapter.pdf")
    document.close()


def make_scanned() -> None:
    source = fitz.open()
    page = source.new_page(width=595, height=842)
    page.insert_text((60, 90), "Chapter 1 Plants", fontsize=20)
    page.insert_text((60, 145), "1.1 Parts of a plant", fontsize=15)
    page.insert_textbox(
        fitz.Rect(60, 165, 530, 420),
        "Plants have roots, stems, and leaves. Roots hold the plant in soil. Leaves use sunlight "
        "to make food. These parts work together as the plant grows.",
        fontsize=12,
    )
    pixels = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
    scanned = fitz.open()
    output = scanned.new_page(width=595, height=842)
    output.insert_image(output.rect, stream=pixels.tobytes("png"))
    scanned.save(FIXTURES / "scanned_page.pdf")
    source.close()
    scanned.close()


def main() -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    save_single_page(
        "science_chapter",
        [
            (60, 90, "Chapter 1 Materials Around Us", 20, True),
            (60, 145, "1.1 What are materials?", 15, True),
            (
                60,
                175,
                "Objects around us are made from different materials. A material has properties",
                12,
                False,
            ),
            (60, 195, "that help us decide how it can be used in everyday life.", 12, False),
            (60, 250, "1.2 Grouping materials", 15, True),
            (
                60,
                280,
                "We can group materials by comparing their observable properties. A useful group",
                12,
                False,
            ),
            (
                60,
                300,
                "makes it easier to describe and select materials for a particular purpose.",
                12,
                False,
            ),
        ],
    )
    save_single_page(
        "maths_chapter",
        [
            (60, 90, "Chapter 4 Fractions", 20, True),
            (60, 145, "4.1 Equivalent fractions", 15, True),
            (
                60,
                175,
                "Equivalent fractions describe the same part of a whole. Multiplying the",
                12,
                False,
            ),
            (
                60,
                195,
                "numerator and denominator by the same number preserves the value.",
                12,
                False,
            ),
            (60, 240, "a/b = (a x n)/(b x n)", 14, False),
            (60, 290, "4.2 Comparing fractions", 15, True),
            (
                60,
                320,
                "Fractions with a common denominator can be compared by their numerators.",
                12,
                False,
            ),
        ],
    )
    make_hindi()
    make_scanned()
    save_single_page(
        "two_column_chapter",
        [
            (60, 90, "Chapter 2 Living Things", 20, True),
            (60, 145, "2.1 Needs of living things", 15, True),
            (
                60,
                180,
                "Living things need water and air. They grow and change over time.",
                12,
                False,
            ),
            (60, 200, "Their surroundings provide the resources needed for survival.", 12, False),
            (310, 180, "Plants and animals respond to changes in their environment.", 12, False),
            (310, 200, "Observing these changes helps us understand life around us.", 12, False),
        ],
    )
    expected = {
        "science_chapter": {"chapters": 1, "topics": ["What are materials?", "Grouping materials"]},
        "maths_chapter": {"chapters": 1, "topics": ["Equivalent fractions", "Comparing fractions"]},
        "hindi_chapter": {"chapters": 1, "topics": ["प्रकाश के स्रोत"]},
        "scanned_page": {"chapters": 1, "topics": ["Parts of a plant"], "ocr_pages": [1]},
        "two_column_chapter": {"chapters": 1, "topics": ["Needs of living things"]},
    }
    (FIXTURES / "expected.json").write_text(
        json.dumps(expected, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
