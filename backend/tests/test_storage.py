from app.config import settings
from app.services import s3 as s3_store
from app.services.storage import LocalStorageService, S3StorageService, get_storage_service


def test_local_output_path():
    svc = LocalStorageService()
    p = svc.output_path("abc123")
    assert p.name == "abc123.mp4"
    assert "output" in str(p)


def test_local_output_url():
    assert LocalStorageService().output_url("abc123") == "/api/jobs/abc123/output"


def test_storage_backend_selection(monkeypatch):
    monkeypatch.setattr(settings, "storage_backend", "local")
    assert isinstance(get_storage_service(), LocalStorageService)
    monkeypatch.setattr(settings, "storage_backend", "s3")
    assert isinstance(get_storage_service(), S3StorageService)
    monkeypatch.setattr(settings, "storage_backend", "bogus")
    try:
        get_storage_service()
        raise AssertionError("should have raised")
    except ValueError:
        pass


def test_s3_output_url_falls_back_to_api_path(monkeypatch):
    svc = S3StorageService()
    monkeypatch.setattr(settings, "storage_backend", "s3")
    monkeypatch.setattr(s3_store, "presigned_get_url", lambda *args, **kwargs: None)
    assert svc.output_url("abc123") == "/api/jobs/abc123/output"