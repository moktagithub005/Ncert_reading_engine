import app.storage as storage_module
from app.storage.local import LocalStorage


def test_local_storage_round_trip_and_key_safety(tmp_path) -> None:
    storage = LocalStorage(str(tmp_path))
    size = storage.put(
        "assets/ab/example.bin", iter([b"hello", b" world"]), "application/octet-stream"
    )
    assert size == 11
    assert b"".join(storage.open("assets/ab/example.bin")) == b"hello world"
    try:
        storage.put("../outside", iter([b"x"]), "text/plain")
    except ValueError:
        pass
    else:
        raise AssertionError("path traversal key should be rejected")


def test_storage_factory_selects_s3_backend(monkeypatch) -> None:
    backend = object()
    monkeypatch.setattr(
        storage_module, "get_settings", lambda: type("Settings", (), {"storage_backend": "s3"})()
    )
    monkeypatch.setattr(storage_module, "S3Storage", lambda: backend)

    assert storage_module.get_storage() is backend
