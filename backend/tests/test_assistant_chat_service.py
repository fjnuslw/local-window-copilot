from __future__ import annotations

import asyncio
import base64
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.schemas.analyze import WindowAnalysis, WindowAnalysisResult
from app.schemas.window import RawWindowCapture, WindowBounds
from app.services import assistant_chat as chat_module
from app.services.assistant_chat import CHAT_CURRENT_KEY, CHAT_HISTORY_KEY, ChatAgent


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
        summary: str = "Current window is an IDE.",
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


class FakeVisionClient:
    def __init__(self, plan_response: str | None = None) -> None:
        self.plan_response = plan_response or "none"
        self.plan_calls: list[list[dict[str, object]]] = []
        self.calls: list[dict[str, object]] = []
        self.visual_calls: list[dict[str, object]] = []

    def complete_chat(self, *, messages, temperature=None, max_tokens=None):
        self.plan_calls.append(messages)
        return self.plan_response

    def stream_chat(self, *, messages, image_path=None, image_long_edge=None):
        self.calls.append({
            "messages": messages,
            "image_path": image_path,
            "image_long_edge": image_long_edge,
        })
        yield "第一段"
        yield "，第二段。"

    def stream_visual_answer(self, *, question, image_path, visual_prompt, image_long_edge=None):
        self.visual_calls.append({
            "question": question,
            "image_path": image_path,
            "visual_prompt": visual_prompt,
            "image_long_edge": image_long_edge,
        })
        yield "视觉回答第一段"
        yield "，视觉回答第二段。"


def _plan(*calls: str) -> str:
    return calls[0] if calls else "none"


def _screen_look(question: str) -> str:
    return "screen.look"


def _memory_search(query: str) -> str:
    return "memory.search"


@pytest.mark.anyio
async def test_chat_question_pauses_streams_and_resume_restarts(monkeypatch) -> None:
    runtime_store = FakeRuntimeStore()
    watcher = FakeWatcher()
    state = FakeStateService()
    monkeypatch.setattr(chat_module, "get_window_watcher_service", lambda: watcher)
    monkeypatch.setattr(chat_module, "get_assistant_state_service", lambda: state)

    service = ChatAgent(
        runtime_store=runtime_store,
        analysis_service=FakeAnalysisService(),
        vision_model_client=FakeVisionClient(),
    )

    session = await service.ask("这个窗口在做什么？")
    await asyncio.sleep(0.1)

    assert watcher.stopped == 1
    assert session.question == "这个窗口在做什么？"
    current = service.current()
    assert current is not None
    assert current.answer == "第一段，第二段。"
    assert current.status == "done"
    history = service.history(limit=5)
    assert len(history) == 1
    assert history[0].session_id == current.session_id
    assert history[0].question == session.question
    assert history[0].answer == current.answer

    await service.resume_auto_watch()

    assert watcher.started == 1
    assert service.current() is None
    assert runtime_store.deleted == ["assistant:chat:current"]


@pytest.mark.anyio
async def test_chat_archives_finished_current_before_new_short_reply(monkeypatch) -> None:
    runtime_store = FakeRuntimeStore()
    now = datetime.now(UTC)
    runtime_store.data[CHAT_CURRENT_KEY] = {
        "session_id": "previous-current",
        "question": "帮我看看继续问什么比较好",
        "answer": "要不要我帮你分析当前任务进展或下一步计划？",
        "status": "done",
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }
    watcher = FakeWatcher()
    state = FakeStateService()
    vision = FakeVisionClient()
    monkeypatch.setattr(chat_module, "get_window_watcher_service", lambda: watcher)
    monkeypatch.setattr(chat_module, "get_assistant_state_service", lambda: state)

    service = ChatAgent(
        runtime_store=runtime_store,
        analysis_service=FakeAnalysisService(),
        vision_model_client=vision,
    )

    await service.ask("可以")
    await asyncio.sleep(0.1)

    history = runtime_store.data[CHAT_HISTORY_KEY]
    assert isinstance(history, list)
    assert any(item["session_id"] == "previous-current" for item in history)
    assert vision.plan_calls
    planner_prompt = "\n".join(str(m["content"]) for m in vision.plan_calls[-1])
    assert "帮我看看继续问什么比较好" in planner_prompt
    assert "可以" in planner_prompt
    assert "对话承接提示" not in planner_prompt
    assert vision.calls
    answer_prompt = "\n".join(str(m["content"]) for m in vision.calls[-1]["messages"])
    assert "要不要我帮你分析当前任务进展" in answer_prompt
    assert "可以" in answer_prompt
    assert "对话承接提示" not in answer_prompt

