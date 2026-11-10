from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog
from botocore.exceptions import BotoCoreError
from redis.exceptions import RedisError
from sqlalchemy.exc import OperationalError

from app.db.session import SessionLocal
from app.models import IngestionJob
from app.workers.celery_app import celery_app

log = structlog.get_logger()


@celery_app.task(bind=True, max_retries=5)  # type: ignore[untyped-decorator]
def process_book(self: Any, job_id: str) -> None:
    """Pipeline entry point; stage implementations persist progress and can be resumed."""
    db = SessionLocal()
    job = db.get(IngestionJob, UUID(job_id))
    if job is None or job.status == "cancelled":
        db.close()
        return
    job.status, job.current_stage, job.progress_percent = "running", "document_analysis", 1
    job.started_at = datetime.now(UTC)
    db.commit()
    log.info("ingestion_started", job_id=job_id, stage="document_analysis")
    # The complete stage orchestration is attached by app.ingestion.pipeline.
    try:
        from app.ingestion.pipeline import run_pipeline

        run_pipeline(db, job)
    except Exception as exc:
        db.rollback()
        job = db.get(IngestionJob, UUID(job_id))
        from app.ingestion.pipeline import JobCancelled

        if job and isinstance(exc, JobCancelled):
            job.status, job.current_stage, job.finished_at = (
                "cancelled",
                "cancelled",
                datetime.now(UTC),
            )
            db.commit()
        elif isinstance(
            exc, (OperationalError, RedisError, BotoCoreError, TimeoutError, ConnectionError)
        ):
            if self.request.retries < self.max_retries:
                if job:
                    job.status = "queued"
                    job.error_message = str(exc)[:4000]
                    db.commit()
                countdown = min(300, 2 ** (self.request.retries + 1))
                raise self.retry(exc=exc, countdown=countdown, max_retries=5) from exc
            if job:
                job.status, job.error_message, job.finished_at = (
                    "failed",
                    str(exc)[:4000],
                    datetime.now(UTC),
                )
                db.commit()
        elif job:
            job.status, job.error_message, job.finished_at = (
                "failed",
                str(exc)[:4000],
                datetime.now(UTC),
            )
            db.commit()
        log.exception("ingestion_failed", job_id=job_id, stage=job.current_stage if job else None)
        raise
    finally:
        db.close()
