from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import aiosqlite
import structlog

from config.settings import settings

logger = structlog.get_logger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_usage (
    user_id INTEGER NOT NULL,
    usage_date TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, usage_date)
);

CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    chat_id INTEGER NOT NULL,
    status TEXT NOT NULL,
    progress INTEGER NOT NULL DEFAULT 0,
    progress_message_id INTEGER,
    job_dir TEXT,
    highlights_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS user_locks (
    user_id INTEGER PRIMARY KEY,
    job_id TEXT NOT NULL
);
"""


class Database:
    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path or settings.database_path

    async def _connection(self) -> aiosqlite.Connection:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(self._db_path)
        conn.row_factory = aiosqlite.Row
        await conn.executescript(SCHEMA)
        await conn.commit()
        return conn

    async def get_daily_usage(self, user_id: int) -> int:
        today = date.today().isoformat()
        conn = await self._connection()
        try:
            cursor = await conn.execute(
                "SELECT count FROM daily_usage WHERE user_id = ? AND usage_date = ?",
                (user_id, today),
            )
            row = await cursor.fetchone()
            return int(row["count"]) if row else 0
        finally:
            await conn.close()

    async def increment_daily_usage(self, user_id: int) -> int:
        today = date.today().isoformat()
        conn = await self._connection()
        try:
            await conn.execute(
                """
                INSERT INTO daily_usage (user_id, usage_date, count)
                VALUES (?, ?, 1)
                ON CONFLICT(user_id, usage_date)
                DO UPDATE SET count = count + 1
                """,
                (user_id, today),
            )
            await conn.commit()
            cursor = await conn.execute(
                "SELECT count FROM daily_usage WHERE user_id = ? AND usage_date = ?",
                (user_id, today),
            )
            row = await cursor.fetchone()
            return int(row["count"])
        finally:
            await conn.close()

    async def has_active_job(self, user_id: int) -> bool:
        conn = await self._connection()
        try:
            cursor = await conn.execute(
                "SELECT 1 FROM user_locks WHERE user_id = ?",
                (user_id,),
            )
            return await cursor.fetchone() is not None
        finally:
            await conn.close()

    async def lock_user(self, user_id: int, job_id: str) -> None:
        conn = await self._connection()
        try:
            await conn.execute(
                "INSERT OR REPLACE INTO user_locks (user_id, job_id) VALUES (?, ?)",
                (user_id, job_id),
            )
            await conn.commit()
        finally:
            await conn.close()

    async def unlock_user(self, user_id: int) -> None:
        conn = await self._connection()
        try:
            await conn.execute("DELETE FROM user_locks WHERE user_id = ?", (user_id,))
            await conn.commit()
        finally:
            await conn.close()

    async def get_locked_job_id(self, user_id: int) -> str | None:
        conn = await self._connection()
        try:
            cursor = await conn.execute(
                "SELECT job_id FROM user_locks WHERE user_id = ?",
                (user_id,),
            )
            row = await cursor.fetchone()
            return str(row["job_id"]) if row else None
        finally:
            await conn.close()

    async def clear_all_locks(self) -> int:
        conn = await self._connection()
        try:
            cursor = await conn.execute("SELECT COUNT(*) AS c FROM user_locks")
            row = await cursor.fetchone()
            count = int(row["c"]) if row else 0
            await conn.execute("DELETE FROM user_locks")
            await conn.commit()
            return count
        finally:
            await conn.close()

    async def create_job(
        self,
        job_id: str,
        user_id: int,
        chat_id: int,
        job_dir: str,
        progress_message_id: int | None = None,
    ) -> None:
        conn = await self._connection()
        try:
            await conn.execute(
                """
                INSERT INTO jobs (job_id, user_id, chat_id, status, progress, progress_message_id, job_dir)
                VALUES (?, ?, ?, 'downloading', 0, ?, ?)
                """,
                (job_id, user_id, chat_id, progress_message_id, job_dir),
            )
            await conn.commit()
        finally:
            await conn.close()

    async def update_job(
        self,
        job_id: str,
        *,
        status: str | None = None,
        progress: int | None = None,
        progress_message_id: int | None = None,
        highlights_json: str | None = None,
    ) -> None:
        fields: list[str] = []
        values: list[object] = []
        if status is not None:
            fields.append("status = ?")
            values.append(status)
        if progress is not None:
            fields.append("progress = ?")
            values.append(progress)
        if progress_message_id is not None:
            fields.append("progress_message_id = ?")
            values.append(progress_message_id)
        if highlights_json is not None:
            fields.append("highlights_json = ?")
            values.append(highlights_json)
        if not fields:
            return
        values.append(job_id)
        conn = await self._connection()
        try:
            await conn.execute(
                f"UPDATE jobs SET {', '.join(fields)} WHERE job_id = ?",
                values,
            )
            await conn.commit()
        finally:
            await conn.close()

    async def get_job(self, job_id: str) -> dict | None:
        conn = await self._connection()
        try:
            cursor = await conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,))
            row = await cursor.fetchone()
            return dict(row) if row else None
        finally:
            await conn.close()

    async def get_job_highlights(self, job_id: str) -> dict | None:
        job = await self.get_job(job_id)
        if not job or not job.get("highlights_json"):
            return None
        return json.loads(job["highlights_json"])

    async def save_job_highlights(self, job_id: str, highlights: dict) -> None:
        await self.update_job(job_id, highlights_json=json.dumps(highlights, ensure_ascii=False))
