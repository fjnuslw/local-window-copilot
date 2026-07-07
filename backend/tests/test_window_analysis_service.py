from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from app.schemas.analyze import CandidateQuestion, VisionInput, WindowAnalysis
from app.schemas.window import RawWindowCapture, WindowBounds
from app.services.observation_builder import ObservationBuilder
from app.services.vision_model_client import extract_json_object, extract_response_info, parse_window_analysis
from app.services.window_analysis import ObservationAgent


class FakeRuntimeManager:
    def __init__(self) -> None:
        self.ensure_calls = 0

    def ensure_server_ready(self) -> None:
        self.ensure_calls += 1


class FakeVisionModelClient:
    """模拟视觉模型客户端。

    analyze_image 返回 (WindowAnalysis, VisionInput) 元组，
    与真实 VisionModelClient.analyze_image 的签名保持一致。
    """

    def __init__(
        self,
        analysis: WindowAnalysis,
        *,
        vision_input: VisionInput | None = None,
    ) -> None:
        self.analysis = analysis
        self.endpoint = "http://127.0.0.1:18181/v1/chat/completions"
        self.image_paths: list[Path] = []
        self.vision_input = vision_input or VisionInput(
            original_size=[1822, 827],
            sent_size=[1024, 465],
            long_edge=1024,
            detail_mode="standard",
        )

    def analyze_image(self, image_path: Path) -> tuple[WindowAnalysis, VisionInput]:
        self.image_paths.append(image_path)
        return self.analysis, self.vision_input


class FakeRuntimeStore:
    def __init__(self) -> None:
        self.data: dict[str, object] = {}
        self.events: list[tuple[str, object]] = []
        self.ttls: dict[str, int | None] = {}

    def set_json(
        self,
        name: str,
        payload: object,
        *,
        ttl_seconds: int | None = None,
    ) -> None:
        self.data[name] = payload
        self.ttls[name] = ttl_seconds

    def get_json(self, name: str) -> object | None:
        return self.data.get(name)

    def record_event(self, name: str, payload: object) -> bool:
        self.events.append((name, payload))
        return True

    def delete(self, name: str) -> None:
        self.data.pop(name, None)
        self.ttls.pop(name, None)


def make_capture(image_path: Path, screenshot_hash: str = "abc123") -> RawWindowCapture:
    image_path.write_bytes(b"fake-image")
    return RawWindowCapture(
        app_name="Code.exe",
        process_id=1234,
        window_title="README.md - Visual Studio Code",
        window_bounds=WindowBounds(left=10, top=20, right=810, bottom=620),
        screenshot_path=image_path,
        screenshot_hash=screenshot_hash,
        captured_at=datetime.now(UTC),
    )


def test_extract_response_info_separates_reasoning_content() -> None:
    response = {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "role": "assistant",
                    "reasoning_content": "internal thinking",
                    "content": "{\"window_type\":\"document\",\"summary\":\"ok\"}",
                },
            }
        ],
        "usage": {"completion_tokens": 12},
    }

    info = extract_response_info(response)

    assert info["content"].startswith("{")
    assert info["reasoning_content"] == "internal thinking"
    assert info["finish_reason"] == "stop"
    assert info["usage"]["completion_tokens"] == 12

def test_parse_window_analysis_extracts_json_after_think_tags() -> None:
    raw = """
<think>internal reasoning that should be ignored</think>
{
  "window_type": "ide",
  "summary": "Current window is an IDE.",
  "key_points": ["editor", "project files", "tests"],
  "candidate_questions": [
    {
      "question": "How should I verify this change?",
      "category": "testing",
      "reason": "The window includes code and test context.",
      "priority": 0.8
    }
  ],
  "caution": null
}
"""

    json_text = extract_json_object(raw)
    parsed = parse_window_analysis(raw)

    assert json_text.startswith("{")
    assert parsed.window_type == "ide"
    assert parsed.candidate_questions[0].question == "How should I verify this change?"


def test_window_analysis_service_stores_latest_summary_in_runtime_store(tmp_path) -> None:
    capture = make_capture(tmp_path / "capture.png")
    analysis = WindowAnalysis(
        window_type="ide",
        summary="Current window is VS Code with the project README open.",
        key_points=["README is visible", "backend work is in progress", "model integration exists"],
        candidate_questions=[
            CandidateQuestion(
                question="What should be implemented next?",
                category="development",
                reason="The current context is a development workspace.",
                priority=0.9,
            )
        ],
        caution=None,
    )
    runtime = FakeRuntimeManager()
    client = FakeVisionModelClient(analysis)
    runtime_store = FakeRuntimeStore()
    service = ObservationAgent(
        runtime_manager=runtime,
        vision_model_client=client,
        runtime_store=runtime_store,
        latest_analysis_ttl_seconds=60,
    )

    result = service.analyze_capture(capture)

    assert runtime.ensure_calls == 1
    assert client.image_paths == [capture.screenshot_path]
    assert result.capture.screenshot_hash == "abc123"
    assert result.analysis.summary == "Current window is VS Code with the project README open."
    assert "window:latest_analysis" in runtime_store.data
    assert runtime_store.ttls["window:latest_analysis"] == 60
    assert runtime_store.events[-1][0] == "window:analysis"
    assert service.get_latest().analysis.summary == "Current window is VS Code with the project README open."


