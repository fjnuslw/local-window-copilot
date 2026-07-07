from __future__ import annotations

import asyncio
import base64
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.schemas.analyze import WindowAnalysis, WindowAnalysisResult
from app.schemas.window import RawWindowCapture, WindowBounds
from app.schemas.chat import ChatSession
from app.services import assistant_chat as chat_module
from app.services.agent_tools import AgentToolResult
from app.services.assistant_chat import (
    CHAT_CURRENT_KEY,
    CHAT_HISTORY_KEY,
    AnswerContext,
    ChatAgent,
    ContextBudgetExceededError,
)
from app.services.context_summary import (
    COMPACT_LOCK_KEY,
    COMPACT_METRICS_KEY,
    ROLLING_SUMMARY_KEY,
    CompactStateStore,
    build_rolling_summary_state,
)


class FakeRuntimeStore:
    def __init__(self) -> None:
        self.data: dict[str, object] = {}
        self.deleted: list[str] = []
        self.events: list[tuple[str, object]] = []

    def set_json(self, name: str, payload: object, *, ttl_seconds: int | None = None) -> None:
        self.data[name] = payload

    def get_json(self, name: str) -> object | None:
        return self.data.get(name)

    def record_event(self, name: str, payload: object) -> bool:
        self.events.append((name, payload))
        return True

    def delete(self, name: str) -> None:
        self.deleted.append(name)
        self.data.pop(name, None)


class FakeWatcher:
    def __init__(self) -> None:
        self.started = 0
        self.stopped = 0

    async def stop(self):
        self.stopped += 1

    def start(self):
        self.started += 1


class FakeModelRuntimeManager:
    def __init__(self) -> None:
        self.ensure_calls = 0

    def ensure_server_ready(self) -> None:
        self.ensure_calls += 1


class FakeStateService:
    def __init__(self) -> None:
        self.states: list[tuple[str, str | None]] = []

    async def set_state(self, state: str, *, reason: str | None = None):
        self.states.append((state, reason))


class FakeAnalysisService:
    def __init__(
        self,
        *,
        app_name: str = "Code.exe",
        window_title: str = "README.md - VS Code",
        window_type: str = "ide",
        summary: str = "当前窗口是 IDE，打开了后端项目文件。",
        screenshot_path: Path | None = None,
    ) -> None:
        self.app_name = app_name
        self.window_title = window_title
        self.window_type = window_type
        self.summary = summary
        self.screenshot_path = screenshot_path or Path("capture.png")

    def get_latest(self) -> WindowAnalysisResult:
        capture = RawWindowCapture(
            app_name=self.app_name,
            process_id=123,
            window_title=self.window_title,
            window_bounds=WindowBounds(left=0, top=0, right=800, bottom=600),
            screenshot_path=self.screenshot_path,
            screenshot_hash="hash",
            captured_at=datetime.now(UTC),
        )
        return WindowAnalysisResult(
            capture=capture,
            analysis=WindowAnalysis(
                window_type=self.window_type,
                summary=self.summary,
                key_points=["README", "backend"],
                candidate_questions=[],
            ),
            latency_ms=1,
            model_endpoint="http://127.0.0.1:18181/v1/chat/completions",
            analyzed_at=datetime.now(UTC),
        )


class FakeWindowSummaryStore:
    def __init__(self, items: list[dict[str, object]] | None = None) -> None:
        self.items = items or []

    def recent(self, *, limit: int | None = None):
        return self.items[-limit:] if limit is not None else list(self.items)


class FakeMemoryService:
    def __init__(self, items=None) -> None:
        self.items = items or []

    def recent_items(self, *, limit: int = 5):
        return self.items[-limit:]


