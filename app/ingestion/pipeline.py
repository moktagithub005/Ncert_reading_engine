import hashlib
import re
import tempfile
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pdfplumber
import pymupdf as fitz
import pytesseract
import structlog
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.metrics import JOB_FAILURES, JOB_STAGE_SECONDS
from app.core.text import sanitize_postgres_value
from app.ingestion.detectors.boilerplate import remove_boilerplate
from app.ingestion.detectors.captions import detect_caption
from app.ingestion.detectors.headings import chapter_heading, topic_heading
from app.ingestion.flatten import flatten_blocks
from app.models import Asset, Book, Chapter, FileRecord, IngestionJob, IngestionStageRun, Topic
from app.storage import get_storage

log = structlog.get_logger()


def _special_block_type(title: str) -> str:
    settings = get_settings()
    patterns = (
        (settings.activity_regex, "activity"),
        (settings.exercise_regex, "exercise"),
        (settings.summary_regex, "summary"),
        (settings.boxed_block_regex, "box"),
    )
    for pattern, block_type in patterns:
        if re.search(pattern, title, re.IGNORECASE):
            return block_type
    return "topic"


class JobCancelled(Exception):
    pass


def _stage(
    db: Session,
    job: IngestionJob,
    name: str,
    progress: int,
    fn: Callable[[], tuple[Any, dict[str, Any]]],
) -> tuple[Any, dict[str, Any]]:
    db.refresh(job)
    if job.status == "cancelled":
        raise JobCancelled("Job was cancelled")
    job.current_stage, job.progress_percent = name, progress
    run = IngestionStageRun(job_id=job.id, stage=name, status="running")
    db.add(run)
    db.commit()
    start = time.monotonic()
    try:
        result, summary = fn()
        result = sanitize_postgres_value(result)
        summary = sanitize_postgres_value(summary)
        run.status, run.output_summary = "succeeded", summary
        run.duration_ms = int((time.monotonic() - start) * 1000)
        run.finished_at = datetime.now(UTC)
        JOB_STAGE_SECONDS.labels(name).observe(run.duration_ms / 1000)
        db.commit()
        log.info("ingestion_stage_succeeded", job_id=str(job.id), stage=name, summary=summary)
        return result, summary
    except Exception as exc:
        db.rollback()
        failed_run = db.get(IngestionStageRun, run.id)
        JOB_FAILURES.labels(name).inc()
        if failed_run:
            failed_run.status, failed_run.error_message = "failed", str(exc)[:4000]
            failed_run.finished_at = datetime.now(UTC)
            failed_run.duration_ms = int((time.monotonic() - start) * 1000)
            JOB_STAGE_SECONDS.labels(name).observe(failed_run.duration_ms / 1000)
            db.commit()
        log.exception("ingestion_stage_failed", job_id=str(job.id), stage=name)
        raise


