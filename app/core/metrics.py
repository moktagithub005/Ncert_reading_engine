from prometheus_client import Counter, Histogram

HTTP_REQUESTS = Counter("ncert_http_requests_total", "HTTP requests", ["method", "path", "status"])
HTTP_LATENCY = Histogram("ncert_http_request_seconds", "HTTP request latency", ["method", "path"])
CACHE_HITS = Counter("ncert_cache_hits_total", "Cache lookups", ["result"])
JOB_STAGE_SECONDS = Histogram("ncert_job_stage_seconds", "Ingestion stage duration", ["stage"])
JOB_FAILURES = Counter("ncert_job_failures_total", "Failed ingestion jobs", ["stage"])