def test_window_analysis_service_does_not_pause_on_title_keywords(tmp_path) -> None:
    capture = make_capture(tmp_path / "capture.png")
    capture.window_title = "Payment password"
    runtime = FakeRuntimeManager()
    client = FakeVisionModelClient(
        WindowAnalysis(
            window_type="ide",
            summary="Analyzed by model.",
            key_points=[],
            candidate_questions=[],
        )
    )
    runtime_store = FakeRuntimeStore()
    service = ObservationAgent(
        runtime_manager=runtime,
        vision_model_client=client,
        runtime_store=runtime_store,
        observation_builder=ObservationBuilder(),
    )

    result = service.analyze_capture(capture)

    assert runtime.ensure_calls == 1
    assert client.image_paths == [capture.screenshot_path]
    assert result.observation is not None
    assert result.observation.privacy_state == "normal"
    assert result.analysis.summary == "Analyzed by model."
    assert runtime_store.data["window:latest_analysis"]["observation"]["privacy_state"] == "normal"


def test_window_analysis_service_records_vision_input_metadata(tmp_path) -> None:
    """分析结果必须记录 original_size / sent_size / long_edge / detail_mode，
    便于调试图片缩放对识别质量的影响。"""
    capture = make_capture(tmp_path / "capture.png")
    analysis = WindowAnalysis(
        window_type="ide",
        summary="VS Code with README.",
        key_points=["editor"],
        candidate_questions=[],
    )
    expected_vision = VisionInput(
        original_size=[1822, 827],
        sent_size=[1024, 465],
        long_edge=1024,
        detail_mode="standard",
    )
    runtime = FakeRuntimeManager()
    client = FakeVisionModelClient(analysis, vision_input=expected_vision)
    runtime_store = FakeRuntimeStore()
    service = ObservationAgent(
        runtime_manager=runtime,
        vision_model_client=client,
        runtime_store=runtime_store,
        latest_analysis_ttl_seconds=60,
    )

    result = service.analyze_capture(capture)

    assert result.vision_input is not None
    assert result.vision_input.original_size == [1822, 827]
    assert result.vision_input.sent_size == [1024, 465]
    assert result.vision_input.long_edge == 1024
    assert result.vision_input.detail_mode == "standard"
    # 同时写入 runtime_store，便于后续追溯
    stored = runtime_store.data["window:latest_analysis"]
    assert stored["vision_input"]["original_size"] == [1822, 827]
    assert stored["vision_input"]["sent_size"] == [1024, 465]


def test_window_analysis_service_records_screenshot_path_in_summary_store(tmp_path) -> None:
    """WindowSummaryStore 必须保存 screenshot_path / screenshot_hash / window_bounds，
    以便视觉追问时根据观察找到对应截图。"""
    from app.services.window_summary_store import WindowSummaryStore

    capture = make_capture(tmp_path / "capture.png", screenshot_hash="hash-xyz")
    analysis = WindowAnalysis(
        window_type="ide",
        summary="VS Code with project open.",
        key_points=["README", "backend"],
        candidate_questions=[],
        regions=[
            {
                "name": "中间编辑区",
                "location": "窗口中央",
                "content": "README 和 backend 代码可见。",
                "visible_text": ["README", "backend"],
                "ui_elements": ["编辑器", "滚动条"],
            }
        ],
        visible_text=["README", "backend"],
        ui_elements=["编辑器", "滚动条"],
        entities=["VS Code", "README"],
        uncertain_areas=["底部状态栏小字不清晰"],
    )
    runtime = FakeRuntimeManager()
    client = FakeVisionModelClient(analysis)
    runtime_store = FakeRuntimeStore()
    summary_store = WindowSummaryStore(runtime_store=runtime_store, history_limit=10)
    service = ObservationAgent(
        runtime_manager=runtime,
        vision_model_client=client,
        runtime_store=runtime_store,
        window_summary_store=summary_store,
        latest_analysis_ttl_seconds=60,
    )

    service.analyze_capture(capture)

    items = summary_store.recent(limit=5)
    assert len(items) == 1
    record = items[0]
    assert record["screenshot_path"] == str(capture.screenshot_path)
    assert record["screenshot_hash"] == "hash-xyz"
    assert record["window_bounds"] == {"left": 10, "top": 20, "right": 810, "bottom": 620}
    assert record["process_id"] == 1234
    assert record["vision_input"]["long_edge"] == 1024
    assert record["regions"][0]["name"] == "中间编辑区"
    assert record["visible_text"] == ["README", "backend"]
    assert record["entities"] == ["VS Code", "README"]
    # find_by_screenshot_hash 应能回查
    found = summary_store.find_by_screenshot_hash("hash-xyz")
    assert found is not None
    assert found["screenshot_path"] == str(capture.screenshot_path)
    # 找不到时返回 None
    assert summary_store.find_by_screenshot_hash("nonexistent") is None


