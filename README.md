# NCERT Content Platform

Backend for converting complete NCERT textbook PDFs into reviewable structured content and publishing approved content through a read-only API.

## Quick start

1. Copy `.env.example` to `.env`; set a long random `JWT_SECRET` and MinIO credentials.
2. Run `docker compose up --build`.
3. In another shell, run `docker compose exec api alembic upgrade head`.
4. Create the first admin with `docker compose exec api python scripts/create_admin.py --email admin@example.org --password 'use-a-long-password'`.
5. Open `http://localhost:8000/docs`. Log in at `/api/v1/admin/auth/login`, then upload a PDF to `/api/v1/admin/books/upload` with class, subject, language, and edition fields.
6. Poll the returned job, review the generated tree, correct and approve every item, then publish.

The local Compose setup uses PostgreSQL 16, Redis, and MinIO. Set `STORAGE_BACKEND=local` to use local disk instead. Source PDFs are private; only assets belonging to published books can be fetched through the public API.

## Development

Install `pip install -e '.[dev]'`, apply migrations with `alembic upgrade head`, then use `ruff check .`, `mypy app`, and `pytest --cov=app`. To run the Postgres/Redis-backed upload-to-publish and golden-PDF flows (required for the 80% coverage gate), start Docker Compose, apply migrations, and run `DATABASE_URL=postgresql+psycopg://ncert:ncert@localhost:5432/ncert REDIS_URL=redis://localhost:6379/0 STORAGE_BACKEND=local NCERT_INTEGRATION_TESTS=1 pytest --cov=app --cov-fail-under=80`. Tesseract with English and Hindi language data must be installed for the scanned-page fixture. Synthetic PDF fixtures can be generated with scripts in `scripts/`. The API OpenAPI document is available at `/openapi.json`.

## API flow

Admin routes live under `/api/v1/admin`; published read routes live under `/api/v1`. A reviewer can edit and approve. Only an admin can publish/unpublish or import content. Public routes expose published books only and use versioned Redis response keys, ETags, and immutable asset URLs.

## Operations

Run `docker compose --profile operations up -d backup` for daily dumps to the `backups` volume; copy them off-host and periodically restore them to a separate database. Scale workers independently with Celery worker replicas. Review `/metrics`, worker JSON logs, and `ingestion_stage_runs` for job diagnostics.

## Scope

Tutor, student accounts, RAG, embeddings, and a student frontend are intentionally excluded. See `NCERT_PROJECT_SPEC.md` for the full product specification.

# Ncert_reading_engine
