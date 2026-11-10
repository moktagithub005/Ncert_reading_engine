# Ingestion stages

The worker records each stage in `ingestion_stage_runs` and updates the job progress field. Processing is dispatched asynchronously after upload validation.

1. Document analysis collects page count, page sizes, and PDF outline entries.
2. Text extraction reads span text and font geometry page by page. Pages with little/no text use Tesseract OCR when enabled.
3. Boilerplate removal drops repeated top/bottom lines and page-number-only lines and records removed samples.
4. Chapter detection prefers outline bookmarks and falls back to printed chapter headings.
5. Topic detection uses number prefixes and font size/boldness. Low confidence and short/long sections appear in the report.
6. Content structuring preserves extracted words and joins wrapped lines. `flatten_blocks` is the canonical text representation.
7. Raster images are converted to WebP and stored by content hash.
8. Chapters, topics, assets, stage summaries, and a quality report are saved for review.

Thresholds and OCR settings are centralized in `app/config.py` and environment variables. Subject-specific regex configuration can be extended there as detectors are tuned against synthetic golden fixtures.
