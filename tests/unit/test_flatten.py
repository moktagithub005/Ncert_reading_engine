from app.ingestion.flatten import flatten_blocks


def test_flatten_preserves_order_and_structure_markers() -> None:
    blocks = [
        {"type": "heading", "level": 2, "text": "A heading"},
        {"type": "paragraph", "text": "A paragraph."},
        {"type": "formula", "text": "v = u + at", "needs_review": True},
        {"type": "list", "ordered": False, "items": ["one", "two"]},
        {"type": "table", "rows": [["a", "b"], ["1", "2"]]},
        {"type": "figure", "label": "Fig. 2.1", "caption": "A sample"},
        {
            "type": "activity",
            "title": "Activity 2.1",
            "blocks": [{"type": "paragraph", "text": "Try this."}],
        },
    ]

    assert flatten_blocks(blocks) == (
        "A heading\n\nA paragraph.\n\nv = u + at\n\none\ntwo\n\na | b\n1 | 2\n\n"
        "[Figure: Fig. 2.1 - A sample]\n\nActivity 2.1\n\nTry this."
    )


def test_flatten_ignores_empty_text_blocks() -> None:
    assert flatten_blocks([{"type": "paragraph", "text": " "}, {"type": "unknown"}]) == ""
