import json
from pathlib import Path

from app.main import app


def test_openapi_schema_matches_reviewed_snapshot() -> None:
    snapshot_path = Path(__file__).parents[1] / "fixtures" / "openapi.snapshot.json"
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))

    assert app.openapi() == snapshot
