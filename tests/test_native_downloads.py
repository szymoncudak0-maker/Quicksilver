"""Regression for large ZIP downloads: native browser link + streamed FileResponse."""
from pathlib import Path
import time

from fastapi.testclient import TestClient

import app_online


def test_unique_names_for_batch_downloads(tmp_path):
    folder = tmp_path / "Quicksilver_2026"
    batch1 = folder / "paczka_001"
    batch2 = folder / "paczka_002"
    batch1.mkdir(parents=True)
    batch2.mkdir(parents=True)
    assert app_online.asset_filename(batch1 / "zdjecia.zip", folder) == "zdjecia_paczka_001.zip"
    assert app_online.asset_filename(batch2 / "zdjecia.zip", folder) == "zdjecia_paczka_002.zip"
    assert app_online.asset_filename(batch1 / "wynik.xlsx", folder) == "wynik_paczka_001.xlsx"
    assert app_online.asset_filename(folder / "wynik.xlsx", folder) == "wynik.xlsx"


def test_download_endpoints_stream_with_auth_and_unique_names(tmp_path, monkeypatch):
    sid = "streaming-test-session"
    folder = tmp_path / "Quicksilver"
    batch = folder / "paczka_001"
    batch.mkdir(parents=True)
    asset = batch / "zdjecia.zip"
    asset.write_bytes(b"part-of-zip")
    bundle = tmp_path / "Quicksilver_wyniki.zip"
    bundle.write_bytes(b"full-zip")
    monkeypatch.setattr(app_online, "SESSIONS", {
        sid: {"created": time.time(), "csrf": "some-csrf-token"}
    })
    monkeypatch.setattr(app_online, "JOBS", {
        "photo-job": {
            "sid": sid, "state": "complete", "bundle": bundle,
            "files": [asset], "artifacts": [
                {"index": 0, "filename": "zdjecia_paczka_001.zip"}
            ],
        }
    })

    with TestClient(app_online.app, base_url="https://testserver") as client:
        # The handler must enforce authentication even for a native link download.
        assert client.get("/api/jobs/photo-job/download").status_code == 401
        assert client.get("/api/jobs/photo-job/assets/0").status_code == 401

        client.cookies.set(app_online.COOKIE_NAME, sid, path="/")
        response = client.get("/api/jobs/photo-job/assets/0")
        assert response.status_code == 200
        assert response.content == b"part-of-zip"
        assert "zdjecia_paczka_001.zip" in response.headers["content-disposition"]
        assert response.headers["content-length"] == str(len(b"part-of-zip"))

        response = client.get("/api/jobs/photo-job/download")
        assert response.status_code == 200
        assert response.content == b"full-zip"
        assert "Quicksilver_wyniki.zip" in response.headers["content-disposition"]


def test_photo_download_javascript_does_not_buffer_file(tmp_path):
    js = (Path(__file__).resolve().parents[1] / "static" / "app.js").read_text(encoding="utf-8")
    assert "function browserDownload(" in js
    assert "response.blob()" not in js
    assert "'/assets/'+index" in js
    assert "+'/download'" in js