@pytest.mark.anyio
async def test_chat_context_prefers_current_window_metadata_and_filters_pollution(monkeypatch) -> None:
    runtime_store = FakeRuntimeStore()
    now = datetime.now(UTC)
    runtime_store.data[CHAT_HISTORY_KEY] = [
        {
            "session_id": "polluted",
            "question": "当前窗口叫什么？",
            "answer": "当前窗口的名字是对话工作台。",
            "status": "done",
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        },
        {
            "session_id": "clean",
            "question": "这个页面在讲什么？",
            "answer": "它在讲 KV cache 和 profile 分层。",
            "status": "done",
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        },
    ]
    watcher = FakeWatcher()
    state = FakeStateService()
    vision = FakeVisionClient(_plan(_memory_search("当前这个窗口的名字是什么？")))
    monkeypatch.setattr(chat_module, "get_window_watcher_service", lambda: watcher)
    monkeypatch.setattr(chat_module, "get_assistant_state_service", lambda: state)

    service = ChatAgent(
        runtime_store=runtime_store,
        analysis_service=FakeAnalysisService(
            app_name="chrome.exe",
            window_title="KV Cache Profile/Agent split - Codex",
            window_type="webpage",
            summary="当前窗口是 Codex 文档页。",
        ),
        vision_model_client=vision,
    )

    await service.ask("当前这个窗口的名字是什么？")
    await asyncio.sleep(0.1)

    assert vision.plan_calls
    assert vision.calls
    messages = vision.calls[-1]["messages"]
    joined = "\n".join(str(message["content"]) for message in messages)
    assert "KV Cache Profile/Agent split - Codex" in joined
    assert "当前窗口索引" in joined
    assert "对话工作台" not in joined
    assert "它在讲 KV cache 和 profile 分层" in joined


@pytest.mark.anyio
async def test_chat_rejects_latest_local_copilot_window(monkeypatch) -> None:
    runtime_store = FakeRuntimeStore()
    watcher = FakeWatcher()
    state = FakeStateService()
    vision = FakeVisionClient(_plan(_screen_look("当前这个窗口的名字是什么？")))
    monkeypatch.setattr(chat_module, "get_window_watcher_service", lambda: watcher)
    monkeypatch.setattr(chat_module, "get_assistant_state_service", lambda: state)

    service = ChatAgent(
        runtime_store=runtime_store,
        analysis_service=FakeAnalysisService(window_title="AlertWindow"),
        vision_model_client=vision,
    )

    await service.ask("当前这个窗口的名字是什么？")
    await asyncio.sleep(0.1)

    current = service.current()
    assert current is not None
    assert "不能当作用户窗口" in current.answer
    assert vision.calls == []
    assert vision.visual_calls == []