class FakeVisionClient:
    def __init__(
        self,
        *,
        fail: bool = False,
        tool_calls=None,
        probe_content: str = "第一段，第二段。",
        stream_tool_calls=None,  # 模拟流式阶段发起的 tool_calls
        compact_summary_text: str | None = None,
    ) -> None:
        self.fail = fail
        self.tool_calls = tool_calls
        self.probe_content = probe_content
        self.stream_tool_calls = stream_tool_calls
        self.compact_summary_text = compact_summary_text
        self.calls: list[dict[str, object]] = []
        self.tool_probe_calls: list[dict[str, object]] = []
        self.plan_calls: list[object] = []
        self._collected_stream_tool_calls: list[dict[str, Any]] = []
        self._stream_call_count = 0  # 用于区分第几次 stream 调用

    def stream_chat(self, *, messages, image_path=None, image_long_edge=None, tools=None):
        self.calls.append({
            "messages": messages,
            "image_path": image_path,
            "image_long_edge": image_long_edge,
            "tools": tools,
        })
        if self.fail:
            raise RuntimeError("model failed")
        # 如果模拟了流式工具调用，设置到 collected 属性中
        if self.stream_tool_calls is not None:
            self._collected_stream_tool_calls = list(self.stream_tool_calls)
        else:
            self._collected_stream_tool_calls = []
        # 第 n 次 stream 调用
        self._stream_call_count += 1
        # 当有 stream_tool_calls 时，第一次 stream 不产生文本（模拟模型先发工具调用）
        # 第二次及以后（工具结果已注入）产生实际回答
        if self.stream_tool_calls and self._stream_call_count == 1:
            return  # 第一次不 yield 文本
        yield "第一段"
        yield "，第二段。"

    @property
    def last_stream_tool_calls(self) -> list[dict[str, Any]]:
        return self._collected_stream_tool_calls

    def complete_chat_response(self, **kwargs):
        snapshot = dict(kwargs)
        snapshot["messages"] = [dict(message) for message in kwargs.get("messages", [])]
        if self.fail:
            raise RuntimeError("model failed")
        if kwargs.get("tools"):
            self.tool_probe_calls.append(snapshot)
            message: dict[str, object] = {"content": self.probe_content}
            if self.tool_calls is not None:
                message = {"content": "", "tool_calls": self.tool_calls}
            return {"choices": [{"message": message}]}
        return {"choices": [{"message": {"content": ""}}]}

    def complete_chat(self, *args, **kwargs):
        self.plan_calls.append({"args": args, "kwargs": kwargs})
        if self.compact_summary_text is not None:
            return self.compact_summary_text
        raise AssertionError("compact model should not be called")

def _install_services(monkeypatch):
    watcher = FakeWatcher()
    state = FakeStateService()
    runtime = FakeModelRuntimeManager()
    monkeypatch.setattr(chat_module, "get_window_watcher_service", lambda: watcher)
    monkeypatch.setattr(chat_module, "get_assistant_state_service", lambda: state)
    monkeypatch.setattr(chat_module, "get_model_runtime_manager", lambda: runtime)
    return watcher, state


@pytest.mark.anyio
async def test_chat_uses_memory_search_tool_without_observation_injection(monkeypatch) -> None:
    runtime_store = FakeRuntimeStore()
    watcher, _state = _install_services(monkeypatch)
    vision = FakeVisionClient(
        tool_calls=[
            {
                "id": "call_ctx",
                "type": "function",
                "function": {
                    "name": "memory_search",
                    "arguments": "{\"query\": \"这个窗口在做什么？\"}",
                },
            }
        ]
    )
    service = ChatAgent(
        runtime_store=runtime_store,
        analysis_service=FakeAnalysisService(summary="当前窗口是 IDE，打开了后端项目文件。"),
        vision_model_client=vision,
        window_summary_store=FakeWindowSummaryStore(),
    )

    session = await service.ask("这个窗口在做什么？")
    await asyncio.sleep(0.1)

    assert watcher.stopped == 1
    current = service.current()
    assert current is not None
    assert current.session_id == session.session_id
    assert current.answer == "第一段，第二段。"
    assert current.status == "done"
    assert vision.plan_calls == []
    assert len(vision.tool_probe_calls) == 1
    probe_joined = "\n".join(str(m["content"]) for m in vision.tool_probe_calls[-1]["messages"])
    assert "当前窗口是 IDE" not in probe_joined
    assert len(vision.calls) == 1
    final_messages = vision.calls[-1]["messages"]
    joined = "\n".join(str(m.get("content", "")) for m in final_messages)
    assert any(m.get("role") == "tool" and m.get("name") == "memory_search" for m in final_messages)
    assert "当前窗口是 IDE" in joined
    stages = [
        payload["stage"] for name, payload in runtime_store.events
        if name == "assistant:interaction_trace"
    ]
    assert "context_built" in stages
    assert "answer_messages" in stages
    assert "planner_raw_response" not in stages
    assert "planner_parsed" not in stages

