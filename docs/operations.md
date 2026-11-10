# Operations

## Backups and restores

Run the optional daily backup service with `docker compose --profile operations up -d backup`; it writes custom-format dumps to the `backups` volume. Copy dumps off-host and test restores on a schedule using `pg_restore --clean --if-exists`. Back up object storage separately; database backups alone do not include source PDFs or images.

## Scaling and stuck jobs

Scale the worker service independently. Celery late acknowledgements allow jobs to return to the queue after worker loss. Inspect job status and stage-run rows before retrying via the admin API. Parse failures should be corrected or reprocessed with adjusted options rather than blindly retried.

## Public API load check

With the Compose stack running, use `k6 run -e BASE_URL=http://localhost:8000 tests/load/public_api.js`. The script drives 200 readers for 30 seconds, checks class-list cache headers and conditional `304` responses, and applies p95 targets of 150 ms uncached and 30 ms cached. Run it against a test deployment with representative published content before release.

## Security

Set unique secrets through the deployment environment, restrict MinIO and database ports, configure `CORS_ORIGINS`, and terminate TLS at a trusted proxy. Workers run as a non-root user. Source PDFs must remain behind controlled storage access.