def test_window_analysis_service_does_not_write_analysis_summary_to_memory(tmp_path) -> None:
    class FakeMemoryService:
        def __init__(self) -> None:
            self.saved = []
            self.remembered = []

        def save_observation(self, observation):
            self.saved.append(observation)

        def remember_analysis(self, **kwargs):
            self.remembered.append(kwargs)

    capture = make_capture(tmp_path / "capture.png")
    analysis = WindowAnalysis(
        window_type="ide",
        summary="VS Code with project open.",
        key_points=["README"],
        candidate_questions=[],
    )
    memory_service = FakeMemoryService()
    service = ObservationAgent(
        runtime_manager=FakeRuntimeManager(),
        vision_model_client=FakeVisionModelClient(analysis),
        runtime_store=FakeRuntimeStore(),
        observation_builder=ObservationBuilder(),
        memory_service=memory_service,
    )

    service.analyze_capture(capture)

    assert len(memory_service.saved) == 1
    assert memory_service.remembered == []


def test_parse_window_analysis_backfills_global_fields_from_regions() -> None:
    raw = """
{
  "window_type": "webpage",
  "summary": "控制台页面。",
  "regions": [
    {
      "name": "顶部栏",
      "location": "顶部",
      "content": "标题和刷新按钮可见。",
      "visible_text": ["Local Window Copilot", "刷新"],
      "ui_elements": ["标题栏", "刷新按钮"],
      "uncertainty": null
    }
  ],
  "visible_text": [],
  "ui_elements": [],
  "entities": ["Local Window Copilot"],
  "key_points": ["控制台页面"],
  "candidate_questions": [],
  "caution": null,
  "uncertain_areas": []
}
"""

    parsed = parse_window_analysis(raw)

    assert parsed.visible_text == ["Local Window Copilot", "刷新"]
    assert parsed.ui_elements == ["标题栏", "刷新按钮"]

def test_parse_window_analysis_allows_omitted_legacy_candidate_questions() -> None:
    raw = """
{
  "window_type": "document",
  "summary": "当前窗口显示文档内容。",
  "key_points": ["文档正文可见"],
  "regions": [],
  "visible_text": ["Agility RAG"],
  "ui_elements": [],
  "entities": ["Agility RAG"],
  "uncertain_areas": []
}
"""

    parsed = parse_window_analysis(raw)

    assert parsed.window_type == "document"
    assert parsed.candidate_questions == []
    assert parsed.visible_text == ["Agility RAG"]

def test_window_analysis_service_handles_uncertain_areas(tmp_path) -> None:
    """WindowAnalysis.uncertain_areas 字段应能正确解析模型输出。"""
    raw = """
{
  "window_type": "webpage",
  "summary": "网页内容描述。",
  "regions": [
    {
      "name": "顶部导航",
      "location": "窗口顶部",
      "content": "包含标题和导航按钮。",
      "visible_text": ["首页", "设置"],
      "ui_elements": ["导航按钮"],
      "uncertainty": null
    }
  ],
  "visible_text": ["首页", "设置"],
  "ui_elements": ["导航按钮"],
  "entities": ["设置"],
  "key_points": ["标题", "导航栏"],
  "candidate_questions": [],
  "caution": null,
  "uncertain_areas": ["底部小字不清晰", "右侧广告位被遮挡"]
}
"""
    parsed = parse_window_analysis(raw)
    assert parsed.uncertain_areas == ["底部小字不清晰", "右侧广告位被遮挡"]
    assert parsed.regions[0].name == "顶部导航"
    assert parsed.visible_text == ["首页", "设置"]
    assert parsed.entities == ["设置"]

    # 缺省值应为空列表
    raw_no_uncertain = """
{
  "window_type": "ide",
  "summary": "IDE",
  "key_points": [],
  "candidate_questions": []
}
"""
    parsed_no_uncertain = parse_window_analysis(raw_no_uncertain)
    assert parsed_no_uncertain.uncertain_areas == []