@pytest.mark.anyio
async def test_chat_archives_finished_current_before_new_question(monkeypatch) -> None:
    runtime_store = FakeRuntimeStore()
    now = datetime.now(UTC)
    runtime_store.data[CHAT_CURRENT_KEY] = {
        "session_id": "previous-current",
        "question": "上一轮",
        "answer": "上一轮回答",
        "status": "done",
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }
    _install_services(monkeypatch)
    service = ChatAgent(
        runtime_store=runtime_store,
        analysis_service=FakeAnalysisService(),
        vision_model_client=FakeVisionClient(),
        window_summary_store=FakeWindowSummaryStore(),
        clear_history_on_start=False,
    )

    await service.ask("继续")
    await asyncio.sleep(0.1)

    history = runtime_store.data[CHAT_HISTORY_KEY]
    assert isinstance(history, list)
    assert any(item["session_id"] == "previous-current" for item in history)


@pytest.mark.anyio
async def test_local_copilot_latest_is_not_injected(monkeypatch) -> None:
    runtime_store = FakeRuntimeStore()
    _install_services(monkeypatch)
    vision = FakeVisionClient()
    service = ChatAgent(
        runtime_store=runtime_store,
        analysis_service=FakeAnalysisService(window_title="AlertWindow", summary="self UI pollution"),
        vision_model_client=vision,
        window_summary_store=FakeWindowSummaryStore(),
    )

    await service.ask("当前窗口是什么？")
    await asyncio.sleep(0.1)

    joined = "\n".join(str(m["content"]) for m in vision.tool_probe_calls[-1]["messages"])
    assert "self UI pollution" not in joined
    assert "AlertWindow" not in joined


@pytest.mark.anyio
async def test_user_image_is_attached_to_direct_chat(tmp_path, monkeypatch) -> None:
    upload_dir = tmp_path / "uploads"
    settings = chat_module.get_settings()
    monkeypatch.setattr(settings, "chat_upload_dir", upload_dir)
    monkeypatch.setattr(settings, "chat_image_max_bytes", 1024)
    _install_services(monkeypatch)
    vision = FakeVisionClient()
    service = ChatAgent(
        runtime_store=FakeRuntimeStore(),
        analysis_service=FakeAnalysisService(window_title="AlertWindow"),
        vision_model_client=vision,
        window_summary_store=FakeWindowSummaryStore(),
    )

    encoded = base64.b64encode(b"fake-image").decode("ascii")
    await service.ask(
        "这张图里有什么？",
        image_base64=encoded,
        image_name="clip.png",
        image_mime="image/png",
    )
    await asyncio.sleep(0.1)

    assert vision.calls
    image_path = vision.calls[-1]["image_path"]
    assert isinstance(image_path, Path)
    assert image_path.exists()


@pytest.mark.anyio
async def test_model_error_does_not_use_alternate_answer(monkeypatch) -> None:
    runtime_store = FakeRuntimeStore()
    _install_services(monkeypatch)
    service = ChatAgent(
        runtime_store=runtime_store,
        analysis_service=FakeAnalysisService(),
        vision_model_client=FakeVisionClient(fail=True),
        window_summary_store=FakeWindowSummaryStore(),
    )

    await service.ask("会失败吗？")
    await asyncio.sleep(0.1)

    current = service.current()
    assert current is not None
    assert current.status == "error"
    assert current.error == "model failed"
    assert current.answer == ""


@pytest.mark.anyio
async def test_probe_content_without_tool_calls_is_not_answer(monkeypatch) -> None:
    """probe 阶段模型把工具调用意图写成自然语言 content 而非结构化 tool_calls 时，
    content 不应被当作最终答案。最终答案由 stream 阶段生成。详见 spec §2.2.1。"""
    runtime_store = FakeRuntimeStore()
    _install_services(monkeypatch)
    vision = FakeVisionClient(
        tool_calls=None,
        probe_content="调用 memory.search 检查窗口内容。",
    )
    service = ChatAgent(
        runtime_store=runtime_store,
        analysis_service=FakeAnalysisService(),
        vision_model_client=vision,
        window_summary_store=FakeWindowSummaryStore(),
    )

    await service.ask("你确认一下里面都有什么吧")
    await asyncio.sleep(0.1)

    current = service.current()
    assert current is not None
    assert current.status == "done"
    # probe content 不应出现在最终答案里
    assert "调用 memory.search 检查窗口内容" not in current.answer
    # 最终答案来自 stream 阶段
    assert current.answer == "第一段，第二段。"
    # probe 被调用过
    assert len(vision.tool_probe_calls) == 1
    # stream 也被调用过（probe 无 tool_calls 时不跳过 stream）
    assert len(vision.calls) == 1
    # stream 应该携带 tools（修复后的行为）
    assert vision.calls[0].get("tools") is not None
    # trace 中应有 probe_without_tool_call 而非 no_tool_answer
    stages = [
        payload["stage"] for name, payload in runtime_store.events
        if name == "assistant:interaction_trace"
    ]
    assert "probe_without_tool_call" in stages
    assert "no_tool_answer" not in stages