@pytest.mark.anyio
async def test_chat_visual_question_routes_to_visual_answer(tmp_path, monkeypatch) -> None:
    """明确视觉类问题应越过 planner，直接用注册的 screen.look 看截图。"""
    screenshot = tmp_path / "capture.png"
    screenshot.write_bytes(b"fake-image")
    runtime_store = FakeRuntimeStore()
    watcher = FakeWatcher()
    state = FakeStateService()
    vision = FakeVisionClient(_plan(_screen_look("页面里有什么内容？")))
    monkeypatch.setattr(chat_module, "get_window_watcher_service", lambda: watcher)
    monkeypatch.setattr(chat_module, "get_assistant_state_service", lambda: state)

    service = ChatAgent(
        runtime_store=runtime_store,
        analysis_service=FakeAnalysisService(screenshot_path=screenshot),
        vision_model_client=vision,
    )

    await service.ask("页面里有什么内容？")
    await asyncio.sleep(0.1)

    assert vision.plan_calls, "高置信视觉问题不应依赖小模型 planner"
    assert vision.visual_calls, "screen.look 应调用 stream_visual_answer"
    assert vision.calls, "visual tool result should be passed to answer model"
    call = vision.visual_calls[-1]
    assert call["question"] == "页面里有什么内容？"
    assert call["image_path"] == screenshot
    assert call["image_long_edge"] == 1536
    assert "视觉追问" in call["visual_prompt"] or "视觉" in call["visual_prompt"]
    current = service.current()
    assert current is not None
    assert current.answer == "第一段，第二段。"
    assert current.status == "done"
    trace_events = [
        payload for name, payload in runtime_store.events
        if name == "assistant:interaction_trace"
    ]
    stages = [event["stage"] for event in trace_events]
    assert "planner_raw_response" in stages
    assert "planner_parsed" in stages
    assert "answer_messages" in stages


@pytest.mark.anyio
async def test_chat_user_image_routes_to_screen_tool(tmp_path, monkeypatch) -> None:
    upload_dir = tmp_path / "uploads"
    settings = chat_module.get_settings()
    monkeypatch.setattr(settings, "chat_upload_dir", upload_dir)
    monkeypatch.setattr(settings, "chat_image_max_bytes", 1024)
    runtime_store = FakeRuntimeStore()
    watcher = FakeWatcher()
    state = FakeStateService()
    vision = FakeVisionClient(_plan(_screen_look("这张图里有什么")))
    monkeypatch.setattr(chat_module, "get_window_watcher_service", lambda: watcher)
    monkeypatch.setattr(chat_module, "get_assistant_state_service", lambda: state)

    service = ChatAgent(
        runtime_store=runtime_store,
        analysis_service=FakeAnalysisService(window_title="AlertWindow"),
        vision_model_client=vision,
    )

    encoded = base64.b64encode(b"fake-image").decode("ascii")
    await service.ask(
        "这张图里有什么",
        image_base64=encoded,
        image_name="clip.png",
        image_mime="image/png",
    )
    await asyncio.sleep(0.1)

    current = service.current()
    assert current is not None
    assert current.image_name == "clip.png"
    assert current.image_path is not None
    assert Path(current.image_path).exists()
    assert vision.visual_calls
    assert vision.visual_calls[-1]["image_path"] == Path(current.image_path)
    assert vision.calls
    assert current.answer == "第一段，第二段。"


@pytest.mark.anyio
async def test_chat_specific_content_followup_uses_visual_tool_then_answer_model(tmp_path, monkeypatch) -> None:
    screenshot = tmp_path / "capture.png"
    screenshot.write_bytes(b"fake-image")
    runtime_store = FakeRuntimeStore()
    watcher = FakeWatcher()
    state = FakeStateService()
    vision = FakeVisionClient(_plan(_screen_look("里面有什么具体内容呢")))
    monkeypatch.setattr(chat_module, "get_window_watcher_service", lambda: watcher)
    monkeypatch.setattr(chat_module, "get_assistant_state_service", lambda: state)

    service = ChatAgent(
        runtime_store=runtime_store,
        analysis_service=FakeAnalysisService(screenshot_path=screenshot),
        vision_model_client=vision,
    )

    await service.ask("里面有什么具体内容呢")
    await asyncio.sleep(0.1)

    assert vision.plan_calls
    assert vision.visual_calls
    assert vision.calls
    current = service.current()
    assert current is not None
    assert current.answer == "第一段，第二段。"
    stages = [
        payload["stage"] for name, payload in runtime_store.events
        if name == "assistant:interaction_trace"
    ]
    assert "answer_messages" in stages

