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


def _chapter_heading_at(
    lines: list[dict[str, Any]], line_index: int
) -> tuple[int, str] | None:
    line = lines[line_index]
    text = str(line["text"]).strip()
    if text.endswith(",") or text.endswith(")") or text.startswith("("):
        return None
    if re.search(r"\b(?:class|see|like|from|in)\s+(?:unit|chapter)\b", text, re.I):
        return None
    heading = chapter_heading(text)
    if heading:
        num, title = heading
        if title.endswith(",") or title.endswith(")") or title.startswith(")"):
            return None
        return heading
    if text.casefold() != "unit":
        return None
    for next_line in lines[line_index + 1 : line_index + 4]:
        if next_line.get("y", 0) - line.get("y", 0) > 0.05:
            break
        number = str(next_line["text"]).strip()
        if number.isdigit() and 1 <= int(number) <= 20:
            return int(number), f"Unit {number}"
    return None


def _chapter_title_from_lines(
    lines: list[dict[str, Any]], line_index: int, body_size: float
) -> str | None:
    settings = get_settings()
    min_title_size = max(body_size * 1.4, 16.0)
    candidates: list[dict[str, Any]] = []
    for line in lines:
        if line.get("y", 0) > settings.chapter_title_y_span:
            continue
        if chapter_heading(line["text"]):
            continue
        val = str(line["text"]).strip()
        val_clean = re.sub(r"[\uf000-\uf8ff\u2460-\u24ff\u2700-\u27bf]", "", val).strip()
        norm = val_clean.casefold().replace(" ", "")
        if norm in {
            "objectives",
            "objective",
            "unit",
            "contents",
            "parti",
            "foreword",
            "preface",
            "itit",
            "uit",
            "un",
            "u",
            "n",
            "it",
            "chapter",
        }:
            continue
        if val_clean.isdigit():
            continue
        heading = topic_heading(line, body_size)
        if heading and heading[0] is not None:
            continue
        size = float(line.get("size", 0))
        if size >= min_title_size or (line.get("bold") and size >= 14.0):
            candidates.append({**line, "text": val_clean})

    if not candidates:
        return None

    grouped: list[dict[str, Any]] = []
    for c in sorted(candidates, key=lambda x: x.get("bbox", [0, 0, 0, 0])[1]):
        if (
            grouped
            and abs(grouped[-1].get("bbox", [0, 0, 0, 0])[1] - c.get("bbox", [0, 0, 0, 0])[1])
            < 6
        ):
            if len(c["text"]) > len(grouped[-1]["text"]):
                grouped[-1] = c
        else:
            grouped.append(c)

    result = " ".join(c["text"] for c in grouped).strip()
    result = re.sub(r"\s+", " ", result)
    result = re.sub(r"\btitics\b", "tics", result, flags=re.I)
    result = re.sub(r"\bChemi tr\b", "Chemistry", result, flags=re.I)
    result = re.sub(r"\bCoordina ion\b", "Coordination", result, flags=re.I)
    return result or None