def run_pipeline(db: Session, job: IngestionJob) -> None:
    settings = get_settings()
    source = db.get(FileRecord, job.source_file_id)
    if source is None:
        raise ValueError("Source file record is missing")
    with tempfile.TemporaryDirectory(prefix="ncert-job-") as temp_dir:
        pdf_path = Path(temp_dir) / "source.pdf"
        with pdf_path.open("wb") as output:
            for chunk in get_storage().open(source.storage_key):
                output.write(chunk)
        # PyMuPDF does not publish complete static types for its Document API.
        doc = cast(Any, fitz.open(pdf_path))  # type: ignore[no-untyped-call]
        params = job.params
        profile, _ = _stage(
            db,
            job,
            "document_analysis",
            5,
            lambda: (
                {
                    "pages": doc.page_count,
                    "toc": doc.get_toc(),
                    "sizes": [(p.rect.width, p.rect.height) for p in doc],
                },
                {"pages": doc.page_count},
            ),
        )
        del profile

        def extract() -> tuple[dict[str, Any], dict[str, Any]]:
            page_lines: list[list[dict[str, Any]]] = []
            scanned = []
            for page_no, page in enumerate(doc, 1):
                db.refresh(job)
                if job.status == "cancelled":
                    raise JobCancelled("Job was cancelled")
                data = page.get_text("dict")
                lines: list[dict[str, Any]] = []
                font_sizes: list[float] = []
                for block in data.get("blocks", []):
                    if block.get("type") != 0:
                        continue
                    for raw_line in block.get("lines", []):
                        spans = raw_line.get("spans", [])
                        text_value = "".join(span.get("text", "") for span in spans).strip()
                        if not text_value:
                            continue
                        size = max((float(span.get("size", 0)) for span in spans), default=0.0)
                        font_sizes.extend(
                            float(s.get("size", 0)) for s in spans if s.get("text", "").strip()
                        )
                        flags = [s.get("flags", 0) for s in spans]
                        bbox = raw_line.get("bbox", [0, 0, page.rect.width, page.rect.height])
                        lines.append(
                            {
                                "text": text_value,
                                "size": size,
                                "bold": any(f & 16 for f in flags),
                                "font": spans[0].get("font", "") if spans else "",
                                "bbox": bbox,
                                "y": bbox[1] / max(page.rect.height, 1),
                                "page": page_no,
                                "source": "pdf",
                                "confidence": 1.0,
                            }
                        )
                if len(" ".join(x["text"] for x in lines)) < settings.text_layer_min_chars:
                    scanned.append(page_no)
                    if settings.ocr_enabled:
                        matrix = fitz.Matrix(300 / 72, 300 / 72)  # type: ignore[no-untyped-call]
                        pix = page.get_pixmap(matrix=matrix, alpha=False)
                        image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                        ocr_data = pytesseract.image_to_data(
                            image,
                            lang=params.get("language") == "hi" and "eng+hin" or settings.ocr_langs,
                            output_type=pytesseract.Output.DICT,
                            timeout=settings.page_timeout_seconds,
                        )
                        buckets: dict[tuple[int, int, int], list[str]] = {}
                        for i, word in enumerate(ocr_data["text"]):
                            if word.strip():
                                key = (
                                    ocr_data["block_num"][i],
                                    ocr_data["par_num"][i],
                                    ocr_data["line_num"][i],
                                )
                                buckets.setdefault(key, []).append(word)
                        for words in buckets.values():
                            lines.append(
                                {
                                    "text": " ".join(words),
                                    "size": 0,
                                    "bold": False,
                                    "font": "ocr",
                                    "bbox": [0, 0, page.rect.width, page.rect.height],
                                    "y": 0.5,
                                    "page": page_no,
                                    "source": "ocr",
                                    "confidence": 0.55,
                                }
                            )
                lines.sort(
                    key=lambda line: (
                        round(line["bbox"][0] / max(page.rect.width, 1) * 2),
                        line["bbox"][1],
                    )
                )
                page_lines.append(lines)
            body_sizes = [line["size"] for page in page_lines for line in page if line["size"] > 0]
            body_size = max(set(body_sizes), key=body_sizes.count) if body_sizes else 0.0
            return {"pages": page_lines, "body_size": body_size, "scanned": scanned}, {
                "ocr_pages": scanned,
                "body_size": body_size,
            }

        extracted, _ = _stage(db, job, "text_extraction", 20, extract)

        def remove_repeated_boilerplate() -> tuple[Any, dict[str, Any]]:
            clean, removed_lines = remove_boilerplate(extracted["pages"])
            return (clean, removed_lines), {"removed_samples": sorted(set(removed_lines))[:30]}

        (clean_pages, removed), _ = _stage(
            db, job, "boilerplate_removal", 30, remove_repeated_boilerplate
        )

        def detect_structure() -> tuple[list[dict[str, Any]], dict[str, Any]]:
            toc = doc.get_toc()
            candidates: list[dict[str, Any]] = []
            for depth, title, page_num in toc:
                if depth == 1:
                    candidates.append(
                        {
                            "no": len(candidates) + 1,
                            "title": title.strip(),
                            "start": max(1, page_num),
                            "confidence": 0.95,
                        }
                    )
            if not candidates:
                for page_no, lines in enumerate(clean_pages, 1):
                    for line_index, line in enumerate(lines):
                        if line.get("y", 0) > settings.chapter_heading_y_threshold:
                            continue
                        found = chapter_heading(line["text"])
                        if found:
                            title = found[1]
                            if title.casefold() == line["text"].strip().casefold():
                                title_lines: list[str] = []
                                for next_line in lines[line_index + 1 :]:
                                    if chapter_heading(next_line["text"]) or topic_heading(
                                        next_line, extracted["body_size"]
                                    ):
                                        break
                                    if next_line.get("size", 0) >= extracted["body_size"] * 1.5:
                                        title_lines.append(next_line["text"].strip())
                                    elif title_lines:
                                        break
                                if title_lines:
                                    title = " ".join(title_lines)
                            candidates.append(
                                {
                                    "no": found[0],
                                    "title": title,
                                    "start": page_no,
                                    "confidence": 0.86 if line["source"] == "pdf" else 0.55,
                                    "heading_y": line.get("y", 0),
                                }
                            )
            if not candidates:
                candidates = [
                    {
                        "no": 1,
                        "title": source.original_name.rsplit(".", 1)[0],
                        "start": 1,
                        "confidence": 0.1,
                    }
                ]
            candidates.sort(key=lambda item: item["start"])
            for i, chapter in enumerate(candidates):
                db.refresh(job)
                if job.status == "cancelled":
                    raise JobCancelled("Job was cancelled")
                chapter["end"] = (
                    candidates[i + 1]["start"] - 1 if i + 1 < len(candidates) else doc.page_count
                )
                chapter_lines = [
                    line
                    for p in range(chapter["start"], chapter["end"] + 1)
                    for line in clean_pages[p - 1]
                    if not (
                        p == chapter["start"]
                        and "heading_y" in chapter
                        and line.get("y", 0) - chapter["heading_y"]
                        <= settings.chapter_heading_y_threshold
                        and (
                            chapter_heading(line["text"])
                            or line.get("size", 0) >= extracted["body_size"] * 1.5
                        )
                    )
                ]
                heading_lines = []
                for line_idx, line in enumerate(chapter_lines):
                    heading = topic_heading(line, extracted["body_size"])
                    if heading:
                        heading_lines.append((line_idx, heading, line))
                topics: list[dict[str, Any]] = []
                if not heading_lines:
                    topics.append(
                        {
                            "title": chapter["title"],
                            "code": None,
                            "level": 1,
                            "confidence": 0.35,
                            "lines": chapter_lines,
                            "page_start": chapter["start"],
                            "page_end": chapter["end"],
                            "block_type": "intro",
                        }
                    )
                else:
                    if heading_lines[0][0] > 0:
                        topics.append(
                            {
                                "title": "Introduction",
                                "code": None,
                                "level": 1,
                                "confidence": 0.35,
                                "lines": chapter_lines[: heading_lines[0][0]],
                                "page_start": chapter["start"],
                                "page_end": chapter["start"],
                                "block_type": "intro",
                            }
                        )
                    for j, (line_index, heading, heading_line) in enumerate(heading_lines):
                        end_index = (
                            heading_lines[j + 1][0]
                            if j + 1 < len(heading_lines)
                            else len(chapter_lines)
                        )
                        title = heading[1]
                        body_lines = chapter_lines[line_index + 1 : end_index]
                        block_type = _special_block_type(title)
                        topics.append(
                            {
                                "title": title,
                                "code": heading[0],
                                "level": heading[2],
                                "confidence": heading[3],
                                "lines": body_lines,
                                "page_start": heading_line["page"],
                                "page_end": body_lines[-1]["page"]
                                if body_lines
                                else heading_line["page"],
                                "block_type": block_type,
                            }
                        )
                chapter["topics"] = topics
                previous_by_level: dict[int, int] = {}
                for topic_index, topic in enumerate(chapter["topics"]):
                    level = topic.get("level", 1)
                    topic["parent_index"] = previous_by_level.get(level - 1) if level > 1 else None
                    topic["level"] = level
                    previous_by_level[level] = topic_index
                    for deeper in [depth for depth in previous_by_level if depth > level]:
                        del previous_by_level[deeper]
            return candidates, {
                "chapters": len(candidates),
                "topics": sum(len(c["topics"]) for c in candidates),
            }

        chapters, structure_stats = _stage(db, job, "chapter_topic_detection", 45, detect_structure)

        def structure_content() -> tuple[list[dict[str, Any]], dict[str, Any]]:
            list_pattern = re.compile(settings.list_item_regex)
            tables_found = 0
            for chapter in chapters:
                for topic in chapter["topics"]:
                    blocks: list[dict[str, Any]] = []
                    paragraph: list[str] = []

                    def flush_paragraph(
                        block_list: list[dict[str, Any]], paragraph_lines: list[str]
                    ) -> None:
                        if paragraph_lines:
                            block_list.append(
                                {"type": "paragraph", "text": " ".join(paragraph_lines)}
                            )
                            paragraph_lines.clear()

                    for line in topic["lines"]:
                        value = line["text"].strip()
                        if not value:
                            continue
                        if list_pattern.match(value):
                            flush_paragraph(blocks, paragraph)
                            item = list_pattern.sub("", value, count=1)
                            if blocks and blocks[-1].get("type") == "list":
                                blocks[-1]["items"].append(item)
                            else:
                                blocks.append({"type": "list", "ordered": False, "items": [item]})
                            continue
                        punctuation = sum(
                            not char.isalnum() and not char.isspace() for char in value
                        )
                        symbol_ratio = punctuation / max(len(value), 1)
                        if (
                            symbol_ratio >= settings.formula_symbol_ratio
                            or "math" in line.get("font", "").lower()
                        ):
                            flush_paragraph(blocks, paragraph)
                            blocks.append(
                                {
                                    "type": "formula",
                                    "text": value,
                                    "latex": None,
                                    "needs_review": True,
                                }
                            )
                            continue
                        if paragraph and paragraph[-1].endswith("-"):
                            paragraph[-1] = paragraph[-1][:-1] + value
                        else:
                            paragraph.append(value)
                    flush_paragraph(blocks, paragraph)
                    if topic["block_type"] in {"activity", "box"}:
                        blocks = [
                            {
                                "type": "activity",
                                "title": topic["title"],
                                "blocks": blocks,
                            }
                        ]
                    topic["blocks"] = blocks
            with pdfplumber.open(pdf_path) as table_pdf:
                for page_number, page in enumerate(table_pdf.pages, 1):
                    extracted_tables = page.extract_tables() or []
                    for rows in extracted_tables:
                        cleaned_rows = [
                            [str(cell or "").strip() for cell in row]
                            for row in rows
                            if any(str(cell or "").strip() for cell in row)
                        ]
                        cell_count = sum(len(row) for row in cleaned_rows)
                        if cell_count < settings.table_min_cells:
                            continue
                        chapter = next(
                            (
                                item
                                for item in chapters
                                if item["start"] <= page_number <= item["end"]
                            ),
                            None,
                        )
                        topic = (
                            next(
                                (
                                    item
                                    for item in chapter["topics"]
                                    if item["page_start"] <= page_number <= item["page_end"]
                                ),
                                None,
                            )
                            if chapter
                            else None
                        )
                        if topic:
                            topic["blocks"].append(
                                {"type": "table", "rows": cleaned_rows, "caption": None}
                            )
                            tables_found += 1
            for chapter in chapters:
                for topic in chapter["topics"]:
                    topic["text"] = flatten_blocks(topic["blocks"])
                    topic["words"] = len(topic["text"].split())
                    topic["hash"] = hashlib.sha256(topic["text"].encode("utf-8")).hexdigest()
                    topic["flags"] = []
                    if topic["words"] < settings.topic_min_words:
                        topic["flags"].append("under_20_words")
                    if topic["words"] > settings.topic_max_words:
                        topic["flags"].append("over_3000_words")
            return chapters, {**structure_stats, "tables_found": tables_found}

        chapters, content_stats = _stage(db, job, "content_structuring", 60, structure_content)
        structure_stats.update(content_stats)

        def extract_assets() -> tuple[list[dict[str, Any]], dict[str, Any]]:
            count = 0
            # Extract page-local images and attach them to the detected topic.
            for page_no, page in enumerate(doc, 1):
                db.refresh(job)
                if job.status == "cancelled":
                    raise JobCancelled("Job was cancelled")
                for image_meta in page.get_images(full=True):
                    xref = image_meta[0]
                    extracted_image = doc.extract_image(xref)
                    image: Image.Image = Image.open(
                        __import__("io").BytesIO(extracted_image["image"])
                    )
                    if image.width < 40 or image.height < 40:
                        continue
                    image = image.convert("RGB")
                    image.thumbnail((1200, 1200))
                    with tempfile.SpooledTemporaryFile() as converted:
                        image.save(converted, format="WEBP", quality=85)
                        converted.seek(0)
                        raw = converted.read()
                    sha = hashlib.sha256(raw).hexdigest()
                    key = f"assets/{sha[:2]}/{sha}.webp"
                    existing = db.scalar(select(FileRecord).where(FileRecord.sha256 == sha))
                    if existing is None:
                        get_storage().put(key, iter([raw]), "image/webp")
                        existing = FileRecord(
                            storage_key=key,
                            original_name=f"{sha}.webp",
                            mime_type="image/webp",
                            size_bytes=len(raw),
                            sha256=sha,
                            kind="image",
                        )
                        db.add(existing)
                        db.flush()
                    ch = next((c for c in chapters if c["start"] <= page_no <= c["end"]), None)
                    topic = (
                        next(
                            (
                                t
                                for t in ch["topics"]
                                if t["page_start"] <= page_no <= t["page_end"]
                            ),
                            None,
                        )
                        if ch
                        else None
                    )
                    if ch and topic:
                        caption_label, caption_text = next(
                            (
                                detect_caption(line["text"])
                                for line in clean_pages[page_no - 1]
                                if detect_caption(line["text"])[0]
                            ),
                            (None, None),
                        )
                        rectangles = page.get_image_rects(xref)
                        rect = rectangles[0] if rectangles else page.rect
                        topic["asset_files"] = topic.get("asset_files", []) + [
                            {
                                "file": existing,
                                "page": page_no,
                                "width": image.width,
                                "height": image.height,
                                "label": caption_label,
                                "caption": caption_text,
                                "bbox": [rect.x0, rect.y0, rect.x1, rect.y1],
                                "y": rect.y0,
                            }
                        ]
                    count += 1
            return chapters, {"image_count": count}

        chapters, asset_stats = _stage(db, job, "image_extraction", 75, extract_assets)
        doc.close()

        # Persistence is idempotent: replace generated rows for this draft on retry.
        book = db.scalar(
            select(Book).where(
                Book.source_file_id == source.id,
                Book.class_no == params["class_no"],
                Book.subject == params["subject"],
                Book.language == params["language"],
                Book.edition == params["edition"],
            )
        )
        if book is None:
            book = Book(
                class_no=params["class_no"],
                subject=params["subject"],
                title=source.original_name.rsplit(".", 1)[0],
                language=params["language"],
                edition=params["edition"],
                source_file_id=source.id,
                status="in_review",
            )
            db.add(book)
            db.flush()
        else:
            db.query(Asset).filter(Asset.book_id == book.id).delete(synchronize_session=False)
            db.query(Chapter).filter(Chapter.book_id == book.id).delete(synchronize_session=False)
            book.status = "in_review"
        for ch_index, data in enumerate(chapters, 1):
            db.refresh(job)
            if job.status == "cancelled":
                raise JobCancelled("Job was cancelled")
            chapter = Chapter(
                book_id=book.id,
                chapter_no=ch_index,
                title=data["title"],
                start_page=data["start"],
                end_page=data["end"],
                review_status="pending",
                confidence=data["confidence"],
            )
            db.add(chapter)
            db.flush()
            topic_ids: dict[int, UUID] = {}
            for position, topic_data in enumerate(data["topics"], 1):
                parent_index = topic_data.get("parent_index")
                parent_id = topic_ids.get(parent_index) if parent_index is not None else None
                topic_blocks = list(topic_data["blocks"])
                topic = Topic(
                    chapter_id=chapter.id,
                    parent_id=parent_id,
                    position=position,
                    topic_code=topic_data["code"],
                    title=topic_data["title"],
                    content_text=topic_data["text"],
                    content_blocks=topic_blocks,
                    word_count=topic_data["words"],
                    page_start=topic_data["page_start"],
                    page_end=topic_data["page_end"],
                    block_type=topic_data["block_type"],
                    review_status="pending",
                    confidence=topic_data["confidence"],
                    content_hash=topic_data["hash"],
                )
                db.add(topic)
                db.flush()
                topic_ids[position - 1] = topic.id
                for asset_data in sorted(
                    topic_data.get("asset_files", []), key=lambda item: item["y"]
                ):
                    asset = Asset(
                        book_id=book.id,
                        chapter_id=chapter.id,
                        topic_id=topic.id,
                        kind="image",
                        file_id=asset_data["file"].id,
                        caption=asset_data["caption"],
                        figure_label=asset_data["label"],
                        page=asset_data["page"],
                        bbox={"rect": asset_data["bbox"]},
                        width=asset_data["width"],
                        height=asset_data["height"],
                        position=len(topic_blocks),
                        review_status="pending",
                        confidence=0.55,
                    )
                    db.add(asset)
                    db.flush()
                    topic_blocks.append(
                        {
                            "type": "figure",
                            "asset_id": str(asset.id),
                            "label": asset_data["label"] or "Figure",
                            "caption": asset_data["caption"],
                        }
                    )
                if len(topic_blocks) != len(topic_data["blocks"]):
                    topic.content_blocks = topic_blocks
                    topic.content_text = flatten_blocks(topic_blocks)
                    topic.word_count = len(topic.content_text.split())
                    topic.content_hash = hashlib.sha256(
                        topic.content_text.encode("utf-8")
                    ).hexdigest()
        job.book_id, job.status, job.current_stage, job.progress_percent = (
            book.id,
            "succeeded",
            "complete",
            100,
        )
        job.finished_at = datetime.now(UTC)
        job.stats = {
            "quality_report": {
                "chapter_count": len(chapters),
                "topic_count": structure_stats["topics"],
                "ocr_pages": extracted["scanned"],
                "removed_boilerplate": removed[:50],
                "table_count": structure_stats["tables_found"],
                "image_count": asset_stats["image_count"],
                "images_without_captions": sum(
                    not asset["caption"]
                    for chapter in chapters
                    for topic in chapter["topics"]
                    for asset in topic.get("asset_files", [])
                ),
                "flagged_formulas": sum(
                    block.get("type") == "formula"
                    for chapter in chapters
                    for topic in chapter["topics"]
                    for block in topic["blocks"]
                ),
                "empty_chapters": sum(not chapter["topics"] for chapter in chapters),
                "numbering_gaps": [
                    {"chapter": chapter["no"], "code": topic["code"]}
                    for chapter in chapters
                    for prior, topic in zip(chapter["topics"], chapter["topics"][1:], strict=False)
                    if prior["code"]
                    and topic["code"]
                    and prior["code"].rsplit(".", 1)[0] == topic["code"].rsplit(".", 1)[0]
                    and topic["code"].count(".") == prior["code"].count(".")
                    and topic["code"].split(".")[-1].isdigit()
                    and prior["code"].split(".")[-1].isdigit()
                    and int(topic["code"].split(".")[-1]) > int(prior["code"].split(".")[-1]) + 1
                ],
                "low_confidence_topics": sum(
                    t["confidence"] < 0.6 for c in chapters for t in c["topics"]
                ),
                "flagged_topics": sum(bool(t["flags"]) for c in chapters for t in c["topics"]),
                "warnings": [
                    "No reliable chapter headings were detected"
                    for chapter in chapters
                    if chapter["confidence"] < 0.5
                ],
            }
        }
        db.commit()
