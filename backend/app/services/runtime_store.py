from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.core.config import get_settings


class RuntimeStoreError(RuntimeError):
    pass


class RuntimeStore:
    def __init__(self, *, path: Path) -> None:
        self.path = path
        self.last_error: str | None = None
        self._lock = threading.RLock()
        self._initialize()

    def set_json(
        self,
        name: str,
        payload: Any,
        *,
        ttl_seconds: int | None = None,
    ) -> None:
        text = json.dumps(payload, ensure_ascii=False, default=str)
        expires_at = time.time() + ttl_seconds if ttl_seconds is not None else None
        try:
            with self._lock, self._connect() as connection:
                connection.execute(
                    """
                    insert into runtime_json(name, payload, expires_at, updated_at)
                    values (?, ?, ?, ?)
                    on conflict(name) do update set
                        payload = excluded.payload,
                        expires_at = excluded.expires_at,
                        updated_at = excluded.updated_at
                    """,
                    (name, text, expires_at, self._now()),
                )
            self.last_error = None
        except sqlite3.Error as exc:
            self._raise_failure(exc)

    def get_json(self, name: str) -> Any | None:
        try:
            with self._lock, self._connect() as connection:
                row = connection.execute(
                    "select payload, expires_at from runtime_json where name = ?",
                    (name,),
                ).fetchone()
                if row is None:
                    self.last_error = None
                    return None
                payload, expires_at = row
                if expires_at is not None and float(expires_at) <= time.time():
                    connection.execute("delete from runtime_json where name = ?", (name,))
                    self.last_error = None
                    return None
            self.last_error = None
        except sqlite3.Error as exc:
            self._raise_failure(exc)

        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            return None

    def delete(self, name: str) -> None:
        try:
            with self._lock, self._connect() as connection:
                connection.execute("delete from runtime_json where name = ?", (name,))
            self.last_error = None
        except sqlite3.Error as exc:
            self._raise_failure(exc)

    def record_event(self, name: str, payload: Any) -> bool:
        text = json.dumps(payload, ensure_ascii=False, default=str)
        try:
            with self._lock, self._connect() as connection:
                connection.execute(
                    "insert into runtime_events(name, payload, created_at) values (?, ?, ?)",
                    (name, text, self._now()),
                )
                connection.execute(
                    """
                    delete from runtime_events
                    where id not in (
                        select id from runtime_events
                        order by id desc
                        limit 1000
                    )
                    """
                )
            self.last_error = None
            return True
        except sqlite3.Error as exc:
            self._raise_failure(exc)

    def list_events(
        self,
        *,
        names: list[str] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        effective_limit = max(1, min(limit, 500))
        try:
            with self._lock, self._connect() as connection:
                if names:
                    placeholders = ",".join("?" for _ in names)
                    rows = connection.execute(
                        f"""
                        select id, name, payload, created_at
                        from runtime_events
                        where name in ({placeholders})
                        order by id desc
                        limit ?
                        """,
                        (*names, effective_limit),
                    ).fetchall()
                else:
                    rows = connection.execute(
                        """
                        select id, name, payload, created_at
                        from runtime_events
                        order by id desc
                        limit ?
                        """,
                        (effective_limit,),
                    ).fetchall()
            self.last_error = None
        except sqlite3.Error as exc:
            self._raise_failure(exc)

        events: list[dict[str, Any]] = []
        for event_id, name, payload_text, created_at in rows:
            try:
                payload = json.loads(payload_text)
            except json.JSONDecodeError:
                payload = payload_text
            events.append(
                {
                    "id": event_id,
                    "name": name,
                    "payload": payload,
                    "created_at": created_at,
                }
            )
        return events

    def require_ready(self) -> None:
        self._initialize()
        try:
            with self._lock, self._connect() as connection:
                connection.execute("select 1").fetchone()
            self.last_error = None
        except sqlite3.Error as exc:
            self._raise_failure(exc)

    def status(self) -> dict[str, object]:
        try:
            self.require_ready()
            return {
                "available": True,
                "path": str(self.path),
                "last_error": self.last_error,
            }
        except RuntimeStoreError:
            return {
                "available": False,
                "path": str(self.path),
                "last_error": self.last_error,
            }

    def _initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self._lock, self._connect() as connection:
                connection.execute("pragma journal_mode = wal")
                connection.execute(
                    """
                    create table if not exists runtime_json (
                        name text primary key,
                        payload text not null,
                        expires_at real,
                        updated_at text not null
                    )
                    """
                )
                connection.execute(
                    """
                    create table if not exists runtime_events (
                        id integer primary key autoincrement,
                        name text not null,
                        payload text not null,
                        created_at text not null
                    )
                    """
                )
            self.last_error = None
        except sqlite3.Error as exc:
            self._raise_failure(exc)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=5.0)

    def _raise_failure(self, exc: BaseException) -> None:
        self.last_error = str(exc)
        raise RuntimeStoreError(str(exc)) from exc

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()


@lru_cache
def get_runtime_store() -> RuntimeStore:
    settings = get_settings()
    return RuntimeStore(path=settings.runtime_store_path)
