from typing import Any


def remove_nul_characters(value: str) -> str:
    """Remove NULs that PostgreSQL text and JSONB values cannot represent."""
    return value.replace("\x00", "")


def sanitize_postgres_value(value: Any) -> Any:
    """Recursively remove NULs from values that will be stored in PostgreSQL."""
    if isinstance(value, str):
        return remove_nul_characters(value)
    if isinstance(value, dict):
        return {
            sanitize_postgres_value(key): sanitize_postgres_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [sanitize_postgres_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(sanitize_postgres_value(item) for item in value)
    return value