@pytest.mark.anyio
async def test_chat_accepts_prefixed_planner_tool_name(tmp_path, monkeypatch) -> None:
    screenshot = tmp_path / "capture.png"
    screenshot.write_bytes(b"fake-image")
    runtime_store = FakeRuntimeStore()
    watcher = FakeWatcher()
    state = FakeStateService()
    vision = FakeVisionClient("current_step screen.look")
    monkeypatch.setattr(chat_module, "get_window_watcher_service", lambda: watcher)
    monkeypatch.setattr(chat_module, "get_assistant_state_service", lambda: state)

    service = ChatAgent(
        runtime_store=runtime_store,
        analysis_service=FakeAnalysisService(screenshot_path=screenshot),
        vision_model_client=vision,
    )

    await service.ask("当前进行到哪一步了")
    await asyncio.sleep(0.1)

    assert vision.plan_calls, "非高置信视觉问题应仍先经过 planner"
    assert vision.visual_calls, "解析出 screen.look 后应执行视觉工具"
    assert vision.calls
    current = service.current()
    assert current is not None
    assert current.answer == "第一段，第二段。"
    assert current.status == "done"

@pytest.mark.anyio
async def test_chat_text_question_still_uses_stream_chat(monkeypatch) -> None:
    """普通文本问题应仍走 stream_chat（KV cache 友好的分层 messages）。"""
    runtime_store = FakeRuntimeStore()
    watcher = FakeWatcher()
    state = FakeStateService()
    vision = FakeVisionClient()
    monkeypatch.setattr(chat_module, "get_window_watcher_service", lambda: watcher)
    monkeypatch.setattr(chat_module, "get_assistant_state_service", lambda: state)

    service = ChatAgent(
        runtime_store=runtime_store,
        analysis_service=FakeAnalysisService(),
        vision_model_client=vision,
    )

    await service.ask("这个项目怎么部署？")
    await asyncio.sleep(0.1)

    assert vision.plan_calls, "普通问题也先经过工具规划器"
    # 无工具时走陪伴/普通 stream_chat，不调用视觉工具
    assert vision.calls, "文本问题应调用 stream_chat"
    assert vision.visual_calls == [], "文本问题不应调用 stream_visual_answer"


@pytest.mark.anyio
async def test_chat_visual_question_requires_available_screenshot(
    tmp_path, monkeypatch
) -> None:
    """视觉问题但截图文件不存在时，明确结束，不走文本回答。"""
    runtime_store = FakeRuntimeStore()
    watcher = FakeWatcher()
    state = FakeStateService()
    vision = FakeVisionClient(_plan(_screen_look("页面里有什么？")))
    monkeypatch.setattr(chat_module, "get_window_watcher_service", lambda: watcher)
    monkeypatch.setattr(chat_module, "get_assistant_state_service", lambda: state)

    service = ChatAgent(
        runtime_store=runtime_store,
        # 截图路径不存在
        analysis_service=FakeAnalysisService(screenshot_path=tmp_path / "nonexistent.png"),
        vision_model_client=vision,
    )

    await service.ask("页面里有什么？")
    await asyncio.sleep(0.1)

    assert vision.calls == [], "缺少截图时不应调用 stream_chat"
    assert vision.visual_calls == []
    assert vision.plan_calls
    current = service.current()
    assert current is not None
    assert "没有可用的目标窗口截图" in current.answer
    assert current.status == "done"


