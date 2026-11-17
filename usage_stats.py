"""Minimal aggregate usage analytics, with no source filenames, EANs or user data.

Render's default filesystem is ephemeral. Set QS_STATS_DATABASE_URL to a persistent
PostgreSQL database for durable statistics. Without it, SQLite is temporary.
"""
from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS usage_events (
    id TEXT PRIMARY KEY,
    day TEXT NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    rows_processed INTEGER NOT NULL DEFAULT 0,
    photos INTEGER NOT NULL DEFAULT 0,
    products INTEGER NOT NULL DEFAULT 0,
    errors INTEGER NOT NULL DEFAULT 0,
    bytes_total BIGINT NOT NULL DEFAULT 0
)
"""


def date_for_timestamp(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).strftime("%Y-%m-%d")


class UsageStats:
    def __init__(self, database_url=None, db_path=None):
        self.database_url = (database_url if database_url is not None else os.getenv("QS_STATS_DATABASE_URL", "")).strip()
        self.db_path = db_path if db_path is not None else os.getenv("QS_STATS_DB_PATH", "/tmp/quicksilver-usage.sqlite3")
        self.persistent = bool(self.database_url)
        self._lock = threading.RLock()
        self._initialized = False

    def _connect(self):
        if self.database_url:
            import psycopg
            return psycopg.connect(self.database_url, connect_timeout=6), "%s"
        path = Path(self.db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(path, timeout=15), "?"

    def _execute(self, callback):
        with self._lock:
            conn, mark = self._connect()
            try:
                if not self._initialized:
                    conn.execute(SCHEMA)
                    self._initialized = True
                result = callback(conn, mark)
                conn.commit()
                return result
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def record(self, event_id, day, kind, status, rows_processed=0, photos=0,
               products=0, errors=0, bytes_total=0):
        """Idempotent: a start and a finish with the same ID count as one job."""
        values = (event_id, day, kind, status, int(rows_processed), int(photos),
                  int(products), int(errors), int(bytes_total))
        query = """
            INSERT INTO usage_events
                (id, day, kind, status, rows_processed, photos, products, errors, bytes_total)
            VALUES ({})
            ON CONFLICT(id) DO UPDATE SET
                status = excluded.status,
                rows_processed = excluded.rows_processed,
                photos = excluded.photos,
                products = excluded.products,
                errors = excluded.errors,
                bytes_total = excluded.bytes_total
        """
        self._execute(lambda conn, mark: conn.execute(query.format(", ".join([mark] * len(values))), values))

    def overview(self):
        query = """
            SELECT
                COUNT(*),
                COALESCE(SUM(CASE WHEN kind='upload' THEN 1 ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN kind='photo' THEN 1 ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN kind='photo' AND status='complete' THEN 1 ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN kind='photo' AND status='failed' THEN 1 ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN kind='photo' AND status='cancelled' THEN 1 ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN kind='settlement' THEN 1 ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN kind='photo' THEN photos ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN kind='photo' THEN products ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN kind='photo' THEN errors ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN kind='photo' THEN rows_processed ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN kind='photo' THEN bytes_total ELSE 0 END), 0)
            FROM usage_events
        """
        keys = ("events", "uploads", "photo_jobs", "completed", "failed", "cancelled",
                "settlements", "photos", "products", "errors", "rows", "photo_bytes")
        result = self._execute(lambda conn, mark: conn.execute(query).fetchone())
        return {"persistent": self.persistent, "storage": "PostgreSQL" if self.persistent else "SQLite (tymczasowo)",
                **dict(zip(keys, result))}

    def daily(self, limit=30):
        query = """
            SELECT day,
                SUM(CASE WHEN kind='upload' THEN 1 ELSE 0 END),
                SUM(CASE WHEN kind='photo' THEN 1 ELSE 0 END),
                SUM(CASE WHEN kind='photo' AND status='complete' THEN 1 ELSE 0 END),
                SUM(CASE WHEN kind='photo' AND status='failed' THEN 1 ELSE 0 END),
                SUM(CASE WHEN kind='settlement' THEN 1 ELSE 0 END),
                SUM(CASE WHEN kind='photo' THEN photos ELSE 0 END),
                SUM(CASE WHEN kind='photo' THEN products ELSE 0 END),
                SUM(CASE WHEN kind='photo' THEN errors ELSE 0 END),
                SUM(CASE WHEN kind='photo' THEN bytes_total ELSE 0 END)
            FROM usage_events
            GROUP BY day ORDER BY day DESC LIMIT {}
        """
        keys = ("date", "uploads", "photo_jobs", "completed", "failed",
                "settlements", "photos", "products", "errors", "photo_bytes")
        def query_rows(conn, mark):
            return conn.execute(query.format(mark), (limit,)).fetchall()
        return [dict(zip(keys, row)) for row in self._execute(query_rows)]


STATS = UsageStats()
