import re
from collections import Counter
from typing import Any

from app.config import get_settings

PAGE_NUMBER = re.compile(get_settings().page_number_regex, re.IGNORECASE)


def remove_boilerplate(
    pages: list[list[dict[str, Any]]],
) -> tuple[list[list[dict[str, Any]]], list[str]]:
    """Remove page numbers and repeated top/bottom lines at stable vertical positions."""
    counts: Counter[tuple[str, str]] = Counter()
    page_count = max(len(pages), 1)
    for lines in pages:
        for line in lines:
            y = line.get("y", 0.5)
            if y < 0.12 or y > 0.88:
                counts[(str(line["text"]).strip(), "top" if y < 0.5 else "bottom")] += 1
    threshold = max(2, int(page_count * get_settings().boilerplate_repeat_ratio))
    common = {text for (text, _), count in counts.items() if count >= threshold and text}
    removed: list[str] = []
    clean = []
    for lines in pages:
        keep = []
        for line in lines:
            value = str(line["text"]).strip()
            if PAGE_NUMBER.match(value) or value in common:
                removed.append(value)
            else:
                keep.append(line)
        clean.append(keep)
    return clean, sorted(set(removed))
