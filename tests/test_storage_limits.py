"""Storage limits for large photo batches."""
from pathlib import Path
from types import SimpleNamespace

from app_online import should_create_bundle
from engine import max_output_bytes


def test_default_output_limit_is_5gb(monkeypatch):
    monkeypatch.delenv("QS_MAX_OUTPUT_MB", raising=False)
    assert max_output_bytes() == 5_000_000_000
    monkeypatch.setenv("QS_MAX_OUTPUT_MB", "1500")
    assert max_output_bytes() == 1_500_000_000


def test_large_zip_job_avoids_duplicate_download_bundle(tmp_path, monkeypatch):
    import app_online
    (tmp_path / "zdjecia.zip").write_bytes(b"x" * 128)
    monkeypatch.setattr(app_online, "MAX_BUNDLE_BYTES", 100)
    assert should_create_bundle(tmp_path, SimpleNamespace(package_type="zip")) is False
    assert should_create_bundle(tmp_path, SimpleNamespace(package_type="folder")) is True
    monkeypatch.setattr(app_online, "MAX_BUNDLE_BYTES", 200)
    assert should_create_bundle(tmp_path, SimpleNamespace(package_type="zip")) is True
