import pytest

from app.config import get_settings
from app.ingestion.detectors.boilerplate import remove_boilerplate
from app.ingestion.detectors.captions import detect_caption
from app.ingestion.detectors.headings import chapter_heading, topic_heading


def test_chapter_detector_handles_english_and_hindi() -> None:
    assert chapter_heading("Chapter 3: Atoms") == (3, "Atoms")
    assert chapter_heading("अध्याय 4 प्रकाश") == (4, "प्रकाश")
    assert chapter_heading("1 Chapter") == (1, "1 Chapter")
    assert chapter_heading("1 Chapter: Relations") == (1, "Relations")
    assert chapter_heading("A normal sentence") is None


def test_topic_heading_uses_numbering_and_font_strength() -> None:
    numbered = topic_heading({"text": "3.2 Motion", "bold": False, "size": 13}, 10)
    assert numbered == ("3.2", "Motion", 2, 0.82)
    assert topic_heading({"text": "An ordinary line", "bold": False, "size": 10}, 10) is None
    assert topic_heading({"text": "2.87", "bold": False, "size": 10}, 10) is None
    assert topic_heading({"text": "3.2 (c)] Electrolytic", "bold": False, "size": 10}, 10) is None


def test_topic_heading_covers_length_special_block_and_bold_rules() -> None:
    assert chapter_heading("Chapter 2") == (2, "Chapter 2")
    assert topic_heading({"text": "Chapter 2: Matter", "bold": True, "size": 18}, 10) is None
    assert topic_heading({"text": "X", "bold": True, "size": 18}, 10) is None
    too_long = "A" * (get_settings().heading_max_chars + 1)
    assert topic_heading({"text": too_long, "bold": True, "size": 18}, 10) is None
    assert topic_heading({"text": "Activity 3.1", "bold": False, "size": 10}, 10) == (
        None,
        "Activity 3.1",
        1,
        0.62,
    )
    assert topic_heading({"text": "Keywords.", "bold": False, "size": 10}, 10) is None
    assert topic_heading({"text": "Key ideas", "bold": True, "size": 12}, 10) == (
        None,
        "Key ideas",
        1,
        0.55,
    )
    numbered = topic_heading({"text": "3.1 Strong", "bold": True, "size": 15}, 10)
    assert numbered is not None
    assert numbered[:3] == ("3.1", "Strong", 2)
    assert numbered[3] == pytest.approx(0.9)


def test_caption_detector_returns_label_and_caption() -> None:
    assert detect_caption("Fig. 3.1: A simple machine") == ("Fig.3.1", "A simple machine")
    assert detect_caption("No caption here") == (None, None)


def test_boilerplate_removal_drops_repeated_lines_and_page_numbers() -> None:
    pages = [
        [
            {"text": "Science · Class 8", "y": 0.04},
            {"text": "Body one", "y": 0.5},
            {"text": "1", "y": 0.97},
        ],
        [
            {"text": "Science · Class 8", "y": 0.04},
            {"text": "Body two", "y": 0.5},
            {"text": "2", "y": 0.97},
        ],
    ]
    clean, removed = remove_boilerplate(pages)
    assert [[line["text"] for line in page] for page in clean] == [["Body one"], ["Body two"]]
    assert "Science · Class 8" in removed
