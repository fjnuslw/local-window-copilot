from __future__ import annotations

import copy
import json
from datetime import UTC, datetime, timedelta

import pytest

from app.services.context_budget import ContextTokenEstimator
from app.services.context_summary import (
    COMPACT_LOCK_KEY,
    COMPACT_LOCK_OWNER,
    COMPACT_METRICS_KEY,
    COMPACT_STATE_VERSION,
    DEFAULT_COMPACT_LOCK_TTL_SECONDS,
    ROLLING_SUMMARY_KEY,
    CompactEstimate,
    CompactLock,
    CompactMetrics,
    CompactStateStore,
    RollingSummaryState,
    build_rolling_summary_state,
    compact_estimate_from_payload,
    compact_estimate_to_payload,
    compact_lock_from_payload,
    compact_lock_to_payload,
    compact_metrics_from_payload,
    compact_metrics_to_payload,
    empty_compact_metrics,
    empty_rolling_summary_state,
    is_compact_lock_expired,
    normalize_session_ids,
    normalize_utc_iso,
    parse_utc_iso,
    rolling_summary_from_payload,
    rolling_summary_to_payload,
    utc_now_iso,
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


# ---------------------------------------------------------------------------
# 1. 常量 key 与 spec 一致
# ---------------------------------------------------------------------------


def test_constants_match_spec():
    assert ROLLING_SUMMARY_KEY == "assistant:chat:rolling_summary:v1"
    assert COMPACT_LOCK_KEY == "assistant:chat:compact_lock:v1"
    assert COMPACT_METRICS_KEY == "assistant:chat:compact_metrics:v1"
    assert COMPACT_STATE_VERSION == 1
    assert COMPACT_LOCK_OWNER == "assistant-chat"
    assert DEFAULT_COMPACT_LOCK_TTL_SECONDS == 90


# ---------------------------------------------------------------------------
# 2. empty_rolling_summary_state
# ---------------------------------------------------------------------------


def test_empty_rolling_summary_state():
    state = empty_rolling_summary_state()
    assert state.version == 1
    assert state.summary == ""
    assert state.covered_session_ids == ()
    assert state.updated_at is None
    assert state.source_session_count == 0
    assert state.estimate == CompactEstimate(source="rough", tokens=0, chars=0)
    assert state.last_error is None


# ---------------------------------------------------------------------------
# 3. normalize_session_ids
# ---------------------------------------------------------------------------


def test_normalize_session_ids_dedup_preserve_order():
    assert normalize_session_ids(
        ["  s1 ", "s2", "", "s1", "  ", "s3", None, 42, "s2"]
    ) == ("s1", "s2", "s3")


# ---------------------------------------------------------------------------
# 4. build_rolling_summary_state 计算 estimate
# ---------------------------------------------------------------------------


def test_build_rolling_summary_state_computes_estimate():
    summary = "上下文管理改造"  # 7 个 CJK 字符 -> 7 tokens
    state = build_rolling_summary_state(
        summary=summary,
        covered_session_ids=["s1", "s1", "s2"],
        source_session_count=2,
    )
    expected = ContextTokenEstimator().estimate_text(summary)
    assert state.estimate.tokens == expected.tokens
    assert state.estimate.chars == expected.chars
    assert state.estimate.source == "rough"
    assert state.covered_session_ids == ("s1", "s2")
    assert state.source_session_count == 2
    assert state.version == COMPACT_STATE_VERSION
    assert state.updated_at is not None


def test_build_rolling_summary_state_rejects_invalid_inputs():
    with pytest.raises(ValueError):
        build_rolling_summary_state(
            summary=123,  # type: ignore[arg-type]
            covered_session_ids=[],
            source_session_count=0,
        )
    with pytest.raises(ValueError):
        build_rolling_summary_state(
            summary="x",
            covered_session_ids=["s1"],
            source_session_count=0,  # < len(covered)
        )


def test_build_rolling_summary_state_uses_provided_updated_at():
    state = build_rolling_summary_state(
        summary="x",
        covered_session_ids=[],
        source_session_count=0,
        updated_at="2026-01-01T00:00:00+00:00",
    )
    assert state.updated_at == "2026-01-01T00:00:00+00:00"


def test_build_rolling_summary_state_rejects_non_serializable_last_error():
    class NotSerializable:
        pass

    with pytest.raises(ValueError):
        build_rolling_summary_state(
            summary="x",
            covered_session_ids=[],
            source_session_count=0,
            last_error={"obj": NotSerializable()},  # type: ignore[dict-item]
        )


# ---------------------------------------------------------------------------
# 5/6. payload roundtrip
# ---------------------------------------------------------------------------


def test_rolling_summary_payload_is_json_serializable():
    state = build_rolling_summary_state(
        summary="测试摘要",
        covered_session_ids=["s1", "s2"],
        source_session_count=3,
        last_error={"type": "timeout"},
    )
    payload = rolling_summary_to_payload(state)
    serialized = json.dumps(payload, ensure_ascii=False)
    assert isinstance(serialized, str)


def test_rolling_summary_roundtrip_preserves_fields():
    state = build_rolling_summary_state(
        summary="测试摘要内容",
        covered_session_ids=["s1", "s2", "s3"],
        source_session_count=5,
        updated_at="2026-07-06T12:00:00+00:00",
        last_error={"type": "value_error", "msg": "bad payload"},
    )
    payload = rolling_summary_to_payload(state)
    restored = rolling_summary_from_payload(payload)
    assert restored == state


# ---------------------------------------------------------------------------
# 7. malformed rolling summary payload
# ---------------------------------------------------------------------------


def test_rolling_summary_from_payload_rejects_malformed():
    with pytest.raises(ValueError):
        rolling_summary_from_payload("not-a-dict")
    with pytest.raises(ValueError):
        rolling_summary_from_payload({"version": 999, "summary": ""})
    with pytest.raises(ValueError):
        rolling_summary_from_payload(
            {"version": 1, "summary": "x", "covered_session_ids": [],
             "source_session_count": 0, "updated_at": None}  # 缺 estimate
        )
    with pytest.raises(ValueError):
        rolling_summary_from_payload(
            {"version": 1, "summary": "x", "covered_session_ids": [],
             "source_session_count": 0, "updated_at": None,
             "estimate": {"source": "rough"}}  # estimate 缺 tokens/chars
        )


# ---------------------------------------------------------------------------
# 8/9/10. CompactStateStore summary
# ---------------------------------------------------------------------------


def test_load_summary_returns_empty_when_missing():
    store = CompactStateStore(runtime_store=FakeRuntimeStore())
    assert store.load_summary() == empty_rolling_summary_state()


def test_save_summary_writes_key():
    rt = FakeRuntimeStore()
    store = CompactStateStore(runtime_store=rt)
    state = build_rolling_summary_state(
        summary="hello",
        covered_session_ids=["s1"],
        source_session_count=1,
    )
    store.save_summary(state)
    assert ROLLING_SUMMARY_KEY in rt.data
    assert rt.data[ROLLING_SUMMARY_KEY] == rolling_summary_to_payload(state)


def test_build_and_save_summary_returns_state_and_persists():
    rt = FakeRuntimeStore()
    store = CompactStateStore(runtime_store=rt)
    state = store.build_and_save_summary(
        summary="hello world",
        covered_session_ids=["s1", "s2"],
        source_session_count=2,
    )
    assert rt.data[ROLLING_SUMMARY_KEY] == rolling_summary_to_payload(state)
    assert store.load_summary() == state


def test_clear_summary_deletes_key():
    rt = FakeRuntimeStore()
    store = CompactStateStore(runtime_store=rt)
    store.build_and_save_summary(
        summary="x", covered_session_ids=[], source_session_count=0
    )
    store.clear_summary()
    assert ROLLING_SUMMARY_KEY in rt.deleted
    assert store.load_summary() == empty_rolling_summary_state()


# ---------------------------------------------------------------------------
# 11-15. lock 获取/释放/过期
# ---------------------------------------------------------------------------


def test_acquire_lock_writes_and_returns_lock():
    rt = FakeRuntimeStore()
    store = CompactStateStore(runtime_store=rt)
    lock = store.acquire_lock(source="auto", now=FIXED_NOW)
    assert lock is not None
    assert lock.owner == COMPACT_LOCK_OWNER
    assert lock.source == "auto"
    assert lock.started_at == FIXED_NOW.isoformat()
    expected_expires = (FIXED_NOW + timedelta(seconds=DEFAULT_COMPACT_LOCK_TTL_SECONDS)).isoformat()
    assert lock.expires_at == expected_expires
    assert COMPACT_LOCK_KEY in rt.data


def test_acquire_lock_returns_none_when_active():
    rt = FakeRuntimeStore()
    store = CompactStateStore(runtime_store=rt)
    first = store.acquire_lock(source="auto", now=FIXED_NOW)
    assert first is not None
    second = store.acquire_lock(source="manual", now=FIXED_NOW)
    assert second is None
    # 原 lock 保留
    assert rt.data[COMPACT_LOCK_KEY]["source"] == "auto"


def test_acquire_lock_overwrites_when_expired():
    rt = FakeRuntimeStore()
    store = CompactStateStore(runtime_store=rt)
    expired_now = FIXED_NOW - timedelta(seconds=200)
    store.acquire_lock(source="auto", now=expired_now)
    # 现在 lock 已过期
    new_lock = store.acquire_lock(source="manual", now=FIXED_NOW)
    assert new_lock is not None
    assert new_lock.source == "manual"
    assert rt.data[COMPACT_LOCK_KEY]["source"] == "manual"


def test_release_lock_deletes_matching_owner():
    rt = FakeRuntimeStore()
    store = CompactStateStore(runtime_store=rt)
    store.acquire_lock(source="auto", now=FIXED_NOW)
    result = store.release_lock(owner=COMPACT_LOCK_OWNER)
    assert result is True
    assert COMPACT_LOCK_KEY in rt.deleted
    assert store.load_lock(now=FIXED_NOW) is None


def test_release_lock_preserves_non_matching_owner():
    rt = FakeRuntimeStore()
    store = CompactStateStore(runtime_store=rt)
    store.acquire_lock(source="auto", now=FIXED_NOW)
    result = store.release_lock(owner="other")
    assert result is False
    assert COMPACT_LOCK_KEY not in rt.deleted
    assert store.load_lock(now=FIXED_NOW) is not None


def test_acquire_lock_rejects_invalid_inputs():
    rt = FakeRuntimeStore()
    store = CompactStateStore(runtime_store=rt)
    with pytest.raises(ValueError):
        store.acquire_lock(source="invalid", now=FIXED_NOW)
    with pytest.raises(ValueError):
        store.acquire_lock(source="auto", ttl_seconds=0, now=FIXED_NOW)
    with pytest.raises(ValueError):
        store.acquire_lock(source="auto", ttl_seconds=-5, now=FIXED_NOW)


def test_load_lock_returns_none_when_expired_and_deletes():
    rt = FakeRuntimeStore()
    store = CompactStateStore(runtime_store=rt)
    expired_now = FIXED_NOW - timedelta(seconds=200)
    store.acquire_lock(source="auto", now=expired_now)
    # 当前时间已超过 expires_at
    assert store.load_lock(now=FIXED_NOW) is None
    assert COMPACT_LOCK_KEY in rt.deleted


# ---------------------------------------------------------------------------
# 16. malformed lock payload
# ---------------------------------------------------------------------------


def test_compact_lock_from_payload_rejects_malformed():
    with pytest.raises(ValueError):
        compact_lock_from_payload("not-a-dict")
    with pytest.raises(ValueError):
        compact_lock_from_payload(
            {"owner": "x", "started_at": "2026-07-06T12:00:00+00:00",
             "expires_at": "2026-07-06T12:01:00+00:00", "source": "bogus"}
        )
    with pytest.raises(ValueError):
        compact_lock_from_payload(
            {"owner": "x", "started_at": "not-a-time",
             "expires_at": "2026-07-06T12:01:00+00:00", "source": "auto"}
        )
    with pytest.raises(ValueError):
        compact_lock_from_payload(
            {"owner": "", "started_at": "2026-07-06T12:00:00+00:00",
             "expires_at": "2026-07-06T12:01:00+00:00", "source": "auto"}
        )


def test_is_compact_lock_expired():
    lock = CompactLock(
        owner=COMPACT_LOCK_OWNER,
        started_at="2026-07-06T12:00:00+00:00",
        expires_at="2026-07-06T12:01:30+00:00",
        source="auto",
    )
    # expires_at == now 视为过期
    assert is_compact_lock_expired(lock, now=datetime(2026, 7, 6, 12, 1, 30, tzinfo=UTC)) is True
    assert is_compact_lock_expired(lock, now=datetime(2026, 7, 6, 12, 1, 0, tzinfo=UTC)) is False


def test_parse_utc_iso_handles_naive_and_invalid():
    assert parse_utc_iso("2026-07-06T12:00:00").tzinfo == UTC
    assert parse_utc_iso("2026-07-06T12:00:00+00:00").tzinfo == UTC
    with pytest.raises(ValueError):
        parse_utc_iso("not-a-time")
    with pytest.raises(ValueError):
        parse_utc_iso(123)  # type: ignore[arg-type]


def test_utc_now_iso_returns_iso_string():
    s = utc_now_iso()
    # 能被 fromisoformat 解析
    datetime.fromisoformat(s)


# ---------------------------------------------------------------------------
# 17-19. metrics
# ---------------------------------------------------------------------------


def test_empty_compact_metrics_is_idle():
    metrics = empty_compact_metrics()
    assert metrics.last_status == "idle"
    assert metrics.last_started_at is None
    assert metrics.last_finished_at is None
    assert metrics.source_session_count == 0
    assert metrics.covered_session_count == 0
    assert metrics.summary_tokens == 0
    assert metrics.source_tokens == 0
    assert metrics.error_type is None
    assert metrics.error_message is None


def test_save_success_metrics_load_returns_ok():
    rt = FakeRuntimeStore()
    store = CompactStateStore(runtime_store=rt)
    metrics = store.save_success_metrics(
        started_at="2026-07-06T12:00:00+00:00",
        finished_at="2026-07-06T12:00:30+00:00",
        source_session_count=5,
        covered_session_count=5,
        summary_tokens=1200,
        source_tokens=8000,
    )
    assert metrics.last_status == "ok"
    assert metrics.error_type is None
    loaded = store.load_metrics()
    assert loaded.last_status == "ok"
    assert loaded.covered_session_count == 5
    assert loaded.summary_tokens == 1200
    assert loaded.error_type is None


def test_save_error_metrics_load_returns_error_fields():
    rt = FakeRuntimeStore()
    store = CompactStateStore(runtime_store=rt)
    store.save_error_metrics(
        started_at="2026-07-06T12:00:00+00:00",
        finished_at="2026-07-06T12:00:30+00:00",
        source_session_count=3,
        covered_session_count=0,
        summary_tokens=0,
        source_tokens=2000,
        error_type="timeout",
        error_message="compact model request timed out",
    )
    loaded = store.load_metrics()
    assert loaded.last_status == "error"
    assert loaded.error_type == "timeout"
    assert loaded.error_message == "compact model request timed out"


def test_load_metrics_returns_empty_when_missing():
    store = CompactStateStore(runtime_store=FakeRuntimeStore())
    assert store.load_metrics() == empty_compact_metrics()


def test_clear_metrics_deletes_key():
    rt = FakeRuntimeStore()
    store = CompactStateStore(runtime_store=rt)
    store.save_success_metrics(
        started_at="2026-07-06T12:00:00+00:00",
        finished_at="2026-07-06T12:00:30+00:00",
        source_session_count=1,
        covered_session_count=1,
        summary_tokens=10,
        source_tokens=20,
    )
    store.clear_metrics()
    assert COMPACT_METRICS_KEY in rt.deleted
    assert store.load_metrics() == empty_compact_metrics()


# ---------------------------------------------------------------------------
# 20. malformed metrics payload
# ---------------------------------------------------------------------------


def test_compact_metrics_from_payload_rejects_malformed():
    with pytest.raises(ValueError):
        compact_metrics_from_payload("not-a-dict")
    with pytest.raises(ValueError):
        compact_metrics_from_payload({"last_status": "bogus"})
    with pytest.raises(ValueError):
        compact_metrics_from_payload(
            {"last_status": "ok", "source_session_count": -1,
             "covered_session_count": 0, "summary_tokens": 0, "source_tokens": 0}
        )
    with pytest.raises(ValueError):
        compact_metrics_from_payload(
            {"last_status": "ok", "summary_tokens": -5}
        )
    with pytest.raises(ValueError):
        compact_metrics_from_payload(
            {"last_status": "ok"}  # 缺字段
        )


# ---------------------------------------------------------------------------
# 21. converter 不修改输入
# ---------------------------------------------------------------------------


def test_estimate_to_from_payload_does_not_mutate_input():
    original = {"source": "rough", "tokens": 10, "chars": 40}
    snapshot = copy.deepcopy(original)
    est = compact_estimate_from_payload(original)
    compact_estimate_to_payload(est)
    assert original == snapshot


def test_rolling_summary_to_from_payload_does_not_mutate_input():
    state = build_rolling_summary_state(
        summary="hello",
        covered_session_ids=["s1", "s2"],
        source_session_count=2,
    )
    payload = rolling_summary_to_payload(state)
    payload_snapshot = copy.deepcopy(payload)
    rolling_summary_from_payload(payload)
    assert payload == payload_snapshot


def test_lock_to_from_payload_does_not_mutate_input():
    payload = {
        "owner": COMPACT_LOCK_OWNER,
        "started_at": "2026-07-06T12:00:00+00:00",
        "expires_at": "2026-07-06T12:01:30+00:00",
        "source": "auto",
    }
    snapshot = copy.deepcopy(payload)
    lock = compact_lock_from_payload(payload)
    compact_lock_to_payload(lock)
    assert payload == snapshot


def test_metrics_to_from_payload_does_not_mutate_input():
    payload = {
        "last_started_at": "2026-07-06T12:00:00+00:00",
        "last_finished_at": "2026-07-06T12:00:30+00:00",
        "last_status": "ok",
        "source_session_count": 1,
        "covered_session_count": 1,
        "summary_tokens": 10,
        "source_tokens": 20,
        "error_type": None,
        "error_message": None,
    }
    snapshot = copy.deepcopy(payload)
    metrics = compact_metrics_from_payload(payload)
    compact_metrics_to_payload(metrics)
    assert payload == snapshot


def test_load_summary_does_not_share_mutable_state_with_store():
    rt = FakeRuntimeStore()
    store = CompactStateStore(runtime_store=rt)
    store.build_and_save_summary(
        summary="hello",
        covered_session_ids=["s1"],
        source_session_count=1,
        last_error={"type": "x"},
    )
    loaded = store.load_summary()
    # 修改加载出来的对象，不应影响 RuntimeStore 中的 payload
    if loaded.last_error is not None:
        loaded.last_error["type"] = "mutated"
    reloaded = store.load_summary()
    assert reloaded.last_error == {"type": "x"}


# ===========================================================================
# 005-fix 补丁：契约加固
# ===========================================================================


# --- 修复 1: CompactEstimate.source 固定为 rough --------------------------


def test_compact_estimate_rejects_non_rough_source():
    with pytest.raises(ValueError, match="estimate.source"):
        compact_estimate_from_payload(
            {"source": "response_usage", "tokens": 1, "chars": 1}
        )


def test_compact_estimate_accepts_rough_source():
    est = compact_estimate_from_payload(
        {"source": "rough", "tokens": 5, "chars": 20}
    )
    assert est.source == "rough"
    assert est.tokens == 5
    assert est.chars == 20


# --- 修复 2: 写入 metrics 前完成校验 -------------------------------------


def test_save_success_metrics_rejects_negative_counts_without_write():
    rt = FakeRuntimeStore()
    store = CompactStateStore(runtime_store=rt)

    with pytest.raises(ValueError):
        store.save_success_metrics(
            started_at="2026-07-06T12:00:00+00:00",
            finished_at="2026-07-06T12:00:30+00:00",
            source_session_count=-1,
            covered_session_count=0,
            summary_tokens=0,
            source_tokens=0,
        )

    assert COMPACT_METRICS_KEY not in rt.data


def test_save_error_metrics_rejects_negative_tokens_without_write():
    rt = FakeRuntimeStore()
    store = CompactStateStore(runtime_store=rt)

    with pytest.raises(ValueError):
        store.save_error_metrics(
            started_at="2026-07-06T12:00:00+00:00",
            finished_at="2026-07-06T12:00:30+00:00",
            source_session_count=1,
            covered_session_count=1,
            summary_tokens=-5,
            source_tokens=20,
            error_type="timeout",
            error_message="timed out",
        )

    assert COMPACT_METRICS_KEY not in rt.data


def test_save_metrics_rejects_invalid_time_without_write():
    rt = FakeRuntimeStore()
    store = CompactStateStore(runtime_store=rt)

    with pytest.raises(ValueError, match="last_started_at"):
        store.save_success_metrics(
            started_at="not-a-time",
            finished_at="2026-07-06T12:00:30+00:00",
            source_session_count=1,
            covered_session_count=1,
            summary_tokens=10,
            source_tokens=20,
        )

    assert COMPACT_METRICS_KEY not in rt.data


def test_save_error_metrics_rejects_empty_error_fields_without_write():
    rt = FakeRuntimeStore()
    store = CompactStateStore(runtime_store=rt)

    with pytest.raises(ValueError, match="error_type"):
        store.save_error_metrics(
            started_at="2026-07-06T12:00:00+00:00",
            finished_at="2026-07-06T12:00:30+00:00",
            source_session_count=1,
            covered_session_count=1,
            summary_tokens=10,
            source_tokens=20,
            error_type="",
            error_message="msg",
        )

    assert COMPACT_METRICS_KEY not in rt.data


def test_save_error_metrics_rejects_whitespace_error_message_without_write():
    rt = FakeRuntimeStore()
    store = CompactStateStore(runtime_store=rt)

    with pytest.raises(ValueError, match="error_message"):
        store.save_error_metrics(
            started_at="2026-07-06T12:00:00+00:00",
            finished_at="2026-07-06T12:00:30+00:00",
            source_session_count=1,
            covered_session_count=1,
            summary_tokens=10,
            source_tokens=20,
            error_type="timeout",
            error_message="   ",
        )

    assert COMPACT_METRICS_KEY not in rt.data


def test_compact_metrics_to_payload_validates_before_return():
    # 构造一个非法 metrics（负数 token），to_payload 应抛出 ValueError。
    bad_metrics = CompactMetrics(
        last_started_at=None,
        last_finished_at=None,
        last_status="idle",
        source_session_count=0,
        covered_session_count=0,
        summary_tokens=-1,
        source_tokens=0,
        error_type=None,
        error_message=None,
    )
    with pytest.raises(ValueError, match="summary_tokens"):
        compact_metrics_to_payload(bad_metrics)


# --- 修复 3: summary 与 metrics 时间字段校验 ISO -------------------------


def test_build_rolling_summary_state_rejects_invalid_updated_at():
    with pytest.raises(ValueError, match="updated_at"):
        build_rolling_summary_state(
            summary="x",
            covered_session_ids=[],
            source_session_count=0,
            updated_at="bad-time",
        )


def test_rolling_summary_from_payload_rejects_invalid_updated_at():
    state = build_rolling_summary_state(
        summary="x",
        covered_session_ids=[],
        source_session_count=0,
    )
    payload = rolling_summary_to_payload(state)
    payload["updated_at"] = "not-a-time"
    with pytest.raises(ValueError, match="updated_at"):
        rolling_summary_from_payload(payload)


def test_metrics_from_payload_rejects_invalid_time():
    payload = {
        "last_started_at": "bad-time",
        "last_finished_at": "2026-07-06T12:00:30+00:00",
        "last_status": "ok",
        "source_session_count": 1,
        "covered_session_count": 1,
        "summary_tokens": 10,
        "source_tokens": 20,
        "error_type": None,
        "error_message": None,
    }
    with pytest.raises(ValueError, match="last_started_at"):
        compact_metrics_from_payload(payload)


def test_metrics_from_payload_rejects_invalid_finished_at():
    payload = {
        "last_started_at": "2026-07-06T12:00:00+00:00",
        "last_finished_at": "bad-time",
        "last_status": "ok",
        "source_session_count": 1,
        "covered_session_count": 1,
        "summary_tokens": 10,
        "source_tokens": 20,
        "error_type": None,
        "error_message": None,
    }
    with pytest.raises(ValueError, match="last_finished_at"):
        compact_metrics_from_payload(payload)


# --- 修复 4: last_error 深拷贝 --------------------------------------------


def test_rolling_summary_last_error_deep_copied():
    nested_error = {"outer": {"inner": "x"}}
    state = build_rolling_summary_state(
        summary="x",
        covered_session_ids=[],
        source_session_count=0,
        last_error=nested_error,
    )

    payload = rolling_summary_to_payload(state)
    payload["last_error"]["outer"]["inner"] = "mutated"

    assert state.last_error == {"outer": {"inner": "x"}}


def test_rolling_summary_build_does_not_share_last_error_with_caller():
    nested_error = {"outer": {"inner": "x"}}
    state = build_rolling_summary_state(
        summary="x",
        covered_session_ids=[],
        source_session_count=0,
        last_error=nested_error,
    )
    # 修改调用方传入的 dict，不应影响已构建的 state。
    nested_error["outer"]["inner"] = "mutated"
    assert state.last_error == {"outer": {"inner": "x"}}


def test_rolling_summary_from_payload_deep_copies_last_error():
    state = build_rolling_summary_state(
        summary="x",
        covered_session_ids=[],
        source_session_count=0,
        last_error={"outer": {"inner": "x"}},
    )
    payload = rolling_summary_to_payload(state)

    restored = rolling_summary_from_payload(payload)
    # 修改 payload 中的 last_error，不应影响已解析出的 state。
    payload["last_error"]["outer"]["inner"] = "mutated"
    assert restored.last_error == {"outer": {"inner": "x"}}


def test_rolling_summary_to_payload_does_not_share_last_error_with_state():
    state = build_rolling_summary_state(
        summary="x",
        covered_session_ids=[],
        source_session_count=0,
        last_error={"outer": {"inner": "x"}},
    )
    payload = rolling_summary_to_payload(state)
    # 修改 payload 中的 last_error，不应影响 state。
    payload["last_error"]["outer"]["inner"] = "mutated"
    assert state.last_error == {"outer": {"inner": "x"}}


# --- 修复 2 补充：existing error metrics 走 from_payload 仍校验非空 -------


def test_compact_metrics_from_payload_rejects_error_status_with_null_error_type():
    payload = {
        "last_started_at": "2026-07-06T12:00:00+00:00",
        "last_finished_at": "2026-07-06T12:00:30+00:00",
        "last_status": "error",
        "source_session_count": 1,
        "covered_session_count": 1,
        "summary_tokens": 10,
        "source_tokens": 20,
        "error_type": None,
        "error_message": "msg",
    }
    with pytest.raises(ValueError, match="error_type"):
        compact_metrics_from_payload(payload)


def test_compact_metrics_from_payload_rejects_error_status_with_null_error_message():
    payload = {
        "last_started_at": "2026-07-06T12:00:00+00:00",
        "last_finished_at": "2026-07-06T12:00:30+00:00",
        "last_status": "error",
        "source_session_count": 1,
        "covered_session_count": 1,
        "summary_tokens": 10,
        "source_tokens": 20,
        "error_type": "timeout",
        "error_message": None,
    }
    with pytest.raises(ValueError, match="error_message"):
        compact_metrics_from_payload(payload)


def test_compact_metrics_from_payload_accepts_ok_status_with_null_error_fields():
    payload = {
        "last_started_at": "2026-07-06T12:00:00+00:00",
        "last_finished_at": "2026-07-06T12:00:30+00:00",
        "last_status": "ok",
        "source_session_count": 1,
        "covered_session_count": 1,
        "summary_tokens": 10,
        "source_tokens": 20,
        "error_type": None,
        "error_message": None,
    }
    metrics = compact_metrics_from_payload(payload)
    assert metrics.last_status == "ok"
    assert metrics.error_type is None
    assert metrics.error_message is None



def test_normalize_utc_iso_handles_naive_and_offsets():
    assert normalize_utc_iso(
        "2026-07-06T12:00:00", "test.time"
    ) == "2026-07-06T12:00:00+00:00"
    assert normalize_utc_iso(
        "2026-07-06T20:00:00+08:00", "test.time"
    ) == "2026-07-06T12:00:00+00:00"
    with pytest.raises(ValueError, match="test.time"):
        normalize_utc_iso("bad-time", "test.time")


def test_build_rolling_summary_state_normalizes_updated_at_to_utc():
    state = build_rolling_summary_state(
        summary="x",
        covered_session_ids=[],
        source_session_count=0,
        updated_at="2026-07-06T20:00:00+08:00",
    )

    assert state.updated_at == "2026-07-06T12:00:00+00:00"


def test_rolling_summary_from_payload_normalizes_updated_at_to_utc():
    payload = {
        "version": COMPACT_STATE_VERSION,
        "summary": "x",
        "covered_session_ids": [],
        "updated_at": "2026-07-06T20:00:00+08:00",
        "source_session_count": 0,
        "estimate": {"source": "rough", "tokens": 1, "chars": 1},
        "last_error": None,
    }

    state = rolling_summary_from_payload(payload)
    assert state.updated_at == "2026-07-06T12:00:00+00:00"


def test_rolling_summary_to_payload_validates_before_return():
    state = RollingSummaryState(
        version=COMPACT_STATE_VERSION,
        summary="x",
        covered_session_ids=(),
        updated_at="bad-time",
        source_session_count=0,
        estimate=CompactEstimate(source="rough", tokens=1, chars=1),
        last_error=None,
    )

    with pytest.raises(ValueError, match="updated_at"):
        rolling_summary_to_payload(state)


def test_save_summary_rejects_invalid_state_without_write():
    rt = FakeRuntimeStore()
    store = CompactStateStore(runtime_store=rt)
    state = RollingSummaryState(
        version=COMPACT_STATE_VERSION,
        summary="x",
        covered_session_ids=(),
        updated_at="2026-07-06T12:00:00+00:00",
        source_session_count=-1,
        estimate=CompactEstimate(source="rough", tokens=1, chars=1),
        last_error=None,
    )

    with pytest.raises(ValueError, match="source_session_count"):
        store.save_summary(state)

    assert ROLLING_SUMMARY_KEY not in rt.data


def test_compact_metrics_to_payload_normalizes_time_to_utc():
    metrics = CompactMetrics(
        last_started_at="2026-07-06T20:00:00+08:00",
        last_finished_at="2026-07-06T20:00:30+08:00",
        last_status="ok",
        source_session_count=1,
        covered_session_count=1,
        summary_tokens=10,
        source_tokens=20,
        error_type=None,
        error_message=None,
    )

    payload = compact_metrics_to_payload(metrics)
    assert payload["last_started_at"] == "2026-07-06T12:00:00+00:00"
    assert payload["last_finished_at"] == "2026-07-06T12:00:30+00:00"
