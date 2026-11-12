"""SQLite usage ledger for per-tenant token accounting and request telemetry."""

import sqlite3
import threading
import time
from typing import Dict, Optional


class UsageLedger:
    """Thread-safe SQLite ledger tracking token usage and request latency."""

    def __init__(self, db_path: str = "gateway_ledger.db") -> None:
        self.db_path = db_path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS usage_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    status_code INTEGER NOT NULL,
                    prompt_tokens INTEGER NOT NULL DEFAULT 0,
                    completion_tokens INTEGER NOT NULL DEFAULT 0,
                    ttft_ms REAL,
                    total_ms REAL NOT NULL,
                    created_at REAL NOT NULL
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_usage_tenant ON usage_events(tenant_id)"
            )
            self._conn.commit()

    def record(
        self,
        request_id: str,
        tenant_id: str,
        status_code: int,
        prompt_tokens: int,
        completion_tokens: int,
        ttft_ms: Optional[float],
        total_ms: float,
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO usage_events (
                    request_id, tenant_id, status_code,
                    prompt_tokens, completion_tokens, ttft_ms, total_ms, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request_id,
                    tenant_id,
                    status_code,
                    prompt_tokens,
                    completion_tokens,
                    ttft_ms,
                    total_ms,
                    time.time(),
                ),
            )
            self._conn.commit()

    def get_tenant_tokens(self, tenant_id: str) -> int:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT COALESCE(SUM(prompt_tokens + completion_tokens), 0) AS total
                FROM usage_events
                WHERE tenant_id = ? AND status_code = 200
                """,
                (tenant_id,),
            ).fetchone()
            return int(row["total"]) if row else 0

    def get_tenant_summary(self, tenant_id: str) -> Dict[str, float]:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT
                    COUNT(*) AS requests,
                    COALESCE(SUM(CASE WHEN status_code = 200 THEN 1 ELSE 0 END), 0) AS succeeded,
                    COALESCE(SUM(CASE WHEN status_code = 503 THEN 1 ELSE 0 END), 0) AS refused_503,
                    COALESCE(SUM(prompt_tokens + completion_tokens), 0) AS total_tokens,
                    COALESCE(AVG(CASE WHEN status_code = 200 THEN ttft_ms END), 0.0) AS avg_ttft_ms
                FROM usage_events
                WHERE tenant_id = ?
                """,
                (tenant_id,),
            ).fetchone()
            return {
                "requests": int(row["requests"]),
                "succeeded": int(row["succeeded"]),
                "refused_503": int(row["refused_503"]),
                "total_tokens": int(row["total_tokens"]),
                "avg_ttft_ms": round(float(row["avg_ttft_ms"]), 2),
            }

    def close(self) -> None:
        with self._lock:
            self._conn.close()
