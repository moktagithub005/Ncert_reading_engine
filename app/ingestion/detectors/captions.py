import re

from app.config import get_settings

CAPTION = re.compile(get_settings().figure_caption_regex, re.IGNORECASE)


def detect_caption(text: str) -> tuple[str | None, str | None]:
    match = CAPTION.search(text)
    if not match:
        return None, None
    return match.group(0).replace(" ", ""), text[match.end() :].strip(" :.-") or None
