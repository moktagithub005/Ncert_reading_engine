# NCERT Content Platform & Reading Engine

Backend engine for converting complete NCERT textbook PDFs (Classes 1–12 across Physics, Chemistry, Mathematics, Science, and Humanities in English and Hindi) into reviewable, structured content hierarchies, and publishing approved content through a high-performance, read-only public API.

---

## 📑 Table of Contents
- [Project Overview & Scope](#-project-overview--scope)
- [Complete Technology Stack](#-complete-technology-stack)
- [Default Credentials & Service URLs](#-default-credentials--service-urls)
- [System Architecture & Ingestion Pipeline](#-system-architecture--ingestion-pipeline)
- [Data Model & Entities](#-data-model--entities)
- [API Flow & Endpoints](#-api-flow--endpoints)
- [Quick Start Guide](#-quick-start-guide)
- [Testing & Quality Assurance](#-testing--quality-assurance)
- [Operations & Backups](#-operations--backups)
- [Developer & AI Agent Guidelines](#-developer--ai-agent-guidelines)

---

## 🎯 Project Overview & Scope

The NCERT Reading Engine is an automated textbook digitization, extraction, and content-management backend:
* **Ingests Raw PDFs**: Parses large, multi-hundred-page NCERT textbook PDFs with complex multi-column layouts, mixed fonts, drop shadows, Hindi UTF-8 Unicode, and vector/Type3 glyphs.
* **Decomposes Structure**: Extracts precise book, chapter, and nested topic hierarchies (e.g. `1.1`, `1.2`, `10.5.1`) without swallowing body paragraphs into titles or picking up in-text exercise questions.
* **Audit & Review Workflow**: Human-in-the-loop review console where reviewers inspect extracted chapters/topics, check confidence scores, make edits, and approve content.
* **Content Delivery API**: High-throughput public read-only API with Redis caching, ETag/`If-None-Match` conditional requests (`304 Not Modified`), and asset delivery.

### 🚫 Strict Scope Boundaries (Phase 1)
To keep the backend deterministic, fast, and focused on clean content extraction, the following are **strictly excluded**:
* ❌ NO AI tutor features or chatbots.
* ❌ NO RAG (Retrieval-Augmented Generation) or vector embeddings.
* ❌ NO student account management or authentication.
* ❌ NO student-facing web or mobile frontend (only the internal admin/reviewer test console).

---

## 🛠 Complete Technology Stack

| Component | Technology | Version | Purpose |
| :--- | :--- | :--- | :--- |
| **Language** | Python | 3.12 | Core backend language |
| **API Framework** | FastAPI | 0.115+ | High-performance async web framework |
| **Server** | Uvicorn | 0.32+ | ASGI web server |
| **Settings & Validation** | Pydantic & Pydantic-Settings | v2 | Data validation, serialization, and environment configuration |
| **Primary Database** | PostgreSQL | 16 (Alpine) | Relational store for books, chapters, topics, assets, and audit logs |
| **Database Driver** | Psycopg (v3) | 3.2+ | Async and binary-compatible PostgreSQL driver |
| **ORM** | SQLAlchemy | 2.0+ | Declarative models and session management |
| **Schema Migrations** | Alembic | 1.13+ | Version-controlled database schema migrations |
| **Task Queue / Worker** | Celery | 5.4+ | Asynchronous job processing for long-running PDF ingestion stages |
| **Message Broker & Cache** | Redis | 7 (Alpine) | Celery task broker, result backend, and public API response cache |
| **Object Storage** | MinIO | Latest | S3-compatible object store for source PDFs and extracted WebP image assets |
| **PDF Extraction** | PyMuPDF (`fitz`) | 1.24+ | Fast text span extraction, font metrics, block bboxes, and image extraction |
| **OCR Engine** | Tesseract OCR (`pytesseract`) | 5.x | Fallback OCR for scanned pages and Type3 symbol fonts (`eng+hin` models) |
| **Image Processing** | Pillow (PIL) | 10.x | Conversion of extracted figures/diagrams to modern WebP format |
| **Logging** | Structlog | 24.4+ | Structured JSON logging for request tracking and worker diagnostics |
| **Metrics & Monitoring** | Prometheus Client | 0.21+ | Prometheus metrics endpoint (`/metrics`) for latency, jobs, and stages |
| **Containerization** | Docker & Docker Compose | Compose v2 | Multi-container development and deployment environment |
| **Code Quality** | Ruff, Mypy, Pytest | Latest | Strict linting, type-checking (PEP 484), and unit/integration test suite |

---

## 🔑 Default Credentials & Service URLs

### 🌐 Service Endpoints

| Service | Localhost URL | Notes |
| :--- | :--- | :--- |
| **Test & Review Console UI** | [http://localhost:8000/test-ui](http://localhost:8000/test-ui) or [http://localhost:8000/](http://localhost:8000/) | Browser console to upload PDFs, view ingestion jobs, and review chapter/topic trees |
| **Interactive Swagger API Docs** | [http://localhost:8000/docs](http://localhost:8000/docs) | Interactive OpenAPI documentation to test all endpoints |
| **ReDoc API Documentation** | [http://localhost:8000/redoc](http://localhost:8000/redoc) | Clean alternate API specification |
| **MinIO Storage Console** | [http://localhost:9001](http://localhost:9001) | Web interface to browse MinIO buckets (`ncert`) and files |
| **MinIO S3 API Endpoint** | `http://localhost:9000` | S3 API endpoint consumed by the backend |
| **Prometheus Metrics** | [http://localhost:8000/metrics](http://localhost:8000/metrics) | Prometheus-formatted engine and HTTP metrics |
| **Health Check** | [http://localhost:8000/health](http://localhost:8000/health) | Engine liveness and readiness probe |

### 🔐 Default Credentials

| Resource | Username / Email | Password | Access / Role |
| :--- | :--- | :--- | :--- |
| **Admin API & Test UI** | `ncert-admin@example.com` | `Admin@12345678` | System Administrator (`admin` role) |
| **Secondary Admin** | `integration-admin@example.com` | `Admin@12345678` | Integration Administrator |
| **MinIO Object Store** | `minioadmin` | `minioadmin` | S3 Root User (`S3_ACCESS_KEY` / `S3_SECRET_KEY`) |
| **PostgreSQL Database** | `ncert` | `ncert` | Database: `ncert` (Port `5432`) |
| **Redis** | *No password by default* | *None* | Database index: `0` (Port `6379`) |

---

## ⚙️ System Architecture & Ingestion Pipeline

PDF processing runs in the background via Celery workers across 6 deterministic, resumable stages. Progress is updated after each stage and recorded in the `ingestion_stage_runs` table:

```mermaid
flowchart TD
    A["Raw PDF Upload (Admin API)"] --> B["Storage: S3/MinIO (Content Addressed)"]
    B --> C["Celery Worker: Ingestion Job"]
    C --> S1["1. document_analysis (Pages, Sizes, Outline/TOC)"]
    S1 --> S2["2. text_extraction (Spans, Fonts, Drop-shadow deduplication, OCR fallback)"]
    S2 --> S3["3. boilerplate_removal (Headers, Footers, Page numbers)"]
    S3 --> S4["4. chapter_topic_detection (Chapter discovery, Gap detection, Topic hierarchy)"]
    S4 --> S5["5. content_structuring (Block flattening, Tables, Wrapped text joining)"]
    S5 --> S6["6. image_extraction (Diagrams to WebP, SHA-256 deduplication)"]
    S6 --> D["Database Persistence (Book, Chapters, Topics, Assets in_review)"]
```

### Detailed Stage Breakdown:

1. **`document_analysis` (Progress: 5%)**:
   - Analyzes total page counts, page dimensions, and extracts native PDF bookmarks (TOC outline).
2. **`text_extraction` (Progress: 20%)**:
   - Extracts character spans with exact font sizes, font weights (`bold`, `demi`), and bounding boxes.
   - **Drop-Shadow Deduplication**: Collapses duplicate font-shadow offset spans (0.2 pt offsets).
   - **Horizontal Reading Order**: Spans within a line are strictly sorted horizontally by x-coordinate (`bbox[0]`) to ensure correct digit/word ordering (`Chapter 1` vs `1 Chapter`).
   - **OCR Fallback**: Pages with fewer than 30 text characters (e.g., scanned pages or pages using Type3 vector glyphs) trigger Tesseract OCR with `eng+hin`.
3. **`boilerplate_removal` (Progress: 30%)**:
   - Detects and eliminates repeated top running headers (e.g. `Physics · Class XII`), bottom running footers, and standalone page numbers.
4. **`chapter_topic_detection` (Progress: 45%)**:
   - Identifies chapter starts via bookmarks or regex patterns supporting English and Hindi (`Chapter 1`, `Unit 1`, `अध्याय 1`, `इकाई 1`).
   - **Missing Chapter Gap Detection**: Bounded search between preceding and subsequent chapter boundaries to detect chapters encoded in vector/Type3 glyphs (e.g., Chemistry Unit 3 *Electrochemistry*).
   - **Topic Numbering Hierarchy**: Captures dotted hierarchy codes (`1.1`, `1.2`, `10.5.1`).
   - **Heading Continuation Consistency**: Enforces font weight and size consistency (`nxt_bold if line_bold else True`) so that normal body text following a topic heading is never swallowed into the title.
   - **Question Filtering**: Filters out in-text questions (`Calculate...`, `Explain...`, `How would you...`, items ending in `?` or `:`) from being misclassified as topics.
5. **`content_structuring` (Progress: 60%)**:
   - Normalizes text into structured, ordered content blocks (`paragraph`, `table`, `activity`, `box`). Wrapped lines are joined while preserving exact textbook terminology.
6. **`image_extraction` (Progress: 75% -> 100%)**:
   - Extracts figures, diagrams, and illustrations, converts them to WebP format, deduplicates them using SHA-256 hashes, and uploads them to MinIO under `assets/{hash}.webp`.

---

## 🗄 Data Model & Entities

```mermaid
erDiagram
    FileRecord ||--o{ Book : "source_file"
    Book ||--o{ Chapter : "contains"
    Chapter ||--o{ Topic : "contains"
    Topic ||--o{ Topic : "nests (parent_id)"
    Book ||--o{ Asset : "owns"
    FileRecord ||--o{ Asset : "stores"
    Book ||--o{ IngestionJob : "processes"
    FileRecord ||--o{ IngestionJob : "source"
    IngestionJob ||--o{ IngestionStageRun : "records"
    User ||--o{ ReviewEvent : "authors"
```

* **`FileRecord`**: Stores raw uploaded PDF files and asset metadata with SHA-256 hash, byte size, MIME type, and storage key.
* **`Book`**: Represents an edition-specific textbook (`class_no`, `subject`, `title`, `language`, `edition`, `status`). Status transitions: `draft` -> `in_review` -> `published` -> `archived`.
* **`Chapter`**: Sequential chapters belonging to a book (`chapter_no`, `title`, `start_page`, `end_page`, `confidence`, `review_status`).
* **`Topic`**: Hierarchical topics under a chapter (`topic_code`, `title`, `position`, `content_text`, `content_blocks`, `parent_id`).
* **`Asset`**: Extracted diagrams, figures, and illustrations linked to books/topics.
* **`IngestionJob` & `IngestionStageRun`**: Tracks asynchronous background pipeline execution with progress percentage, stage timings, and summaries.
* **`User`**: Admin and reviewer accounts with hashed passwords and role-based permissions (`admin`, `reviewer`).
* **`ReviewEvent`**: Audit trail recording before/after state snapshots when reviewers modify or approve content.

---

## 🚀 API Flow & Endpoints

### 🔒 Admin & Ingestion Endpoints (`/api/v1/admin`)

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `POST` | `/api/v1/admin/auth/login` | Authenticate with email/password to obtain JWT access & refresh tokens |
| `POST` | `/api/v1/admin/auth/refresh` | Exchange refresh token for a new access token |
| `POST` | `/api/v1/admin/books/upload` | Upload PDF file (`multipart/form-data`) with `class_no`, `subject`, `language`, `edition` |
| `GET` | `/api/v1/admin/jobs/{job_id}` | Poll background ingestion progress and stage statuses |
| `GET` | `/api/v1/admin/jobs/{job_id}/report` | Retrieve extraction quality report (confidence scores, issues) |
| `GET` | `/api/v1/admin/books/{book_id}/tree` | Get full hierarchy tree of chapters and topics for review |
| `PATCH`| `/api/v1/admin/chapters/{chapter_id}`| Update chapter title, page numbers, or review status |
| `PATCH`| `/api/v1/admin/topics/{topic_id}` | Update topic code, title, content text, or blocks |
| `POST` | `/api/v1/admin/books/{book_id}/publish` | Validate that all chapters/topics are approved and publish the book |
| `POST` | `/api/v1/admin/books/{book_id}/unpublish`| Revert published book back to `in_review` status |

### 📖 Public Read Endpoints (`/api/v1`)

*All public endpoints only return data for books with `status = "published"`.*

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/api/v1/classes` | List all classes containing published textbooks |
| `GET` | `/api/v1/classes/{class_no}/subjects` | List subjects available for a given class number |
| `GET` | `/api/v1/books/{book_id}` | Retrieve book metadata |
| `GET` | `/api/v1/books/{book_id}/chapters` | List chapters under a published book |
| `GET` | `/api/v1/chapters/{chapter_id}/topics`| List topics under a chapter |
| `GET` | `/api/v1/topics/{topic_id}` | Retrieve complete topic content text and structured blocks |
| `GET` | `/api/v1/assets/{asset_id}` | Stream or redirect to extracted image assets |

*Note: Public endpoints support HTTP caching headers (`ETag`, `Cache-Control`) and respond with `304 Not Modified` when an `If-None-Match` request header matches the current version.*

---

## 🏁 Quick Start Guide

### 1. Prerequisites
* [Docker](https://docs.docker.com/get-docker/) & [Docker Compose](https://docs.docker.com/compose/)
* [Python 3.12](https://www.python.org/) (optional, for local development outside Docker)
* [uv](https://docs.astral.sh/uv/) or `pip`

### 2. Environment Setup
```bash
# Clone the repository
git clone https://github.com/moktagithub005/Ncert_reading_engine.git
cd Ncert_reading_engine

# Copy environment template
cp .env.example .env
```

### 3. Launch Services with Docker Compose
```bash
docker compose up --build -d
```
This starts:
* `api` on `http://localhost:8000`
* `worker` (Celery background worker)
* `postgres` on `localhost:5432`
* `redis` on `localhost:6379`
* `minio` on `localhost:9000` (API) & `localhost:9001` (Web Console)

### 4. Apply Database Migrations
```bash
docker compose exec api alembic upgrade head
```

### 5. Create the Initial Admin User
```bash
docker compose exec api python scripts/create_admin.py --email ncert-admin@example.com --password Admin@12345678
```

### 6. Open the Test & Review UI
Navigate to [http://localhost:8000/test-ui](http://localhost:8000/test-ui):
1. Sign in with `ncert-admin@example.com` / `Admin@12345678`.
2. Upload any NCERT PDF (Class 12 Maths, Chemistry, Physics, etc.).
3. Watch the real-time job progress bar.
4. Select the book from the dropdown to browse its chapters and clean topic trees.

---

## 🧪 Testing & Quality Assurance

### Run Unit Tests
```bash
# Using uv (fastest)
uv run pytest tests/unit

# Or directly inside the docker container
docker compose exec api pytest tests/unit
```

### Run Linter & Type Checker
```bash
# Check code style and rules
uv run ruff check app tests

# Run strict static type checking
uv run mypy app
```

### Run Integration Tests with Coverage Gate (>=80%)
```bash
DATABASE_URL=postgresql+psycopg://ncert:ncert@localhost:5432/ncert \
REDIS_URL=redis://localhost:6379/0 \
STORAGE_BACKEND=local \
NCERT_INTEGRATION_TESTS=1 \
uv run pytest --cov=app --cov-fail-under=80
```

---

## 🛡 Operations & Backups

### Automated PostgreSQL Dumps
Run the daily automated backup container:
```bash
docker compose --profile operations up -d backup
```
Backups are written to the `backups` volume. To restore a backup:
```bash
docker compose exec postgres pg_restore -U ncert -d ncert --clean --if-exists /backups/latest.dump
```

### Scaling Background Workers
To scale Celery workers to handle heavy ingestion loads:
```bash
docker compose up -d --scale worker=4
```

---

## 🤖 Developer & AI Agent Guidelines

When modifying this repository, follow these core principles:
1. **Never Invent Textbook Content**: The extraction must be exact and truthful to the supplied NCERT PDF.
2. **Preserve Multilingual Unicode**: Preserve Hindi UTF-8 and Devnagari characters during all extraction and regex operations.
3. **Thin HTTP Handlers**: Keep API route controllers thin; place business logic in service modules (`app/services/`) or pipeline stages (`app/ingestion/`).
4. **Deterministic & Resumable Pipeline**: Ingestion stages must be idempotent and resumable if interrupted.
5. **Schema Changes via Alembic**: Never modify the database schema directly; always generate an Alembic migration (`alembic revision --autogenerate -m "..."`).
6. **Configurable Thresholds**: Keep detector thresholds, font tolerances, and regex patterns centralized in `app/config.py`.
7. **Scope Discipline**: Do not add tutor, chatbot, RAG, vector embedding, or student frontend features to this codebase.