@pytest.mark.anyio
async def test_stream_can_call_tools_when_probe_fails(monkeypatch) -> None:
    """probe 没发 tool_calls 但流式阶段发了 tool_calls：工具应被执行并重新 stream。

    这覆盖了"对话惰性"的核心修复路径：模型在 stream 阶段获得第二次机会调 memory.search。
    """
    runtime_store = FakeRuntimeStore()
    _install_services(monkeypatch)
    stream_tc = [
        {
            "id": "call_stream_1",
            "type": "function",
            "function": {
                "name": "memory_search",
                "arguments": "{\"query\": \"当前窗口\"}",
            },
        }
    ]
    vision = FakeVisionClient(
        tool_calls=None,           # probe 不发 tool_calls
        probe_content="让我查一下。",
        stream_tool_calls=stream_tc,  # 但 stream 发了
    )
    service = ChatAgent(
        runtime_store=runtime_store,
        analysis_service=FakeAnalysisService(summary="IDE 窗口，打开后端项目。"),
        vision_model_client=vision,
        window_summary_store=FakeWindowSummaryStore(),
    )

    await service.ask("当前是什么窗口")
    await asyncio.sleep(0.1)

    current = service.current()
    assert current is not None
    assert current.status == "done"
    # probe content 不在最终答案中
    assert "让我查一下" not in current.answer
    # 答案来自最终 stream 轮次
    assert current.answer == "第一段，第二段。"

    # 验证调用链：
    # 1) probe 调用一次（无 tools 结果）
    assert len(vision.tool_probe_calls) == 1
    # 2) stream 至少调用两次：第一次发 tool_calls，第二次带结果重新生成
    assert len(vision.calls) >= 2
    # 第一次 stream 有 tools 参数
    first_stream = vision.calls[0]
    assert first_stream.get("tools") is not None
    # 第二次 stream 的消息中应包含工具执行结果
    second_stream = vision.calls[1]
    msgs_second = second_stream["messages"]
    assert any(m.get("role") == "tool" for m in msgs_second)

    # trace 中应有 stream_tool_calls 阶段
    stages = [
        payload["stage"] for name, payload in runtime_store.events
        if name == "assistant:interaction_trace"
    ]
    assert "stream_tool_calls" in stages


def test_inspect_context_reports_real_messages_and_memory_only_tool(monkeypatch) -> None:
    _install_services(monkeypatch)
    service = ChatAgent(
        runtime_store=FakeRuntimeStore(),
        analysis_service=FakeAnalysisService(summary="详细的观察文本内容。"),
        vision_model_client=FakeVisionClient(),
        window_summary_store=FakeWindowSummaryStore(),
    )

    context = service.inspect_context("预览一下")

    assert context["answer_mode"] == "tool_auto"
    assert context["usage"]["answer_mode"] == "tool_auto"
    assert [tool["name"] for tool in context["registered_tools"]] == ["memory.search"]
    assert context["usage"]["registered_tool_count"] == 1
    assert context["messages"]
    joined = "\n".join(str(message["content"]) for message in context["messages"])
    assert "详细的观察文本内容。" not in joined
    assert "详细的观察文本内容。" in str(context["latest_observation"])
    assert context["usage"]["current_observation_chars"] == 0
    assert context["usage"]["available_current_observation_chars"] > 0


def _valid_compact_summary() -> str:
    return "\n".join([
        "## 当前任务",
        "继续验证 compact 触发接入。",
        "## 当前判断",
        "自动触发和 summary 注入需要保持主链路稳定。",
        "## 卡点",
        "需要看 trace 和 RuntimeStore 写入。",
        "## 下一步检索指针",
        "- session_id: s1",
        "- 关键词: compact trigger",
        "## 用户偏好",
        "直接、具体、少冗余。",
        "## 最近完成",
        "008 executor 已完成。",
    ])


