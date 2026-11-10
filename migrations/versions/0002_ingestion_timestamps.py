"""Track ingestion job and stage-run creation and update times."""

from alembic import op

revision = "0002_ingestion_timestamps"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE ingestion_jobs ADD COLUMN created_at TIMESTAMPTZ NOT NULL DEFAULT now()"
    )
    op.execute(
        "ALTER TABLE ingestion_jobs ADD COLUMN updated_at TIMESTAMPTZ NOT NULL DEFAULT now()"
    )
    op.execute(
        "ALTER TABLE ingestion_stage_runs ADD COLUMN created_at TIMESTAMPTZ NOT NULL DEFAULT now()"
    )
    op.execute(
        "ALTER TABLE ingestion_stage_runs ADD COLUMN updated_at TIMESTAMPTZ NOT NULL DEFAULT now()"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE ingestion_stage_runs DROP COLUMN updated_at")
    op.execute("ALTER TABLE ingestion_stage_runs DROP COLUMN created_at")
    op.execute("ALTER TABLE ingestion_jobs DROP COLUMN updated_at")
    op.execute("ALTER TABLE ingestion_jobs DROP COLUMN created_at")
