from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.core.config import get_settings
from app.schemas.chat import ChatSession
from app.services.local_copilot_identity import (
    is_local_copilot_title,
    mentions_local_copilot,
)
from app.services.memory import MemoryService
from app.services.vision_model_client import format_window_observation


AGENT_TOOL_NAMES = ("memory.search",)
_OPENAI_TOOL_NAMES = {"memory_search": "memory.search"}
_INTERNAL_TO_OPENAI = {v: k for k, v in _OPENAI_TOOL_NAMES.items()}

_CJK_PATTERN = re.compile(r"[\u4e00-\u9fff]+")
_TOKEN_PATTERN = re.compile(r"[\u4e00-\u9fff]+|[A-Za-z0-9]+")


@dataclass(frozen=True)
class AgentToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]

    @property
    def openai_name(self) -> str:
        return _INTERNAL_TO_OPENAI[self.name]

    def to_model_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "openai_name": self.openai_name,
            "description": self.description,
            "parameters": self.parameters,
        }

    def to_openai_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.openai_name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass(frozen=True)
class AgentToolCall:
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    call_id: str | None = None
    model_name: str | None = None


@dataclass(frozen=True)
class AgentToolResult:
    name: str
    ok: bool
    content: str
    data: dict[str, Any] = field(default_factory=dict)
    user_error: str | None = None
    call_id: str | None = None
    model_name: str | None = None


@dataclass(frozen=True)
class AgentToolContext:
    question: str
    latest: Any | None
    history_summaries: list[dict[str, Any]]
    chat_history: list[ChatSession]
    user_goals: list[dict[str, Any]] = field(default_factory=list)


class AgentToolRegistry:
    """Single model-visible context tool registry.

    The chat path does not inject screen observations by default. The model gets
    stable profile/dialogue context, then explicitly calls memory.search when it
    needs current screen, recent observations, remembered facts, or prior turns.
    """

    def __init__(self) -> None:
        self._specs = {
            "memory.search": AgentToolSpec(
                name="memory.search",
                description=(
                    "Search local evidence on demand. Returns only context related to the query, "
                    "with source, record_id, and screenshot metadata when available. Call this before "
                    "answering questions about screen content, current pages/code/windows, recent visual "
                    "context, remembered facts, or earlier conversation details."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "The user's question or the specific local evidence needed.",
                        }
                    },
                    "required": ["query"],
                },
            )
        }

    def manifest(self) -> list[dict[str, Any]]:
        return [self._specs[name].to_model_dict() for name in AGENT_TOOL_NAMES]

    def openai_tools(self) -> list[dict[str, Any]]:
        return [self._specs[name].to_openai_tool() for name in AGENT_TOOL_NAMES]

    def manifest_for_prompt(self) -> str:
        return json.dumps(self.manifest(), ensure_ascii=False, indent=2)

    def validate_calls(self, raw_calls: Any) -> list[AgentToolCall]:
        if not isinstance(raw_calls, list):
            raise ValueError("tool_calls must be a list.")
        calls: list[AgentToolCall] = []
        for raw in raw_calls:
            if not isinstance(raw, dict):
                raise ValueError("Each tool call must be an object.")
            name = str(raw.get("name") or "").strip()
            internal_name = _OPENAI_TOOL_NAMES.get(name, name)
            if internal_name not in self._specs:
                raise ValueError(f"Unknown tool: {name or '<empty>'}.")
            args = raw.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args) if args.strip() else {}
                except json.JSONDecodeError as exc:
                    raise ValueError(f"Tool arguments for {name} must be JSON.") from exc
            if not isinstance(args, dict):
                raise ValueError(f"Tool arguments for {name} must be an object.")
            calls.append(AgentToolCall(name=internal_name, arguments=args, model_name=name))
        return calls

    def from_openai_tool_calls(self, raw_calls: Any) -> list[AgentToolCall]:
        if not isinstance(raw_calls, list):
            return []
        calls: list[AgentToolCall] = []
        for raw in raw_calls:
            if not isinstance(raw, dict):
                continue
            function = raw.get("function")
            if not isinstance(function, dict):
                continue
            model_name = str(function.get("name") or "").strip()
            internal_name = _OPENAI_TOOL_NAMES.get(model_name, model_name)
            if internal_name not in self._specs:
                continue
            args_raw = function.get("arguments") or {}
            if isinstance(args_raw, str):
                try:
                    args = json.loads(args_raw) if args_raw.strip() else {}
                except json.JSONDecodeError:
                    args = {}
            elif isinstance(args_raw, dict):
                args = args_raw
            else:
                args = {}
            calls.append(
                AgentToolCall(
                    name=internal_name,
                    arguments=args,
                    call_id=str(raw.get("id") or ""),
                    model_name=model_name,
                )
            )
        return calls


