# Data model

```mermaid
erDiagram
  FILES ||--o{ BOOKS : source
  BOOKS ||--o{ CHAPTERS : contains
  CHAPTERS ||--o{ TOPICS : contains
  TOPICS ||--o{ TOPICS : nests
  BOOKS ||--o{ ASSETS : owns
  FILES ||--o{ ASSETS : stores
  BOOKS ||--o{ INGESTION_JOBS : processes
  FILES ||--o{ INGESTION_JOBS : source
  INGESTION_JOBS ||--o{ INGESTION_STAGE_RUNS : records
  USERS ||--o{ REVIEW_EVENTS : authors
```

Books are edition-specific publication units. Chapters and nested topics hold exact extracted text, ordered JSON content blocks, review state, confidence, and hashes. Assets point to content-addressed files. Ingestion jobs and stage runs provide resumability and diagnostics. Review events retain before/after audit snapshots.