@pytest.mark.anyio
async def test_chat_companion_question_routes_to_companion_mode(monkeypatch) -> None:
    """陪伴类问题（如"我觉得不对"）应走 companion 模式，不注入窗口观察。"""
    runtime_store = FakeRuntimeStore()
    watcher = FakeWatcher()
    state = FakeStateService()
    vision = FakeVisionClient()
    monkeypatch.setattr(chat_module, "get_window_watcher_service", lambda: watcher)
    monkeypatch.setattr(chat_module, "get_assistant_state_service", lambda: state)

    service = ChatAgent(
        runtime_store=runtime_store,
        analysis_service=FakeAnalysisService(),
        vision_model_client=vision,
    )

    await service.ask("我觉得这个方向不对")
    await asyncio.sleep(0.1)

    # 陪伴问题应走 stream_chat（companion 模式也用 stream_chat）
    assert vision.calls, "陪伴问题应调用 stream_chat"
    assert vision.visual_calls == [], "陪伴问题不应调用 stream_visual_answer"
    messages = vision.calls[-1]["messages"]
    joined = "\n".join(str(m["content"]) for m in messages)
    # companion 模式不注入窗口观察
    assert "当前窗口观察" not in joined
    assert "当前窗口元信息" not in joined
    # companion 模式使用 companion prompt 作为 system 消息
    assert "桌面伙伴" in str(messages[0]["content"]) or "陪伴" in str(messages[0]["content"])
    current = service.current()
    assert current is not None
    assert current.status == "done"


@pytest.mark.anyio
async def test_chat_companion_works_without_window_analysis(monkeypatch) -> None:
    """陪伴模式不需要窗口分析结果，可以直接回应。"""
    runtime_store = FakeRuntimeStore()
    watcher = FakeWatcher()
    state = FakeStateService()
    vision = FakeVisionClient()
    monkeypatch.setattr(chat_module, "get_window_watcher_service", lambda: watcher)
    monkeypatch.setattr(chat_module, "get_assistant_state_service", lambda: state)

    class NoAnalysisService:
        def get_latest(self):
            return None

    service = ChatAgent(
        runtime_store=runtime_store,
        analysis_service=NoAnalysisService(),
        vision_model_client=vision,
    )

    await service.ask("我觉得这个方向不对")
    await asyncio.sleep(0.1)

    # 陪伴模式不需要 latest_analysis
    assert vision.calls, "陪伴模式应直接调用 stream_chat"
    current = service.current()
    assert current is not None
    assert current.status == "done"


@pytest.mark.anyio
async def test_chat_screen_request_uses_registered_screen_tool(tmp_path, monkeypatch) -> None:
    """看屏幕类问题应进入注册的 screen.look，而不是旧硬编码回答分叉。"""
    runtime_store = FakeRuntimeStore()
    watcher = FakeWatcher()
    state = FakeStateService()
    vision = FakeVisionClient(_plan(_screen_look("帮我分析一下当前页面")))
    screenshot = tmp_path / "screen.png"
    screenshot.write_bytes(b"fake-image")
    monkeypatch.setattr(chat_module, "get_window_watcher_service", lambda: watcher)
    monkeypatch.setattr(chat_module, "get_assistant_state_service", lambda: state)

    service = ChatAgent(
        runtime_store=runtime_store,
        analysis_service=FakeAnalysisService(
            app_name="chrome.exe",
            window_title="Some Page - Chrome",
            summary="这是一个网页。",
            screenshot_path=screenshot,
        ),
        vision_model_client=vision,
    )

    await service.ask("帮我分析一下当前页面")
    await asyncio.sleep(0.1)

    assert vision.plan_calls, "高置信视觉问题不应依赖小模型 planner"
    assert vision.visual_calls, "screen.look 应执行视觉细看"
    assert vision.calls, "visual tool result should be passed to answer model"
    assert vision.visual_calls[-1]["image_long_edge"] == 1536
    current = service.current()
    assert current is not None
    assert current.answer == "第一段，第二段。"
    trace_events = [
        payload for name, payload in runtime_store.events
        if name == "assistant:interaction_trace"
    ]
    stages = [event["stage"] for event in trace_events]
    assert "planner_raw_response" in stages
    assert "planner_parsed" in stages
    assert "tool_results" in stages
    assert "answer_messages" in stages
    assert "session_finished" in stages