def test_rolling_summary_is_injected_after_profile(monkeypatch) -> None:
    runtime_store = FakeRuntimeStore()
    CompactStateStore(runtime_store=runtime_store).save_summary(
        build_rolling_summary_state(
            summary=_valid_compact_summary(),
            covered_session_ids=["s1"],
            source_session_count=1,
            updated_at="2026-07-06T12:00:00+00:00",
        )
    )
    _install_services(monkeypatch)
    service = ChatAgent(
        runtime_store=runtime_store,
        analysis_service=FakeAnalysisService(),
        vision_model_client=FakeVisionClient(),
        window_summary_store=FakeWindowSummaryStore(),
        clear_history_on_start=False,
    )

    preview = service.inspect_context("继续")
    messages = preview["messages"]

    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert messages[2]["content"].startswith("[compact_state]")
    assert "继续验证 compact 触发接入" in messages[2]["content"]
    assert preview["compact_covered_session_ids"] == ["s1"]
    kinds = [segment["kind"] for segment in preview["context_budget"]["segments"]]
    labels = [segment["label"] for segment in preview["context_budget"]["segments"]]
    assert "summary" in kinds
    assert "rolling_summary" in labels


@pytest.mark.anyio
async def test_auto_compact_after_done_writes_summary_metrics_and_trace(monkeypatch) -> None:
    runtime_store = FakeRuntimeStore()
    settings = chat_module.get_settings()
    monkeypatch.setattr(settings, "compact_enabled", True)
    monkeypatch.setattr(settings, "compact_auto_enabled", True)
    monkeypatch.setattr(settings, "compact_raw_tail_turns", 0)
    monkeypatch.setattr(settings, "compact_uncovered_session_threshold", 1)
    monkeypatch.setattr(settings, "compact_history_trigger_tokens", 10**9)
    _install_services(monkeypatch)
    vision = FakeVisionClient(compact_summary_text=_valid_compact_summary())
    service = ChatAgent(
        runtime_store=runtime_store,
        analysis_service=FakeAnalysisService(),
        vision_model_client=vision,
        window_summary_store=FakeWindowSummaryStore(),
    )

    session = await service.ask("触发 compact")
    await asyncio.sleep(0.1)

    current = service.current()
    assert current is not None
    assert current.status == "done"
    assert len(vision.plan_calls) == 1
    assert ROLLING_SUMMARY_KEY in runtime_store.data
    assert COMPACT_METRICS_KEY in runtime_store.data
    summary_payload = runtime_store.data[ROLLING_SUMMARY_KEY]
    assert summary_payload["covered_session_ids"] == [session.session_id]
    stages = [
        payload["stage"]
        for name, payload in runtime_store.events
        if name == "assistant:interaction_trace"
    ]
    assert "context_summary.started" in stages
    assert "context_summary.succeeded" in stages


@pytest.mark.anyio
async def test_auto_compact_error_keeps_session_done_and_traces_failed(monkeypatch) -> None:
    runtime_store = FakeRuntimeStore()
    settings = chat_module.get_settings()
    monkeypatch.setattr(settings, "compact_enabled", True)
    monkeypatch.setattr(settings, "compact_auto_enabled", True)
    monkeypatch.setattr(settings, "compact_raw_tail_turns", 0)
    monkeypatch.setattr(settings, "compact_uncovered_session_threshold", 1)
    monkeypatch.setattr(settings, "compact_history_trigger_tokens", 10**9)
    _install_services(monkeypatch)
    vision = FakeVisionClient(compact_summary_text="invalid summary")
    service = ChatAgent(
        runtime_store=runtime_store,
        analysis_service=FakeAnalysisService(),
        vision_model_client=vision,
        window_summary_store=FakeWindowSummaryStore(),
    )

    await service.ask("触发失败 compact")
    await asyncio.sleep(0.1)

    current = service.current()
    assert current is not None
    assert current.status == "done"
    assert ROLLING_SUMMARY_KEY not in runtime_store.data
    assert runtime_store.data[COMPACT_METRICS_KEY]["last_status"] == "error"
    stages = [
        payload["stage"]
        for name, payload in runtime_store.events
        if name == "assistant:interaction_trace"
    ]
    assert "context_summary.failed" in stages