class AgentToolRuntime:
    """memory.search 工具执行器。

    Ranker 使用 SQLite FTS5 BM25（见 spec §5.3），不依赖 VLM。
    """

    def __init__(self, *, memory_service: MemoryService | None) -> None:
        self.memory_service = memory_service

    def execute_many(
        self,
        calls: list[AgentToolCall],
        context: AgentToolContext,
    ) -> list[AgentToolResult]:
        return [self.execute(call, context) for call in calls]

    def execute(
        self,
        call: AgentToolCall,
        context: AgentToolContext,
    ) -> AgentToolResult:
        if call.name == "memory.search":
            return self._memory_search(call, context)
        raise ValueError(f"Unknown tool: {call.name}")

    def _memory_search(self, call: AgentToolCall, context: AgentToolContext) -> AgentToolResult:
        query = str(call.arguments.get("query") or context.question).strip()
        candidates = self._collect_candidates(context)
        if not candidates:
            payload = {
                "query": query,
                "results": [],
                "missing": ["no_local_context_available"],
                "warnings": ["没有可用的窗口观察、记忆或历史对话。需要屏幕证据时请先观察窗口。"],
            }
            return self._json_result(call, ok=False, payload=payload)

        selected = self._rank_candidates(query=query, candidates=candidates)
        results = [self._format_result(candidate) for candidate in selected]
        payload = {
            "query": query,
            "results": results,
            "missing": [] if results else ["no_relevant_local_context"],
            "warnings": [] if results else ["工具没有找到和问题足够相关的本地证据。"],
        }
        return self._json_result(
            call,
            ok=bool(results),
            payload=payload,
            data={"query": query, "candidate_count": len(candidates), "selected_count": len(results)},
        )

    def _collect_candidates(self, context: AgentToolContext) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        latest = context.latest
        if latest is not None and not self._is_invalid_latest(latest):
            content = self._latest_content(latest)
            candidates.append({
                "id": f"latest:{latest.capture.screenshot_hash}",
                "source": "window:latest_analysis",
                "record_id": None,
                "created_at": latest.analyzed_at.isoformat() if latest.analyzed_at else "",
                "app_name": latest.capture.app_name or "",
                "window_title": latest.capture.window_title or "",
                "window_type": latest.analysis.window_type,
                "screenshot_path": str(latest.capture.screenshot_path),
                "screenshot_hash": latest.capture.screenshot_hash,
                "content": content,
                "rank_text": self._rank_text(content),
            })

        for item in context.history_summaries:
            title = str(item.get("window_title") or "")
            content = self._summary_record_content(item)
            if is_local_copilot_title(title) or mentions_local_copilot(self._rank_text(content)):
                continue
            record_id = str(item.get("record_id") or "")
            candidates.append({
                "id": f"window:{record_id or item.get('screenshot_hash') or len(candidates)}",
                "source": "window:summaries",
                "record_id": record_id or None,
                "created_at": str(item.get("created_at") or ""),
                "app_name": str(item.get("app_name") or ""),
                "window_title": title,
                "window_type": str(item.get("window_type") or ""),
                "screenshot_path": str(item.get("screenshot_path") or ""),
                "screenshot_hash": str(item.get("screenshot_hash") or ""),
                "content": content,
                "rank_text": self._rank_text(content),
            })

        if self.memory_service is not None:
            settings = get_settings()
            for item in self.memory_service.recent_items(limit=settings.memory_retrieve_count):
                if item.kind == "analysis_summary":
                    continue
                text = item.text.strip()
                if not text or mentions_local_copilot(text):
                    continue
                candidates.append({
                    "id": f"memory:{item.memory_id}",
                    "source": "memory:items",
                    "record_id": item.memory_id,
                    "created_at": item.created_at.isoformat(),
                    "app_name": "",
                    "window_title": "",
                    "window_type": item.kind,
                    "screenshot_path": "",
                    "screenshot_hash": "",
                    "content": {"text": text, "tags": item.tags, "metadata": item.metadata},
                    "rank_text": text,
                })

        for session in context.chat_history:
            text = f"用户：{session.question}\n助手：{session.answer}".strip()
            if not text or mentions_local_copilot(text):
                continue
            candidates.append({
                "id": f"chat:{session.session_id}",
                "source": "assistant:chat:history",
                "record_id": session.session_id,
                "created_at": session.updated_at.isoformat(),
                "app_name": "",
                "window_title": "",
                "window_type": "conversation",
                "screenshot_path": session.image_path or "",
                "screenshot_hash": "",
                "content": {"question": session.question, "answer": session.answer, "status": session.status},
                "rank_text": text,
            })

        # 跨会话 FTS5 检索（spec §6.2）：从持久索引中按 BM25 检索历史对话
        fts_hits = self._search_chat_history_fts(query=context.question)
        for hit in fts_hits:
            sid = hit["session_id"]
            if any(c["id"] == f"chat:{sid}" for c in candidates):
                continue  # 已在最近 N 条中，不重复
            candidates.append({
                "id": f"chat_fts:{sid}",
                "source": "assistant:chat:history",
                "record_id": sid,
                "created_at": hit["created_at"],
                "app_name": "",
                "window_title": "",
                "window_type": "conversation",
                "screenshot_path": "",
                "screenshot_hash": "",
                "content": {"question": "(历史会话)", "answer": "(见 FTS5 检索结果)", "bm25_score": hit["bm25_score"]},
                "rank_text": f"历史会话 {sid}",
            })
        return candidates

    @staticmethod
    def _search_chat_history_fts(*, query: str) -> list[dict[str, Any]]:
        """从持久 FTS5 索引检索历史对话（spec §6.2）。"""
        if not query.strip():
            return []
        try:
            from app.services.chat_history_index import get_chat_history_index
            return get_chat_history_index().search(query=query, limit=5)
        except Exception:
            return []

    @staticmethod
    def _rank_candidates(
        *,
        query: str,
        candidates: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """FTS5 BM25 排名（见 spec §5.3）。

        在内存 SQLite 中建临时 FTS5 索引，对候选证据打分排序。
        中文采用 bigram 双字滑窗分词，ASCII 整词保留。
        """
        if not candidates:
            return []

        conn = sqlite3.connect(":memory:")
        try:
            conn.execute(
                """
                CREATE VIRTUAL TABLE search_fts USING fts5(
                    candidate_id UNINDEXED,
                    search_text,
                    tokenize = 'unicode61'
                )
                """
            )
            for c in candidates:
                searchable_parts = [
                    c.get("app_name") or "",
                    c.get("window_title") or "",
                    c.get("window_type") or "",
                    c.get("rank_text") or "",
                ]
                search_text = _bigram_tokenize(" ".join(searchable_parts))
                conn.execute(
                    "INSERT INTO search_fts VALUES (?, ?)",
                    (c["id"], search_text),
                )
            conn.commit()

            fts_query = _bigram_tokenize(query)
            if not fts_query.strip():
                return candidates[:min(4, len(candidates))]

            fts_tokens = fts_query.split()
            if not fts_tokens:
                return candidates[:min(4, len(candidates))]
            fts_match = " OR ".join(fts_tokens)

            rows = conn.execute(
                "SELECT candidate_id, bm25(search_fts) as score "
                "FROM search_fts WHERE search_fts MATCH ? "
                "ORDER BY score ASC LIMIT 4",
                (fts_match,),
            ).fetchall()

            by_id = {c["id"]: c for c in candidates}
            selected: list[dict[str, Any]] = []
            for cid, score in rows:
                c = by_id.get(str(cid))
                if c is None:
                    continue
                c = dict(c)
                c["selection_note"] = f"bm25_score={score:.4f}"
                selected.append(c)
            return selected
        finally:
            conn.close()

    @staticmethod
    def _latest_content(latest: Any) -> dict[str, Any]:
        return {
            "summary": latest.analysis.summary,
            "key_points": list(latest.analysis.key_points),
            "regions": [region.model_dump(mode="json") for region in latest.analysis.regions],
            "visible_text": list(latest.analysis.visible_text),
            "ui_elements": list(latest.analysis.ui_elements),
            "entities": list(latest.analysis.entities),
            "uncertain_areas": list(latest.analysis.uncertain_areas),
            "vision_input": latest.vision_input.model_dump(mode="json") if latest.vision_input else None,
        }

    @staticmethod
    def _summary_record_content(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "summary": str(item.get("summary") or ""),
            "key_points": item.get("key_points") if isinstance(item.get("key_points"), list) else [],
            "regions": item.get("regions") if isinstance(item.get("regions"), list) else [],
            "visible_text": item.get("visible_text") if isinstance(item.get("visible_text"), list) else [],
            "ui_elements": item.get("ui_elements") if isinstance(item.get("ui_elements"), list) else [],
            "entities": item.get("entities") if isinstance(item.get("entities"), list) else [],
            "uncertain_areas": item.get("uncertain_areas") if isinstance(item.get("uncertain_areas"), list) else [],
            "vision_input": item.get("vision_input") if isinstance(item.get("vision_input"), dict) else None,
        }

    @staticmethod
    def _rank_text(content: dict[str, Any]) -> str:
        if "text" in content:
            return str(content.get("text") or "")
        return format_window_observation(
            summary=str(content.get("summary") or ""),
            key_points=content.get("key_points") if isinstance(content.get("key_points"), list) else [],
            regions=content.get("regions") if isinstance(content.get("regions"), list) else [],
            visible_text=content.get("visible_text") if isinstance(content.get("visible_text"), list) else [],
            ui_elements=content.get("ui_elements") if isinstance(content.get("ui_elements"), list) else [],
            entities=content.get("entities") if isinstance(content.get("entities"), list) else [],
            uncertain_areas=content.get("uncertain_areas") if isinstance(content.get("uncertain_areas"), list) else [],
            vision_input=content.get("vision_input"),
        )

    @staticmethod
    def _format_result(candidate: dict[str, Any]) -> dict[str, Any]:
        return {
            "source": candidate.get("source"),
            "record_id": candidate.get("record_id"),
            "created_at": candidate.get("created_at"),
            "app_name": candidate.get("app_name"),
            "window_title": candidate.get("window_title"),
            "window_type": candidate.get("window_type"),
            "screenshot_path": candidate.get("screenshot_path"),
            "screenshot_hash": candidate.get("screenshot_hash"),
            "content": candidate.get("content"),
            "selection_note": candidate.get("selection_note") or "",
        }

    def _json_result(
        self,
        call: AgentToolCall,
        *,
        ok: bool,
        payload: dict[str, Any],
        data: dict[str, Any] | None = None,
        user_error: str | None = None,
    ) -> AgentToolResult:
        return AgentToolResult(
            name=call.name,
            ok=ok,
            content=json.dumps(payload, ensure_ascii=False, indent=2, default=self._json_default),
            data=data or payload,
            user_error=user_error,
            call_id=call.call_id,
            model_name=call.model_name,
        )

    @staticmethod
    def _json_default(value: Any) -> str:
        if isinstance(value, datetime):
            return value.isoformat()
        return str(value)

    @staticmethod
    def _is_invalid_latest(latest: Any) -> bool:
        title = str(getattr(latest.capture, "window_title", "") or "")
        if is_local_copilot_title(title):
            return True
        observation = getattr(latest, "observation", None)
        return bool(observation is not None and observation.privacy_state == "privacy")


def _bigram_tokenize(text: str) -> str:
    """中文双字滑窗 + ASCII 整词分词。

    CJK 字符按重叠 2 字 token 分词（中文 IR 标准做法），ASCII 字母数字整词保留。
    不需要外部分词器依赖。
    """
    if not text:
        return ""
    tokens: list[str] = []
    parts = _TOKEN_PATTERN.findall(text)
    for part in parts:
        if _CJK_PATTERN.match(part):
            for i in range(len(part) - 1):
                tokens.append(part[i : i + 2])
            if len(part) == 1:
                tokens.append(part)
        else:
            tokens.append(part.lower())
    return " ".join(tokens)


def get_agent_tool_registry() -> AgentToolRegistry:
    return AgentToolRegistry()
