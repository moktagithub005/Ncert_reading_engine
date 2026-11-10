# API

Interactive OpenAPI documentation is served at `/docs`; the contract is `/openapi.json`.

## Upload and review

Authenticate at `POST /api/v1/admin/auth/login`. Upload `multipart/form-data` to `POST /api/v1/admin/books/upload` with `file`, `class_no`, `subject`, `language`, and `edition`. Poll `GET /api/v1/admin/jobs/{job_id}` and inspect `GET /api/v1/admin/jobs/{job_id}/report`. Review the tree at `GET /api/v1/admin/books/{book_id}/tree`, patch chapters/topics, and approve every item. Publishing is admin-only and validates the full book.

## Public reads

`GET /api/v1/classes`, `/classes/{class_no}/subjects`, `/books/{book_id}`, `/books/{book_id}/chapters`, `/chapters/{chapter_id}/topics`, and `/topics/{topic_id}` expose published content. Send `If-None-Match` to receive `304` when content is unchanged. `GET /api/v1/assets/{asset_id}` redirects to controlled storage access.
