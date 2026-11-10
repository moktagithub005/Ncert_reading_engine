"""Initial NCERT content schema.

Revision ID: 0001_initial
Revises:
"""

from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


UPGRADE_SQL = """
        CREATE TABLE files (
            id UUID PRIMARY KEY,
            storage_key VARCHAR(1024) NOT NULL,
            original_name VARCHAR(512) NOT NULL,
            mime_type VARCHAR(255) NOT NULL,
            size_bytes INTEGER NOT NULL,
            sha256 VARCHAR(64) NOT NULL UNIQUE,
            kind VARCHAR(32) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE TABLE users (
            id UUID PRIMARY KEY,
            email VARCHAR(320) NOT NULL UNIQUE,
            password_hash VARCHAR(255) NOT NULL,
            role VARCHAR(20) NOT NULL,
            is_active BOOLEAN NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX ix_users_email ON users(email);
        CREATE TABLE books (
            id UUID PRIMARY KEY,
            class_no INTEGER NOT NULL CHECK (class_no BETWEEN 1 AND 12),
            subject VARCHAR(120) NOT NULL,
            title VARCHAR(500) NOT NULL,
            language VARCHAR(2) NOT NULL,
            edition VARCHAR(64) NOT NULL,
            status VARCHAR(20) NOT NULL,
            source_file_id UUID REFERENCES files(id) ON DELETE SET NULL,
            published_at TIMESTAMPTZ,
            published_by UUID REFERENCES users(id) ON DELETE SET NULL,
            published_version INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (class_no, subject, language, edition)
        );
        CREATE TABLE review_events (
            id UUID PRIMARY KEY,
            actor_id UUID REFERENCES users(id) ON DELETE SET NULL,
            entity_type VARCHAR(32) NOT NULL,
            entity_id UUID NOT NULL,
            action VARCHAR(24) NOT NULL,
            before JSONB,
            after JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE TABLE chapters (
            id UUID PRIMARY KEY,
            book_id UUID NOT NULL REFERENCES books(id) ON DELETE CASCADE,
            chapter_no INTEGER NOT NULL,
            title VARCHAR(500) NOT NULL,
            start_page INTEGER,
            end_page INTEGER,
            review_status VARCHAR(20) NOT NULL,
            confidence DOUBLE PRECISION NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (book_id, chapter_no)
        );
        CREATE INDEX ix_chapters_book_id ON chapters(book_id);
        CREATE TABLE ingestion_jobs (
            id UUID PRIMARY KEY,
            book_id UUID REFERENCES books(id) ON DELETE SET NULL,
            source_file_id UUID NOT NULL REFERENCES files(id),
            status VARCHAR(20) NOT NULL,
            current_stage VARCHAR(64),
            progress_percent INTEGER NOT NULL,
            started_at TIMESTAMPTZ,
            finished_at TIMESTAMPTZ,
            error_message TEXT,
            params JSONB NOT NULL,
            stats JSONB NOT NULL
        );
        CREATE TABLE topics (
            id UUID PRIMARY KEY,
            chapter_id UUID NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
            parent_id UUID REFERENCES topics(id) ON DELETE CASCADE,
            position INTEGER NOT NULL,
            topic_code VARCHAR(64),
            title VARCHAR(500) NOT NULL,
            content_text TEXT NOT NULL,
            content_blocks JSONB NOT NULL,
            word_count INTEGER NOT NULL,
            page_start INTEGER,
            page_end INTEGER,
            block_type VARCHAR(24) NOT NULL,
            review_status VARCHAR(20) NOT NULL,
            confidence DOUBLE PRECISION NOT NULL,
            content_hash VARCHAR(64) NOT NULL,
            search_vector TSVECTOR GENERATED ALWAYS AS (
                to_tsvector('simple', coalesce(title, '') || ' ' || coalesce(content_text, ''))
            ) STORED NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (chapter_id, position)
        );
        CREATE INDEX ix_topics_search_vector ON topics USING gin(search_vector);
        CREATE INDEX ix_topics_chapter_id ON topics(chapter_id);
        CREATE INDEX ix_topics_ch_parent_position ON topics(chapter_id, parent_id, position);
        CREATE TABLE ingestion_stage_runs (
            id UUID PRIMARY KEY,
            job_id UUID NOT NULL REFERENCES ingestion_jobs(id) ON DELETE CASCADE,
            stage VARCHAR(64) NOT NULL,
            status VARCHAR(20) NOT NULL,
            started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            finished_at TIMESTAMPTZ,
            duration_ms INTEGER,
            output_summary JSONB NOT NULL,
            error_message TEXT
        );
        CREATE INDEX ix_ingestion_stage_runs_job_id ON ingestion_stage_runs(job_id);
        CREATE TABLE assets (
            id UUID PRIMARY KEY,
            book_id UUID NOT NULL REFERENCES books(id) ON DELETE CASCADE,
            chapter_id UUID REFERENCES chapters(id) ON DELETE SET NULL,
            topic_id UUID REFERENCES topics(id) ON DELETE SET NULL,
            kind VARCHAR(32) NOT NULL,
            file_id UUID NOT NULL REFERENCES files(id),
            caption TEXT,
            alt_text TEXT,
            figure_label VARCHAR(120),
            page INTEGER,
            bbox JSONB,
            width INTEGER,
            height INTEGER,
            position INTEGER NOT NULL,
            review_status VARCHAR(20) NOT NULL,
            confidence DOUBLE PRECISION NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX ix_assets_book_id ON assets(book_id);
"""


def upgrade() -> None:
    for statement in UPGRADE_SQL.split(";"):
        if statement.strip():
            op.execute(statement)


DOWNGRADE_SQL = """
        DROP TABLE assets;
        DROP TABLE ingestion_stage_runs;
        DROP TABLE topics;
        DROP TABLE ingestion_jobs;
        DROP TABLE chapters;
        DROP TABLE review_events;
        DROP TABLE books;
        DROP TABLE users;
        DROP TABLE files;
"""


def downgrade() -> None:
    for statement in DOWNGRADE_SQL.split(";"):
        if statement.strip():
            op.execute(statement)