def test_clear_history_clears_compact_summary_and_lock(monkeypatch) -> None:
    runtime_store = FakeRuntimeStore()
    now = datetime.now(UTC)
    runtime_store.data[CHAT_HISTORY_KEY] = [{
        "session_id": "s1",
        "question": "q",
        "answer": "a",
        "status": "done",
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }]
    compact_store = CompactStateStore(runtime_store=runtime_store)
    compact_store.save_summary(
        build_rolling_summary_state(
            summary=_valid_compact_summary(),
            covered_session_ids=["s1"],
            source_session_count=1,
        )
    )
    compact_store.acquire_lock(source="manual", now=now)
    _install_services(monkeypatch)
    service = ChatAgent(
        runtime_store=runtime_store,
        analysis_service=FakeAnalysisService(),
        vision_model_client=FakeVisionClient(),
        window_summary_store=FakeWindowSummaryStore(),
        clear_history_on_start=False,
    )

    cleared = service.clear_history()

    assert cleared == 1
    assert CHAT_HISTORY_KEY not in runtime_store.data
    assert ROLLING_SUMMARY_KEY not in runtime_store.data
    assert COMPACT_LOCK_KEY not in runtime_store.data

def test_compact_status_empty_store(monkeypatch) -> None:
    runtime_store = FakeRuntimeStore()
    _install_services(monkeypatch)
    service = ChatAgent(
        runtime_store=runtime_store,
        analysis_service=FakeAnalysisService(),
        vision_model_client=FakeVisionClient(),
        window_summary_store=FakeWindowSummaryStore(),
        clear_history_on_start=False,
    )

    status = service.compact_status()

    assert status["enabled"] is True
    assert status["summary"]["present"] is False
    assert status["summary"]["tokens"] == 0
    assert status["summary"]["covered_session_count"] == 0
    assert status["metrics"]["last_status"] == "idle"
    assert status["lock"]["active"] is False
    assert status["planner"]["raw_tail_turns"] >= 0


def test_compact_status_reports_summary_metrics_and_lock(monkeypatch) -> None:
    runtime_store = FakeRuntimeStore()
    compact_store = CompactStateStore(runtime_store=runtime_store)
    covered_ids = [f"s{i}" for i in range(15)]
    summary_state = build_rolling_summary_state(
        summary=_valid_compact_summary(),
        covered_session_ids=covered_ids,
        source_session_count=len(covered_ids),
        updated_at="2026-07-06T12:00:00+00:00",
    )
    compact_store.save_summary(summary_state)
    compact_store.save_success_metrics(
        started_at="2026-07-06T12:00:01+00:00",
        finished_at="2026-07-06T12:00:03+00:00",
        source_session_count=3,
        covered_session_count=len(covered_ids),
        summary_tokens=summary_state.estimate.tokens,
        source_tokens=1234,
    )
    compact_store.acquire_lock(source="manual", now=datetime.now(UTC))
    _install_services(monkeypatch)
    service = ChatAgent(
        runtime_store=runtime_store,
        analysis_service=FakeAnalysisService(),
        vision_model_client=FakeVisionClient(),
        window_summary_store=FakeWindowSummaryStore(),
        clear_history_on_start=False,
    )

    status = service.compact_status()

    assert status["summary"]["present"] is True
    assert status["summary"]["text"] == summary_state.summary
    assert status["summary"]["tokens"] == summary_state.estimate.tokens
    assert status["summary"]["covered_session_count"] == 15
    assert status["summary"]["covered_session_ids_tail"] == covered_ids[-12:]
    assert status["metrics"]["last_status"] == "ok"
    assert status["metrics"]["source_tokens"] == 1234
    assert status["lock"]["active"] is True
    assert status["lock"]["source"] == "manual"

class LargeToolRuntime:
    def __init__(self, *, memory_service=None) -> None:
        self.memory_service = memory_service

    def execute_many(self, calls, context):
        content = json.dumps(
            {
                "query": context.question,
                "results": [
                    {
                        "source": "window:summaries",
                        "record_id": "huge-record",
                        "content": {"visible_text": ["A" * 20000]},
                    }
                ],
            },
            ensure_ascii=False,
        )
        return [
            AgentToolResult(
                name=call.name,
                ok=True,
                content=content,
                call_id=call.call_id,
                model_name=call.model_name,
            )
            for call in calls
        ]


