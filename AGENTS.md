# Working rules
- Treat `NCERT_PROJECT_SPEC.md` as the product specification; follow its milestone order.
- Never invent textbook content. Use only supplied PDFs or synthetic layout fixtures.
- Keep extraction exact, preserve Hindi UTF-8, and make detector thresholds configurable.
- Keep HTTP handlers thin and business logic in services or ingestion stages.
- Do not build tutor, RAG, vector, embedding, student account, or frontend features in this phase.
- Add schema changes through Alembic and keep ingestion stages deterministic and resumable.
