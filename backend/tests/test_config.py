import os

from app.config import Settings


def test_r2_resolution_defaults():
    s = Settings(_env_file=None, s3_provider="r2")
    assert s.s3_endpoint_resolved == "https://<account_id>.r2.cloudflarestorage.com"
    assert s.s3_region_resolved == "auto"
    assert s.s3_addressing_style_resolved == "virtual"


def test_b2_resolution_defaults():
    s = Settings(_env_file=None, s3_provider="b2")
    assert s.s3_endpoint_resolved == "https://s3.eu-central-003.backblazeb2.com"
    assert s.s3_region_resolved == "eu-central-003"
    assert s.s3_addressing_style_resolved == "path"


def test_explicit_values_win():
    s = Settings(
        _env_file=None,
        s3_provider="custom",
        s3_endpoint="https://example.com",
        s3_region="us-east-1",
        s3_addressing_style="path",
    )
    assert s.s3_endpoint_resolved == "https://example.com"
    assert s.s3_region_resolved == "us-east-1"
    assert s.s3_addressing_style_resolved == "path"


def test_env_vars_override():
    os.environ["S3_PROVIDER"] = "r2"
    os.environ["S3_ENDPOINT"] = "https://x.r2.cloudflarestorage.com"
    try:
        s = Settings(_env_file=None)
        assert s.s3_provider == "r2"
        assert s.s3_endpoint_resolved == "https://x.r2.cloudflarestorage.com"
    finally:
        os.environ.pop("S3_PROVIDER", None)
        os.environ.pop("S3_ENDPOINT", None)


def test_defaults_are_local_dev():
    s = Settings(_env_file=None)
    assert s.mongodb_uri == "mongodb://localhost:27017"
    assert s.storage_backend == "local"
    assert s.s3_addressing_style == ""