@pytest.mark.anyio
async def test_chat_records_user_goal_for_direction_reflection(monkeypatch) -> None:
    """产品方向反思类问题应被记录到 user_goals（spec §6.2）。"""
    runtime_store = FakeRuntimeStore()
    watcher = FakeWatcher()
    state = FakeStateService()
    vision = FakeVisionClient()
    monkeypatch.setattr(chat_module, "get_window_watcher_service", lambda: watcher)
    monkeypatch.setattr(chat_module, "get_assistant_state_service", lambda: state)

    service = ChatAgent(
        runtime_store=runtime_store,
        analysis_service=FakeAnalysisService(),
        vision_model_client=vision,
    )

    await service.ask("这个方向不对")
    await asyncio.sleep(0.1)

    goals = runtime_store.data.get("companion:user_goals")
    assert isinstance(goals, list)
    assert len(goals) == 1
    assert goals[0]["situation_label"] == "产品方向反思"
    assert goals[0]["user_mood_hint"] == "dissatisfied"


@pytest.mark.anyio
async def test_chat_does_not_record_goal_for_idle_chat(monkeypatch) -> None:
    """无意义的闲聊不应被记录到 user_goals。"""
    runtime_store = FakeRuntimeStore()
    watcher = FakeWatcher()
    state = FakeStateService()
    vision = FakeVisionClient()
    monkeypatch.setattr(chat_module, "get_window_watcher_service", lambda: watcher)
    monkeypatch.setattr(chat_module, "get_assistant_state_service", lambda: state)

    service = ChatAgent(
        runtime_store=runtime_store,
        analysis_service=FakeAnalysisService(),
        vision_model_client=vision,
    )

    # "继续" 不命中任何情绪/意图关键词，situation 为 ambient_idle + neutral + chat
    await service.ask("继续")
    await asyncio.sleep(0.1)

    goals = runtime_store.data.get("companion:user_goals")
    # ambient_idle + neutral + chat 不记录
    assert goals is None or goals == []


def test_inspect_context_includes_situation_and_proactive_nudge(monkeypatch) -> None:
    """inspect_context 应返回 situation 和 proactive_nudge 字段（spec §6.3 / §8.3）。"""
    runtime_store = FakeRuntimeStore()
    watcher = FakeWatcher()
    state = FakeStateService()
    monkeypatch.setattr(chat_module, "get_window_watcher_service", lambda: watcher)
    monkeypatch.setattr(chat_module, "get_assistant_state_service", lambda: state)

    service = ChatAgent(
        runtime_store=runtime_store,
        analysis_service=FakeAnalysisService(),
        vision_model_client=FakeVisionClient(),
    )

    context = service.inspect_context("这个方向不对")

    # situation 字段包含 spec §8.2 定义的全部字段
    situation = context["situation"]
    assert "situation_label" in situation
    assert "user_mood_hint" in situation
    assert "likely_intent" in situation
    assert "interrupt_policy" in situation
    assert "companion_line" in situation
    assert situation["situation_label"] == "产品方向反思"

    # proactive_nudge 字段
    nudge = context["proactive_nudge"]
    assert "should_speak" in nudge
    assert "line" in nudge

    # user_goals 字段
    assert "user_goals" in context
    assert isinstance(context["user_goals"], list)


def test_inspect_context_reports_registered_agent_tools(tmp_path, monkeypatch) -> None:
    """预览不再跑硬编码四路分类，而是展示 agent 工具层。"""
    runtime_store = FakeRuntimeStore()
    watcher = FakeWatcher()
    state = FakeStateService()
    monkeypatch.setattr(chat_module, "get_window_watcher_service", lambda: watcher)
    monkeypatch.setattr(chat_module, "get_assistant_state_service", lambda: state)

    service = ChatAgent(
        runtime_store=runtime_store,
        analysis_service=FakeAnalysisService(screenshot_path=tmp_path / "missing.png"),
        vision_model_client=FakeVisionClient(),
    )

    context = service.inspect_context("页面里有什么？")

    assert context["answer_mode"] == "agent_orchestrated"
    assert context["usage"]["answer_mode"] == "agent_orchestrated"
    assert context["usage"]["registered_tool_count"] == 3
    assert [tool["name"] for tool in context["registered_tools"]] == [
        "screen.look",
        "memory.search",
        "memory.remember",
    ]
    assert context["selected_image"] is None
