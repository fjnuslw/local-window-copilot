from __future__ import annotations

import copy
import json
from datetime import UTC, datetime

import pytest

from app.services.context_summary import (
    COMPACT_LOCK_KEY,
    COMPACT_METRICS_KEY,
    ROLLING_SUMMARY_KEY,
    CompactPlannerConfig,
    CompactStateStore,
    build_rolling_summary_state,
    compact_execution_result_to_dict,
    execute_compact,
)


FIXED_NOW = datetime(2026, 7, 6, 12, 0, 0, tzinfo=UTC)


class FakeRuntimeStore:
    def __init__(self) -> None:
        self.data: dict[str, object] = {}
        self.deleted: list[str] = []

    def set_json(self, name: str, payload: object, *, ttl_seconds: int | None = None) -> None:
        self.data[name] = copy.deepcopy(payload)

    def get_json(self, name: str) -> object | None:
        if name not in self.data:
            return None
        return copy.deepcopy(self.data[name])

    def delete(self, name: str) -> None:
        self.deleted.append(name)
        self.data.pop(name, None)


class FakeModelClient:
    def __init__(self, text: str | None = None, error: Exception | None = None) -> None:
        self.text = text if text is not None else valid_summary()
        self.error = error
        self.calls: list[dict[str, object]] = []

    def complete_chat(
        self,
        *,
        messages: list[dict[str, str]],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        self.calls.append(
            {
                "messages": copy.deepcopy(messages),
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        )
        if self.error is not None:
            raise self.error
        return self.text


def make_session(i: int) -> dict[str, str]:
    return {
        "session_id": f"s{i}",
        "created_at": f"2026-07-06T12:{i:02d}:00+00:00",
        "question": f"question {i}",
        "answer": f"answer {i}",
    }


def make_sessions(n: int) -> list[dict[str, str]]:
    return [make_session(i) for i in range(n, 0, -1)]


def trigger_config() -> CompactPlannerConfig:
    return CompactPlannerConfig(
        raw_tail_turns=1,
        uncovered_session_threshold=1,
        history_trigger_tokens=10**9,
    )


def valid_summary() -> str:
    return "\n".join(
        [
            "## 当前任务",
            "继续推进 compact executor。",
            "## 当前判断",
            "plan、prompt、state 已具备。",
            "## 卡点",
            "需要验证提交边界。",
            "## 下一步检索指针",
            "- session_id: s1",
            "- 关键词: compact executor",
            "## 用户偏好",
            "直接、具体、少冗余。",
            "## 最近完成",
            "007 prompt builder 已完成。",
        ]
    )


def test_execute_compact_success_commits_summary_metrics_and_releases_lock():
    rt = FakeRuntimeStore()
    store = CompactStateStore(runtime_store=rt)
    model = FakeModelClient()

    result = execute_compact(
        sessions=make_sessions(2),
        state_store=store,
        model_client=model,
        planner_config=trigger_config(),
        now=FIXED_NOW,
    )

    assert result.status == "ok"
    assert result.attempted is True
    assert result.compacted is True
    assert result.trigger == "session_threshold"
    assert result.prompt is not None
    assert result.prompt.source_session_ids == ("s1",)
    assert result.summary_state.covered_session_ids == ("s1",)
    assert result.metrics.last_status == "ok"
    assert result.metrics.source_session_count == 1
    assert result.metrics.covered_session_count == 1
    assert result.metrics.summary_tokens > 0
    assert result.metrics.source_tokens == result.prompt.estimated_source_tokens
    assert "compact_succeeded" in result.actions
    assert len(model.calls) == 1
    assert model.calls[0]["temperature"] == 0.0
    assert ROLLING_SUMMARY_KEY in rt.data
    assert COMPACT_METRICS_KEY in rt.data
    assert COMPACT_LOCK_KEY not in rt.data
    assert COMPACT_LOCK_KEY in rt.deleted


def test_execute_compact_skipped_does_not_lock_or_call_model():
    rt = FakeRuntimeStore()
    store = CompactStateStore(runtime_store=rt)
    model = FakeModelClient()

    result = execute_compact(
        sessions=make_sessions(1),
        state_store=store,
        model_client=model,
        planner_config=trigger_config(),
        now=FIXED_NOW,
    )

    assert result.status == "skipped"
    assert result.attempted is False
    assert result.compacted is False
    assert result.prompt is None
    assert result.metrics.last_status == "idle"
    assert "no_compact_needed" in result.actions
    assert model.calls == []
    assert COMPACT_LOCK_KEY not in rt.data
    assert ROLLING_SUMMARY_KEY not in rt.data


def test_execute_compact_respects_active_lock():
    rt = FakeRuntimeStore()
    store = CompactStateStore(runtime_store=rt)
    active_lock = store.acquire_lock(source="auto", now=FIXED_NOW)
    assert active_lock is not None
    model = FakeModelClient()

    result = execute_compact(
        sessions=make_sessions(2),
        state_store=store,
        model_client=model,
        planner_config=trigger_config(),
        force=True,
        now=FIXED_NOW,
    )

    assert result.status == "locked"
    assert result.attempted is False
    assert result.compacted is False
    assert "lock_active" in result.actions
    assert model.calls == []
    assert rt.data[COMPACT_LOCK_KEY]["source"] == "auto"


def test_execute_compact_model_error_keeps_summary_and_writes_error_metrics():
    rt = FakeRuntimeStore()
    store = CompactStateStore(runtime_store=rt)
    previous = build_rolling_summary_state(
        summary=valid_summary(),
        covered_session_ids=["s0"],
        source_session_count=1,
        updated_at="2026-07-06T11:00:00+00:00",
    )
    store.save_summary(previous)
    model = FakeModelClient(error=RuntimeError("model request failed"))

    result = execute_compact(
        sessions=make_sessions(3),
        state_store=store,
        model_client=model,
        planner_config=trigger_config(),
        now=FIXED_NOW,
    )

    assert result.status == "error"
    assert result.attempted is True
    assert result.compacted is False
    assert result.summary_state == previous
    assert store.load_summary() == previous
    assert result.metrics.last_status == "error"
    assert result.metrics.error_type == "RuntimeError"
    assert result.metrics.error_message == "model request failed"
    assert result.metrics.covered_session_count == 1
    assert "compact_failed" in result.actions
    assert COMPACT_LOCK_KEY not in rt.data
    assert COMPACT_LOCK_KEY in rt.deleted


def test_execute_compact_invalid_summary_writes_error_metrics_without_summary_commit():
    rt = FakeRuntimeStore()
    store = CompactStateStore(runtime_store=rt)
    model = FakeModelClient(text="invalid summary")

    result = execute_compact(
        sessions=make_sessions(2),
        state_store=store,
        model_client=model,
        planner_config=trigger_config(),
        now=FIXED_NOW,
    )

    assert result.status == "error"
    assert result.error_type == "ValueError"
    assert result.metrics.last_status == "error"
    assert result.metrics.summary_tokens == 0
    assert result.prompt is not None
    assert ROLLING_SUMMARY_KEY not in rt.data
    assert COMPACT_LOCK_KEY not in rt.data


def test_execute_compact_manual_no_candidates_skips_with_plan_actions():
    rt = FakeRuntimeStore()
    store = CompactStateStore(runtime_store=rt)
    model = FakeModelClient()

    result = execute_compact(
        sessions=make_sessions(1),
        state_store=store,
        model_client=model,
        planner_config=trigger_config(),
        force=True,
        now=FIXED_NOW,
    )

    assert result.status == "skipped"
    assert "manual_requested" in result.actions
    assert "no_source_sessions" in result.actions
    assert model.calls == []


def test_execute_compact_rejects_invalid_source_before_model_call():
    rt = FakeRuntimeStore()
    store = CompactStateStore(runtime_store=rt)
    model = FakeModelClient()

    with pytest.raises(ValueError, match="source"):
        execute_compact(
            sessions=make_sessions(2),
            state_store=store,
            model_client=model,
            planner_config=trigger_config(),
            source="invalid",
            now=FIXED_NOW,
        )

    assert model.calls == []
    assert COMPACT_LOCK_KEY not in rt.data


def test_compact_execution_result_to_dict_is_json_serializable():
    rt = FakeRuntimeStore()
    store = CompactStateStore(runtime_store=rt)
    model = FakeModelClient()
    result = execute_compact(
        sessions=make_sessions(2),
        state_store=store,
        model_client=model,
        planner_config=trigger_config(),
        now=FIXED_NOW,
    )

    payload = compact_execution_result_to_dict(result)
    serialized = json.dumps(payload, ensure_ascii=False)
    restored = json.loads(serialized)

    assert restored["status"] == "ok"
    assert restored["summary_state"]["covered_session_ids"] == ["s1"]
    assert restored["metrics"]["last_status"] == "ok"
    assert restored["prompt"]["source_session_ids"] == ["s1"]