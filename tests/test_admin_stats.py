"""Access control and aggregate counters for the private admin dashboard."""
import time

from fastapi.testclient import TestClient

import app_online
from usage_stats import UsageStats, date_for_timestamp


def test_usage_store_is_idempotent_and_aggregated(tmp_path):
    stats = UsageStats(database_url="", db_path=str(tmp_path / "usage.db"))
    day = "2026-11-17"
    stats.record("u:1", day, "upload", "received", bytes_total=123)
    stats.record("p:1", day, "photo", "started")
    stats.record("p:1", day, "photo", "complete",
                 rows_processed=12, photos=17, products=8, errors=1, bytes_total=5_000_000)
    stats.record("p:1", day, "photo", "complete",
                 rows_processed=12, photos=17, products=8, errors=1, bytes_total=5_000_000)
    stats.record("p:2", day, "photo", "failed", errors=3)
    stats.record("s:1", day, "settlement", "complete", rows_processed=25)
    result = stats.overview()
    assert result["events"] == 4
    assert result["uploads"] == 1
    assert result["photo_jobs"] == 2
    assert result["completed"] == 1
    assert result["failed"] == 1
    assert result["settlements"] == 1
    assert result["photos"] == 17
    assert result["products"] == 8
    assert result["errors"] == 4
    assert result["photo_bytes"] == 5_000_000
    assert result["persistent"] is False
    assert stats.daily() == [{
        "date": day, "uploads": 1, "photo_jobs": 2, "completed": 1,
        "failed": 1, "settlements": 1, "photos": 17,
        "products": 8, "errors": 4, "photo_bytes": 5_000_000
    }]


def test_admin_routes_require_separate_password_and_csrf(tmp_path, monkeypatch):
    monkeypatch.setattr(app_online, "PASSWORD", "common-password-1234567")
    monkeypatch.setattr(app_online, "ADMIN_PASSWORD", "private-admin-password-123456789")
    monkeypatch.setattr(app_online, "STATS", UsageStats(database_url="", db_path=str(tmp_path / "admin.db")))
    app_online.ADMIN_ATTEMPTS.clear()
    app_online.SESSIONS.clear()

    with TestClient(app_online.app, base_url="https://testserver") as client:
        assert client.get("/api/admin/stats").status_code == 401
        assert client.get("/api/admin/export").status_code == 401
        resp = client.post("/api/login", json={"password": "common-password-1234567"})
        assert resp.status_code == 200
        csrf = client.get("/api/bootstrap").json()["key"]
        assert client.get("/api/admin/stats").status_code == 403
        assert client.get("/api/admin/export").status_code == 403
        assert client.post("/api/admin/login", json={"password": "private-admin-password-123456789"}).status_code == 403
        wrong = client.post("/api/admin/login", json={"password": "incorrect"},
                            headers={"X-CSRF-Token": csrf})
        assert wrong.status_code == 401
        ok = client.post("/api/admin/login", json={"password": "private-admin-password-123456789"},
                         headers={"X-CSRF-Token": csrf})
        assert ok.status_code == 200
        assert client.get("/api/admin/status").json()["unlocked"] is True
        assert client.get("/api/admin/stats").status_code == 200
        assert client.get("/api/admin/export").status_code == 200
        assert client.post("/api/admin/logout", json={}, headers={"X-CSRF-Token": csrf}).status_code == 200
        assert client.get("/api/admin/stats").status_code == 403


def test_admin_sessions_expire_and_disabled_config_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(app_online, "PASSWORD", "common-password-1234567")
    monkeypatch.setattr(app_online, "ADMIN_PASSWORD", "common-password-1234567")
    assert app_online.admin_configured() is False
    monkeypatch.setattr(app_online, "ADMIN_PASSWORD", "different-admin-password-1234")
    assert app_online.admin_configured() is True
    monkeypatch.setattr(app_online, "STATS", UsageStats(database_url="", db_path=str(tmp_path / "admin.db")))
    app_online.ADMIN_ATTEMPTS.clear()
    app_online.SESSIONS.clear()
    with TestClient(app_online.app, base_url="https://testserver") as client:
        assert client.post("/api/login", json={"password": app_online.PASSWORD}).status_code == 200
        csrf = client.get("/api/bootstrap").json()["key"]
        assert client.post("/api/admin/login",
                           json={"password": "different-admin-password-1234"},
                           headers={"X-CSRF-Token": csrf}).status_code == 200
        sid = next(iter(app_online.SESSIONS))
        app_online.SESSIONS[sid]["admin_since"] = time.time() - app_online.ADMIN_SESSION_LIFETIME - 1
        assert client.get("/api/admin/stats").status_code == 403


def test_date_is_utc():
    assert date_for_timestamp(0) == "1970-01-01"