@pytest.mark.anyio
async def test_probe_tool_result_is_budgeted_before_stream(monkeypatch) -> None:
    runtime_store = FakeRuntimeStore()
    _install_services(monkeypatch)
    settings = chat_module.get_settings()
    monkeypatch.setattr(settings, "tool_result_item_budget_tokens", 240)
    monkeypatch.setattr(settings, "tool_result_budget_tokens", 240)
    monkeypatch.setattr(chat_module, "AgentToolRuntime", LargeToolRuntime)
    tool_calls = [
        {
            "id": "call_probe_budget",
            "type": "function",
            "function": {
                "name": "memory_search",
                "arguments": "{\"query\": \"当前窗口\"}",
            },
        }
    ]
    vision = FakeVisionClient(tool_calls=tool_calls)
    service = ChatAgent(
        runtime_store=runtime_store,
        analysis_service=FakeAnalysisService(),
        vision_model_client=vision,
        window_summary_store=FakeWindowSummaryStore(),
    )

    await service.ask("当前窗口")
    await asyncio.sleep(0.1)

    assert vision.calls
    tool_message = next(
        message for message in vision.calls[0]["messages"]
        if message.get("role") == "tool"
    )
    payload = json.loads(str(tool_message["content"]))
    assert payload["tool_result_budget"]["truncated"] is True
    assert payload["original_json_type"] == "dict"
    traces = [
        payload for name, payload in runtime_store.events
        if name == "assistant:interaction_trace" and payload.get("stage") == "tool_result_budget"
    ]
    assert traces
    assert traces[-1]["payload"]["truncated_count"] == 1


@pytest.mark.anyio
async def test_stream_tool_result_is_budgeted_before_second_stream(monkeypatch) -> None:
    runtime_store = FakeRuntimeStore()
    _install_services(monkeypatch)
    settings = chat_module.get_settings()
    monkeypatch.setattr(settings, "tool_result_item_budget_tokens", 240)
    monkeypatch.setattr(settings, "tool_result_budget_tokens", 240)
    monkeypatch.setattr(chat_module, "AgentToolRuntime", LargeToolRuntime)
    stream_tool_calls = [
        {
            "id": "call_stream_budget",
            "type": "function",
            "function": {
                "name": "memory_search",
                "arguments": "{\"query\": \"当前窗口\"}",
            },
        }
    ]
    vision = FakeVisionClient(tool_calls=None, stream_tool_calls=stream_tool_calls)
    service = ChatAgent(
        runtime_store=runtime_store,
        analysis_service=FakeAnalysisService(),
        vision_model_client=vision,
        window_summary_store=FakeWindowSummaryStore(),
    )

    await service.ask("当前窗口")
    await asyncio.sleep(0.1)

    assert len(vision.calls) >= 2
    tool_message = next(
        message for message in vision.calls[1]["messages"]
        if message.get("role") == "tool"
    )
    payload = json.loads(str(tool_message["content"]))
    assert payload["tool_result_budget"]["truncated"] is True
    assert payload["tool_result_budget"]["call_id"] == "call_stream_budget"

def _empty_answer_context(messages: list[dict[str, object]]) -> AnswerContext:
    return AnswerContext(
        latest=None,
        context_latest=None,
        history_summaries=[],
        chat_history=[],
        memory_items=[],
        profile_packet="",
        context_packet="",
        messages=messages,
        registered_tools=[],
        selected_image=None,
        selected_reason="test",
        image_path=None,
    )


def _chat_session(question: str = "q") -> ChatSession:
    now = datetime.now(UTC)
    return ChatSession(
        session_id="session-budget",
        question=question,
        created_at=now,
        updated_at=now,
    )


def test_ensure_messages_within_budget_checked_trace(monkeypatch) -> None:
    runtime_store = FakeRuntimeStore()
    settings = chat_module.get_settings()
    monkeypatch.setattr(settings, "minicpm_ctx_size", 20000)
    monkeypatch.setattr(settings, "answer_max_tokens", 0)
    service = ChatAgent(
        runtime_store=runtime_store,
        analysis_service=FakeAnalysisService(),
        vision_model_client=FakeVisionClient(),
        window_summary_store=FakeWindowSummaryStore(),
        clear_history_on_start=False,
    )
    session = _chat_session()
    context = _empty_answer_context([
        {"role": "system", "content": "system"},
        {"role": "user", "content": "hello"},
    ])

    payload = service._ensure_messages_within_budget(
        session,
        context,
        context.messages,
        phase="stream",
        stream_round=0,
    )

    assert payload["over_limit"] is False
    traces = [
        payload for name, payload in runtime_store.events
        if name == "assistant:interaction_trace"
    ]
    assert traces[-1]["stage"] == "context_budget.checked"
    assert traces[-1]["payload"]["phase"] == "stream"


