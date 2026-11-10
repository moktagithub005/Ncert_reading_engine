from typing import Any


def flatten_blocks(blocks: list[dict[str, Any]]) -> str:
    """Return the single canonical plain-text representation of structured blocks."""
    parts: list[str] = []
    for block in blocks:
        kind = block.get("type")
        if kind in {"paragraph", "heading", "formula"}:
            text = str(block.get("text", "")).strip()
            if text:
                parts.append(text)
        elif kind == "list":
            items = [str(item).strip() for item in block.get("items", []) if str(item).strip()]
            if items:
                parts.append("\n".join(items))
        elif kind == "table":
            rows = [" | ".join(str(cell) for cell in row) for row in block.get("rows", [])]
            if rows:
                parts.append("\n".join(rows))
        elif kind == "figure":
            label, caption = block.get("label", "Figure"), block.get("caption", "")
            parts.append(f"[Figure: {label} - {caption}]".rstrip(" -"))
        elif kind == "activity":
            title = str(block.get("title", "")).strip()
            nested = flatten_blocks(block.get("blocks", []))
            parts.extend(item for item in (title, nested) if item)
    return "\n\n".join(parts)