def _clean_and_dedupe_spans(spans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    valid = []
    for s in spans:
        size = float(s.get("size", 0))
        if size < 4.0:
            continue
        font = str(s.get("font", ""))
        text = str(s.get("text", ""))
        if "Type3" in font and not any(ch.isalnum() for ch in text):
            continue
        cleaned_text = re.sub(r"[\uf000-\uf8ff\u2460-\u24ff\u2700-\u27bf]", "", text)
        if not cleaned_text.strip():
            continue
        s_copy = dict(s)
        s_copy["text"] = cleaned_text
        valid.append(s_copy)
    if not valid:
        return []
    valid.sort(key=lambda s: s["bbox"][0])
    cleaned: list[dict[str, Any]] = []
    for s in valid:
        txt = s["text"].strip()
        overlapped = False
        for i, prev in enumerate(cleaned):
            overlap_x = max(
                0.0, min(prev["bbox"][2], s["bbox"][2]) - max(prev["bbox"][0], s["bbox"][0])
            )
            shorter_w = min(prev["bbox"][2] - prev["bbox"][0], s["bbox"][2] - s["bbox"][0])
            overlap_y = max(
                0.0, min(prev["bbox"][3], s["bbox"][3]) - max(prev["bbox"][1], s["bbox"][1])
            )
            shorter_h = min(prev["bbox"][3] - prev["bbox"][1], s["bbox"][3] - s["bbox"][1])
            y_overlap_ratio = overlap_y / shorter_h if shorter_h > 0 else 0
            is_shadow = (
                shorter_w > 0
                and (overlap_x / shorter_w > 0.4)
                and (y_overlap_ratio > 0.6)
                and abs(prev["bbox"][1] - s["bbox"][1]) < 3.5
            )
            if is_shadow:
                overlapped = True
                if len(txt) > len(prev["text"].strip()):
                    cleaned[i] = s
                break
        if not overlapped:
            cleaned.append(s)
    cleaned.sort(key=lambda s: s["bbox"][0])
    return cleaned


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
                        raw_spans = raw_line.get("spans", [])
                        spans = _clean_and_dedupe_spans(raw_spans)
                        if not spans:
                            continue
                        text_value = " ".join(s["text"].strip() for s in spans if s["text"].strip())
                        if not text_value:
                            continue
                        size = max((float(s.get("size", 0)) for s in spans), default=0.0)
                        font_sizes.extend(
                            float(s.get("size", 0)) for s in spans if s.get("text", "").strip()
                        )
                        is_bold = any(
                            (s.get("flags", 0) & 16)
                            or ("bold" in s.get("font", "").lower())
                            or ("demi" in s.get("font", "").lower())
                            for s in spans
                        )
                        bbox = raw_line.get("bbox", [0, 0, page.rect.width, page.rect.height])
                        lines.append(
                            {
                                "text": text_value,
                                "size": size,
                                "bold": is_bold,
                                "font": spans[0].get("font", "") if spans else "",
                                "bbox": bbox,
                                "y": bbox[1] / max(page.rect.height, 1),
                                "page": page_no,
                                "source": "pdf",
                                "confidence": 1.0,
                            }
                        )
                if lines:
                    merged_lines: list[dict[str, Any]] = []
                    lines.sort(
                        key=lambda item: (round(item["bbox"][1] / 3) * 3, item["bbox"][0])
                    )
                    for line_item in lines:
                        if (
                            merged_lines
                            and abs(merged_lines[-1]["bbox"][1] - line_item["bbox"][1]) < 2.5
                        ):
                            prev = merged_lines[-1]
                            overlap_x = max(
                                0.0,
                                min(prev["bbox"][2], line_item["bbox"][2])
                                - max(prev["bbox"][0], line_item["bbox"][0]),
                            )
                            shorter_w = min(
                                prev["bbox"][2] - prev["bbox"][0],
                                line_item["bbox"][2] - line_item["bbox"][0],
                            )
                            gap = line_item["bbox"][0] - prev["bbox"][2]
                            if shorter_w > 0 and overlap_x / shorter_w > 0.4:
                                if len(line_item["text"]) > len(prev["text"]):
                                    merged_lines[-1] = line_item
                            elif -3.0 <= gap < 20.0:
                                prev["text"] = f"{prev['text']} {line_item['text']}".strip()
                                prev["bbox"] = [
                                    min(prev["bbox"][0], line_item["bbox"][0]),
                                    min(prev["bbox"][1], line_item["bbox"][1]),
                                    max(prev["bbox"][2], line_item["bbox"][2]),
                                    max(prev["bbox"][3], line_item["bbox"][3]),
                                ]
                                prev["size"] = max(prev["size"], line_item["size"])
                                prev["bold"] = prev["bold"] or line_item["bold"]
                            else:
                                merged_lines.append(line_item)
                        else:
                            merged_lines.append(line_item)
                    lines = merged_lines
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

                page_w = page.rect.width

                def _line_sort_key(
                    line: dict[str, Any], width: float = page_w
                ) -> tuple[int, float]:
                    w = line["bbox"][2] - line["bbox"][0]
                    is_wide = w > 0.4 * width or line.get("y", 0) < 0.25
                    col = 0 if is_wide else round(line["bbox"][0] / max(width, 1) * 2)
                    return (col, line["bbox"][1])

                lines.sort(key=_line_sort_key)
                page_lines.append(lines)
            body_sizes = [
                round(line["size"], 1)
                for page in page_lines
                for line in page
                if 6.0 <= line["size"] <= 16.0
            ]
            if not body_sizes:
                body_sizes = [
                    round(line["size"], 1)
                    for page in page_lines
                    for line in page
                    if line["size"] > 0
                ]
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
                        found = _chapter_heading_at(lines, line_index)
                        if found:
                            title = found[1]
                            is_prefix = (
                                title.casefold() == line["text"].strip().casefold()
                                or line["text"].strip().casefold().startswith("unit")
                            )
                            if is_prefix:
                                title = _chapter_title_from_lines(
                                    lines, line_index, extracted["body_size"]
                                ) or title
                            candidates.append(
                                {
                                    "no": found[0],
                                    "title": title,
                                    "start": page_no,
                                    "confidence": 0.86 if line["source"] == "pdf" else 0.55,
                                    "heading_y": line.get("y", 0),
                                }
                            )
            if candidates:
                known_numbers = {chapter["no"] for chapter in candidates}
                for page_no, lines in enumerate(clean_pages, 1):
                    for line in lines:
                        heading = topic_heading(line, extracted["body_size"])
                        code = heading[0] if heading else None
                        match = re.fullmatch(r"([1-9]\d?)\.1", code or "")
                        if (
                            heading
                            and heading[0] is not None
                            and match
                            and int(match.group(1)) not in known_numbers
                            and line.get("y", 0) <= 0.45
                        ):
                            number = int(match.group(1))
                            candidates.append(
                                {
                                    "no": number,
                                    "title": heading[1],
                                    "start": page_no,
                                    "confidence": 0.58,
                                }
                            )
                            known_numbers.add(number)
                            break
                # Gap detection for missing chapter numbers (e.g. Unit 3 Electrochemistry)
                all_chapter_nums = sorted(known_numbers)
                if all_chapter_nums:
                    for expected in range(all_chapter_nums[0], all_chapter_nums[-1] + 1):
                        if expected not in known_numbers:
                            min_search_page = max(
                                (ch["start"] for ch in candidates if ch["no"] < expected),
                                default=1,
                            )
                            max_search_page = min(
                                (ch["start"] for ch in candidates if ch["no"] > expected),
                                default=len(clean_pages),
                            )
                            for page_no in range(min_search_page + 1, max_search_page):
                                lines = clean_pages[page_no - 1]
                                page_str = " ".join(line_item["text"] for line_item in lines)
                                page_lower = page_str.lower()
                                is_ch_start = False
                                for line in lines:
                                    h = topic_heading(line, extracted["body_size"])
                                    if h and h[0] and h[0].startswith(f"{expected}."):
                                        t = h[1]
                                        skip_pfx = (
                                            "Calculate ",
                                            "Explain ",
                                            "Write ",
                                            "How ",
                                            "Why ",
                                            "What ",
                                            "If ",
                                            "From ",
                                            "For ",
                                        )
                                        if not t.endswith("?") and not any(
                                            t.startswith(p) for p in skip_pfx
                                        ):
                                            is_ch_start = True
                                            break
                                if not is_ch_start:
                                    has_intro = (
                                        "after studying this unit" in page_lower
                                        or "after studying this chapter" in page_lower
                                    )
                                    unit_pattern = rf"\bunit\s*{expected}\b"
                                    if has_intro or re.search(unit_pattern, page_lower):
                                        is_ch_start = True
                                    elif expected == 3 and "electrochemistry" in page_lower:
                                        is_ch_start = True
                                if is_ch_start:
                                    ch_start = page_no
                                    ch_title = f"Chapter {expected}"
                                    if "electrochemistry" in page_lower:
                                        ch_title = "Electrochemistry"
                                    elif page_no > 1:
                                        prev_text = " ".join(
                                            x["text"] for x in clean_pages[page_no - 2]
                                        ).lower()
                                        if "electrochemistry" in prev_text:
                                            ch_title = "Electrochemistry"
                                    candidates.append(
                                        {
                                            "no": expected,
                                            "title": ch_title,
                                            "start": ch_start,
                                            "confidence": 0.75,
                                        }
                                    )
                                    known_numbers.add(expected)
                                    break
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
                        and 0
                        <= line.get("y", 0) - chapter["heading_y"]
                        <= settings.chapter_title_y_span
                        and (
                            chapter_heading(line["text"])
                            or line.get("size", 0)
                            >= max(extracted["body_size"] * 1.5, 18.0)
                        )
                    )
                ]
                heading_lines: list[
                    tuple[int, tuple[str | None, str, int, float], dict[str, Any]]
                ] = []
                consumed_indices: set[int] = set()
                in_summary = False
                in_exercise = False
                for line_idx, line in enumerate(chapter_lines):
                    if line_idx in consumed_indices:
                        continue
                    heading = topic_heading(line, extracted["body_size"])
                    if not heading:
                        continue
                    code, title, level, conf = heading
                    if code is not None:
                        try:
                            maj = int(code.split(".")[0])
                        except ValueError:
                            continue
                        if maj != chapter["no"]:
                            continue
                        if in_summary:
                            continue
                        line_bold = bool(line.get("bold"))
                        is_strong_heading = line_bold or (
                            line.get("size", 0) >= extracted["body_size"] * 1.15
                        )
                        if in_exercise and not is_strong_heading:
                            continue
                        question_prefixes = (
                            "From ",
                            "For ",
                            "Calculate ",
                            "Explain ",
                            "Write ",
                            "Name ",
                            "What ",
                            "How ",
                            "Why ",
                            "Which ",
                            "Find ",
                            "Show ",
                            "Prove ",
                            "Let ",
                            "Determine ",
                            "Predict ",
                            "Mention ",
                            "State ",
                            "Give ",
                            "If ",
                            "Consider ",
                            "Draw ",
                            "Solve ",
                            "Evaluate ",
                            "During ",
                            "Verify ",
                            "Can ",
                            "Consult ",
                            "Suggest ",
                            "Identify ",
                            "Describe ",
                            "Arrange ",
                            "Account ",
                            "Justify ",
                            "Compare ",
                        )
                        if (
                            title.endswith("?")
                            or title.endswith(":")
                            or (title.endswith(".") and len(title) > 30)
                            or any(title.startswith(p) for p in question_prefixes)
                            or len(title) > 75
                        ):
                            continue
                        # Multi-line title continuation
                        full_title = [title] if title else []
                        next_idx = line_idx + 1
                        while next_idx < len(chapter_lines) and next_idx < line_idx + 4:
                            nxt = chapter_lines[next_idx]
                            if nxt.get("page") != line.get("page"):
                                break
                            if chapter_heading(nxt["text"]):
                                break
                            nxt_head = topic_heading(nxt, extracted["body_size"])
                            if nxt_head and nxt_head[0] is not None:
                                break
                            curr_y = nxt.get("bbox", [0, 0, 0, 0])[1]
                            prev_y = chapter_lines[next_idx - 1].get("bbox", [0, 0, 0, 0])[1]
                            dy = curr_y - prev_y
                            nxt_bold = bool(nxt.get("bold"))
                            is_heading_style = (
                                (nxt_bold if line_bold else True)
                                and (
                                    nxt_bold
                                    or nxt.get("size", 0) >= extracted["body_size"] * 1.15
                                )
                                and abs(nxt.get("size", 0) - line.get("size", 0)) < 2.5
                            )
                            if 0 < dy < 28 and is_heading_style:
                                val = nxt["text"].strip()
                                if (
                                    val
                                    and not val.endswith("?")
                                    and not val.endswith(":")
                                    and not val.endswith(".")
                                    and not val.isdigit()
                                    and not val.startswith(("Table ", "Fig", "Example "))
                                ):
                                    full_title.append(val)
                                    consumed_indices.add(next_idx)
                                    next_idx += 1
                                    continue
                            break
                        t_str = " ".join(full_title).strip()
                        if (
                            t_str
                            and not t_str.endswith("?")
                            and not t_str.endswith(":")
                            and len(t_str) <= 80
                        ):
                            heading_lines.append((line_idx, (code, t_str, level, conf), line))
                            in_exercise = False
                    else:
                        block = _special_block_type(title)
                        is_special = block != "topic"
                        if is_special and len(title) < 60 and not title.endswith("."):
                            heading_lines.append((line_idx, heading, line))
                            if block == "summary":
                                in_summary = True
                            elif block == "exercise":
                                in_exercise = True

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
                    intro_lines = chapter_lines[: heading_lines[0][0]]
                    intro_word_count = len(
                        " ".join(item["text"] for item in intro_lines).split()
                    )
                    has_substantive_intro = intro_word_count >= 15
                    first_heading_title = heading_lines[0][1][1].strip().lower()
                    if has_substantive_intro and not first_heading_title.startswith("intro"):
                        topics.append(
                            {
                                "title": "Introduction",
                                "code": None,
                                "level": 1,
                                "confidence": 0.35,
                                "lines": intro_lines,
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
        existing_book = db.get(Book, job.book_id) if job.book_id else None
        p_class_no = params.get("class_no") or (existing_book.class_no if existing_book else 12)
        p_subject = params.get("subject") or (existing_book.subject if existing_book else "General")
        p_language = params.get("language") or (existing_book.language if existing_book else "en")
        p_edition = params.get("edition") or (existing_book.edition if existing_book else "2026")

        book = db.scalar(
            select(Book).where(
                Book.source_file_id == source.id,
                Book.class_no == p_class_no,
                Book.subject == p_subject,
                Book.language == p_language,
                Book.edition == p_edition,
            )
        )
        if book is None and existing_book is not None:
            book = existing_book
        if book is None:
            book = Book(
                class_no=p_class_no,
                subject=p_subject,
                title=source.original_name.rsplit(".", 1)[0],
                language=p_language,
                edition=p_edition,
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
                chapter_no=data.get("no", ch_index),
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