def test_ensure_messages_within_budget_over_limit_trace(monkeypatch) -> None:
    runtime_store = FakeRuntimeStore()
    settings = chat_module.get_settings()
    monkeypatch.setattr(settings, "minicpm_ctx_size", 1)
    monkeypatch.setattr(settings, "answer_max_tokens", 0)
    service = ChatAgent(
        runtime_store=runtime_store,
        analysis_service=FakeAnalysisService(),
        vision_model_client=FakeVisionClient(),
        window_summary_store=FakeWindowSummaryStore(),
        clear_history_on_start=False,
    )
    session = _chat_session()
    context = _empty_answer_context([
        {"role": "system", "content": "x" * 100},
        {"role": "user", "content": "hello"},
    ])

    with pytest.raises(ContextBudgetExceededError, match="上下文预算超限"):
        service._ensure_messages_within_budget(
            session,
            context,
            context.messages,
            phase="probe",
        )

    traces = [
        payload for name, payload in runtime_store.events
        if name == "assistant:interaction_trace"
    ]
    assert traces[-1]["stage"] == "context_budget.over_limit"
    assert traces[-1]["payload"]["over_limit"] is True


@pytest.mark.anyio
async def test_probe_budget_over_limit_stops_before_model(monkeypatch) -> None:
    runtime_store = FakeRuntimeStore()
    _install_services(monkeypatch)
    settings = chat_module.get_settings()
    monkeypatch.setattr(settings, "minicpm_ctx_size", 1)
    monkeypatch.setattr(settings, "answer_max_tokens", 0)
    vision = FakeVisionClient()
    service = ChatAgent(
        runtime_store=runtime_store,
        analysis_service=FakeAnalysisService(),
        vision_model_client=vision,
        window_summary_store=FakeWindowSummaryStore(),
    )

    await service.ask("预算会超吗")
    await asyncio.sleep(0.1)

    current = service.current()
    assert current is not None
    assert current.status == "error"
    assert "上下文预算超限" in str(current.error)
    assert vision.tool_probe_calls == []
    assert vision.calls == []
    stages = [
        payload["stage"] for name, payload in runtime_store.events
        if name == "assistant:interaction_trace"
    ]
    assert "context_budget.over_limit" in stages


@pytest.mark.anyio
async def test_tool_result_can_trigger_next_stream_budget_guard(monkeypatch) -> None:
    runtime_store = FakeRuntimeStore()
    _install_services(monkeypatch)
    settings = chat_module.get_settings()
    monkeypatch.setattr(settings, "minicpm_ctx_size", 9400)
    monkeypatch.setattr(settings, "answer_max_tokens", 0)
    monkeypatch.setattr(settings, "tool_result_item_budget_tokens", 3000)
    monkeypatch.setattr(settings, "tool_result_budget_tokens", 3000)
    monkeypatch.setattr(chat_module, "AgentToolRuntime", LargeToolRuntime)
    stream_tool_calls = [
        {
            "id": "call_stream_budget_guard",
            "type": "function",
            "function": {
                "name": "memory_search",
                "arguments": "{\"query\": \"当前窗口\"}",
            },
        }
    ]
    vision = FakeVisionClient(tool_calls=None, stream_tool_calls=stream_tool_calls)
    service = ChatAgent(
        runtime_store=runtime_store,
        analysis_service=FakeAnalysisService(),
        vision_model_client=vision,
        window_summary_store=FakeWindowSummaryStore(),
    )
    service._get_profile_packet = lambda: ""  # type: ignore[method-assign]

    await service.ask("当前窗口")
    await asyncio.sleep(0.1)

    current = service.current()
    assert current is not None
    assert current.status == "error"
    assert "上下文预算超限" in str(current.error)
    assert len(vision.calls) == 1
    traces = [
        payload for name, payload in runtime_store.events
        if name == "assistant:interaction_trace"
    ]
    assert any(item["stage"] == "tool_result_budget" for item in traces)
    over = [item for item in traces if item["stage"] == "context_budget.over_limit"]
    assert over
    assert over[-1]["payload"]["phase"] == "stream"
    assert over[-1]["payload"]["stream_round"] == 1