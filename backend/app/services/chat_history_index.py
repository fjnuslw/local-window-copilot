"""跨会话对话历史的持久 FTS5 索引（spec §6.2）。

对话结束时把 session 写入 chat_history_fts 表，
memory.search 候选集加入此表的 BM25 检索结果。
"""
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.services.agent_tools import _bigram_tokenize


class ChatHistoryIndex:
    """持久 FTS5 索引，存储所有历史对话供 memory.search 检索。"""

    def __init__(self, *, db_path: Path) -> None:
        self.db_path = db_path
        self._initialize()

    def index_session(
        self,
        *,
        session_id: str,
        question: str,
        answer: str,
        created_at: datetime | None = None,
    ) -> None:
        """对话结束时调用，把 session 写入 FTS5 索引。

        如果 session_id 已存在，先删除旧记录再插入（支持重新归档）。
        """
        if not session_id or not question.strip():
            return
        ts = (created_at or datetime.now(UTC)).isoformat()
        search_text = _bigram_tokenize(f"用户：{question}\n助手：{answer}")
        if not search_text.strip():
            return
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM chat_history_fts WHERE session_id = ?",
                (session_id,),
            )
            conn.execute(
                "INSERT INTO chat_history_fts (session_id, created_at, search_text) VALUES (?, ?, ?)",
                (session_id, ts, search_text),
            )
            conn.commit()

    def search(self, *, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """BM25 检索历史对话，返回 session_id + created_at + score。"""
        fts_query = _bigram_tokenize(query)
        if not fts_query.strip():
            return []
        fts_tokens = fts_query.split()
        if not fts_tokens:
            return []
        fts_match = " OR ".join(fts_tokens)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT session_id, created_at, bm25(chat_history_fts) as score "
                "FROM chat_history_fts WHERE search_text MATCH ? "
                "ORDER BY score ASC LIMIT ?",
                (fts_match, limit),
            ).fetchall()
        return [
            {
                "session_id": row[0],
                "created_at": row[1],
                "bm25_score": row[2],
            }
            for row in rows
        ]

    def clear(self) -> int:
        """清空所有索引记录，返回删除条数。"""
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM chat_history_fts")
            conn.commit()
            return int(cursor.rowcount if cursor.rowcount is not None else 0)

    def count(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) FROM chat_history_fts").fetchone()
            return int(row[0]) if row else 0

    def _initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS chat_history_fts USING fts5(
                    session_id UNINDEXED,
                    created_at UNINDEXED,
                    search_text,
                    tokenize = 'unicode61'
                )
                """
            )
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path, timeout=30.0)


@lru_cache
def get_chat_history_index() -> ChatHistoryIndex:
    settings = get_settings()
    return ChatHistoryIndex(db_path=settings.runtime_store_path)
