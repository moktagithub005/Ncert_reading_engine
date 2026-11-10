import re
from typing import Any

from app.config import get_settings

NUMBERED = re.compile(get_settings().numbered_heading_regex)
CHAPTER = re.compile(get_settings().chapter_heading_regex, re.IGNORECASE)
CHAPTER_NUMBERS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
}


def chapter_heading(text: str) -> tuple[int, str] | None:
    match = CHAPTER.match(text)
    if not match:
        return None
    number_text = match.group(1).casefold()
    number = int(number_text) if number_text.isdigit() else CHAPTER_NUMBERS[number_text]
    return number, match.group(2).strip() or text.strip()


def topic_heading(
    line: dict[str, Any], body_size: float
) -> tuple[str | None, str, int, float] | None:
    value = str(line["text"]).strip()
    if chapter_heading(value) is not None:
        return None
    if len(value) > get_settings().heading_max_chars or len(value) < 2:
        return None
    match = NUMBERED.match(value)
    if match:
        code, title = match.group(1), match.group(2)
        return (
            code,
            title,
            code.count(".") + 1,
            min(
                0.98,
                0.72
                + (0.08 if line.get("bold") else 0)
                + (0.1 if line.get("size", 0) > body_size else 0),
            ),
        )
    if re.search(get_settings().special_block_regex, value, re.IGNORECASE) and not value.endswith(
        "."
    ):
        return None, value, 1, 0.62
    if line.get("bold") and line.get("size", 0) >= body_size * 1.12:
        return None, value, 1, 0.55
    return None
