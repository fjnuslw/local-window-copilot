from __future__ import annotations

import copy
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from app.services.context_budget import ContextTokenEstimator
from app.services.runtime_store import RuntimeStore


# ---------------------------------------------------------------------------
# 常量（与总 spec §A/§G 一致）
# ---------------------------------------------------------------------------

ROLLING_SUMMARY_KEY = "assistant:chat:rolling_summary:v1"
COMPACT_LOCK_KEY = "assistant:chat:compact_lock:v1"
COMPACT_METRICS_KEY = "assistant:chat:compact_metrics:v1"
COMPACT_STATE_VERSION = 1
COMPACT_LOCK_OWNER = "assistant-chat"
DEFAULT_COMPACT_LOCK_TTL_SECONDS = 90

_COMPACT_LOCK_SOURCES = frozenset({"auto", "manual"})
_COMPACT_METRICS_STATUSES = frozenset({"idle", "ok", "error"})


# ---------------------------------------------------------------------------
# 时间 helper
# ---------------------------------------------------------------------------


def utc_now_iso() -> str:
    """返回当前 UTC 时间的 ISO-8601 字符串。"""
    return datetime.now(UTC).isoformat()


def parse_utc_iso(value: str) -> datetime:
    """解析 ISO-8601 字符串为 timezone-aware datetime。

    无 timezone 的字符串按 UTC 处理。非字符串或无法解析时抛出 ValueError。
    """
    if not isinstance(value, str):
        raise ValueError("expected ISO-8601 string")
    try:
        dt = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"invalid ISO-8601 string: {value!r}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def normalize_utc_iso(value: str, field_name: str) -> str:
    try:
        return parse_utc_iso(value).astimezone(UTC).isoformat()
    except ValueError as exc:
        raise ValueError(f"{field_name} invalid: {exc}") from exc


# ---------------------------------------------------------------------------
# covered_session_ids 规范化
# ---------------------------------------------------------------------------


def normalize_session_ids(values: Iterable[Any]) -> tuple[str, ...]:
    """保留非空 str，strip 后去重，保持首次出现顺序。"""
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        cleaned = value.strip()
        if not cleaned:
            continue
        if cleaned in seen:
            continue
        seen.add(cleaned)
        result.append(cleaned)
    return tuple(result)


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CompactEstimate:
    source: str
    tokens: int
    chars: int


@dataclass(frozen=True)
class RollingSummaryState:
    version: int
    summary: str
    covered_session_ids: tuple[str, ...]
    updated_at: str | None
    source_session_count: int
    estimate: CompactEstimate
    last_error: dict[str, Any] | None


@dataclass(frozen=True)
class CompactLock:
    owner: str
    started_at: str
    expires_at: str
    source: str


@dataclass(frozen=True)
class CompactMetrics:
    last_started_at: str | None
    last_finished_at: str | None
    last_status: str
    source_session_count: int
    covered_session_count: int
    summary_tokens: int
    source_tokens: int
    error_type: str | None
    error_message: str | None


# ---------------------------------------------------------------------------
# estimate converters
# ---------------------------------------------------------------------------


def compact_estimate_to_payload(estimate: CompactEstimate) -> dict[str, int | str]:
    return {
        "source": estimate.source,
        "tokens": estimate.tokens,
        "chars": estimate.chars,
    }


def compact_estimate_from_payload(payload: Any) -> CompactEstimate:
    if not isinstance(payload, dict):
        raise ValueError("estimate payload must be dict")
    source = payload.get("source")
    tokens = payload.get("tokens")
    chars = payload.get("chars")
    if not isinstance(source, str):
        raise ValueError("estimate.source must be str")
    if source != "rough":
        raise ValueError("estimate.source must be 'rough'")
    if not isinstance(tokens, int) or isinstance(tokens, bool):
        raise ValueError("estimate.tokens must be int")
    if not isinstance(chars, int) or isinstance(chars, bool):
        raise ValueError("estimate.chars must be int")
    if tokens < 0:
        raise ValueError("estimate.tokens must be >= 0")
    if chars < 0:
        raise ValueError("estimate.chars must be >= 0")
    return CompactEstimate(source=source, tokens=tokens, chars=chars)


# ---------------------------------------------------------------------------
# rolling summary converters
# ---------------------------------------------------------------------------


def empty_rolling_summary_state() -> RollingSummaryState:
    return RollingSummaryState(
        version=COMPACT_STATE_VERSION,
        summary="",
        covered_session_ids=(),
        updated_at=None,
        source_session_count=0,
        estimate=CompactEstimate(source="rough", tokens=0, chars=0),
        last_error=None,
    )


def build_rolling_summary_state(
    *,
    summary: str,
    covered_session_ids: Iterable[Any],
    source_session_count: int,
    updated_at: str | None = None,
    last_error: dict[str, Any] | None = None,
    estimator: ContextTokenEstimator | None = None,
) -> RollingSummaryState:
    if not isinstance(summary, str):
        raise ValueError("summary must be str")
    if not isinstance(source_session_count, int) or isinstance(source_session_count, bool):
        raise ValueError("source_session_count must be int")
    if source_session_count < 0:
        raise ValueError("source_session_count must be >= 0")

    normalized_ids = normalize_session_ids(covered_session_ids)
    if source_session_count < len(normalized_ids):
        raise ValueError(
            "source_session_count must be >= len(covered_session_ids)"
        )

    updated_at_value = (
        normalize_utc_iso(updated_at, "rolling summary.updated_at")
        if updated_at is not None
        else utc_now_iso()
    )

    if last_error is not None:
        # last_error 必须可被 json.dumps 处理。
        try:
            json.dumps(last_error, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("last_error must be JSON-serializable") from exc
        # 深拷贝避免与调用方共享可变引用。
        last_error_payload: dict[str, Any] | None = copy.deepcopy(last_error)
    else:
        last_error_payload = None

    est = (estimator or ContextTokenEstimator()).estimate_text(summary)
    estimate = CompactEstimate(
        source=est.source, tokens=est.tokens, chars=est.chars
    )

    return RollingSummaryState(
        version=COMPACT_STATE_VERSION,
        summary=summary,
        covered_session_ids=normalized_ids,
        updated_at=updated_at_value,
        source_session_count=source_session_count,
        estimate=estimate,
        last_error=last_error_payload,
    )


def rolling_summary_to_payload(state: RollingSummaryState) -> dict[str, Any]:
    updated_at = (
        normalize_utc_iso(state.updated_at, "rolling summary.updated_at")
        if state.updated_at is not None
        else None
    )
    payload = {
        "version": state.version,
        "summary": state.summary,
        "covered_session_ids": list(state.covered_session_ids),
        "updated_at": updated_at,
        "source_session_count": state.source_session_count,
        "estimate": compact_estimate_to_payload(state.estimate),
        # 深拷贝避免与 state 共享可变引用。
        "last_error": copy.deepcopy(state.last_error) if state.last_error is not None else None,
    }
    rolling_summary_from_payload(payload)
    return payload


def rolling_summary_from_payload(payload: Any) -> RollingSummaryState:
    if not isinstance(payload, dict):
        raise ValueError("rolling summary payload must be dict")
    version = payload.get("version")
    if version != COMPACT_STATE_VERSION:
        raise ValueError(
            f"rolling summary version must be {COMPACT_STATE_VERSION}"
        )
    summary = payload.get("summary")
    if not isinstance(summary, str):
        raise ValueError("rolling summary.summary must be str")
    raw_ids = payload.get("covered_session_ids")
    if not isinstance(raw_ids, list):
        raise ValueError("rolling summary.covered_session_ids must be list")
    covered = normalize_session_ids(raw_ids)

    source_session_count = payload.get("source_session_count")
    if (
        not isinstance(source_session_count, int)
        or isinstance(source_session_count, bool)
    ):
        raise ValueError("rolling summary.source_session_count must be int")
    if source_session_count < 0:
        raise ValueError("rolling summary.source_session_count must be >= 0")
    if source_session_count < len(covered):
        raise ValueError(
            "rolling summary.source_session_count must be >= len(covered_session_ids)"
        )

    updated_at = payload.get("updated_at")
    if updated_at is not None:
        if not isinstance(updated_at, str):
            raise ValueError("rolling summary.updated_at must be str or null")
        updated_at = normalize_utc_iso(updated_at, "rolling summary.updated_at")

    estimate = compact_estimate_from_payload(payload.get("estimate"))

    last_error = payload.get("last_error")
    if last_error is not None and not isinstance(last_error, dict):
        raise ValueError("rolling summary.last_error must be dict or null")
    # 深拷贝避免与 payload 共享可变引用。
    last_error_payload = copy.deepcopy(last_error) if isinstance(last_error, dict) else None

    return RollingSummaryState(
        version=version,
        summary=summary,
        covered_session_ids=covered,
        updated_at=updated_at,
        source_session_count=source_session_count,
        estimate=estimate,
        last_error=last_error_payload,
    )


# ---------------------------------------------------------------------------
# lock converters
# ---------------------------------------------------------------------------


def compact_lock_to_payload(lock: CompactLock) -> dict[str, str]:
    return {
        "owner": lock.owner,
        "started_at": lock.started_at,
        "expires_at": lock.expires_at,
        "source": lock.source,
    }


def compact_lock_from_payload(payload: Any) -> CompactLock:
    if not isinstance(payload, dict):
        raise ValueError("compact lock payload must be dict")
    owner = payload.get("owner")
    started_at = payload.get("started_at")
    expires_at = payload.get("expires_at")
    source = payload.get("source")

    if not isinstance(owner, str) or not owner.strip():
        raise ValueError("compact lock.owner must be non-empty str")
    if not isinstance(started_at, str):
        raise ValueError("compact lock.started_at must be str")
    if not isinstance(expires_at, str):
        raise ValueError("compact lock.expires_at must be str")
    if source not in _COMPACT_LOCK_SOURCES:
        raise ValueError(
            f"compact lock.source must be one of {sorted(_COMPACT_LOCK_SOURCES)}"
        )
    # 时间字符串必须可被 fromisoformat 解析。
    parse_utc_iso(started_at)
    parse_utc_iso(expires_at)

    return CompactLock(
        owner=owner,
        started_at=started_at,
        expires_at=expires_at,
        source=source,
    )


def is_compact_lock_expired(
    lock: CompactLock, *, now: datetime | None = None
) -> bool:
    current = now if now is not None else datetime.now(UTC)
    expires_at = parse_utc_iso(lock.expires_at)
    return expires_at <= current


# ---------------------------------------------------------------------------
# metrics converters
# ---------------------------------------------------------------------------


def empty_compact_metrics() -> CompactMetrics:
    return CompactMetrics(
        last_started_at=None,
        last_finished_at=None,
        last_status="idle",
        source_session_count=0,
        covered_session_count=0,
        summary_tokens=0,
        source_tokens=0,
        error_type=None,
        error_message=None,
    )


def compact_metrics_to_payload(metrics: CompactMetrics) -> dict[str, Any]:
    payload = {
        "last_started_at": metrics.last_started_at,
        "last_finished_at": metrics.last_finished_at,
        "last_status": metrics.last_status,
        "source_session_count": metrics.source_session_count,
        "covered_session_count": metrics.covered_session_count,
        "summary_tokens": metrics.summary_tokens,
        "source_tokens": metrics.source_tokens,
        "error_type": metrics.error_type,
        "error_message": metrics.error_message,
    }
    # 写出前做一次完整校验，确保 payload 结构合法。
    validated = compact_metrics_from_payload(payload)
    return {
        "last_started_at": validated.last_started_at,
        "last_finished_at": validated.last_finished_at,
        "last_status": validated.last_status,
        "source_session_count": validated.source_session_count,
        "covered_session_count": validated.covered_session_count,
        "summary_tokens": validated.summary_tokens,
        "source_tokens": validated.source_tokens,
        "error_type": validated.error_type,
        "error_message": validated.error_message,
    }


def _require_non_negative_int(value: Any, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{field_name} must be int")
    if value < 0:
        raise ValueError(f"{field_name} must be >= 0")
    return value


def compact_metrics_from_payload(payload: Any) -> CompactMetrics:
    if not isinstance(payload, dict):
        raise ValueError("compact metrics payload must be dict")

    last_status = payload.get("last_status")
    if last_status not in _COMPACT_METRICS_STATUSES:
        raise ValueError(
            f"compact metrics.last_status must be one of {sorted(_COMPACT_METRICS_STATUSES)}"
        )

    last_started_at = payload.get("last_started_at")
    if last_started_at is not None:
        if not isinstance(last_started_at, str):
            raise ValueError("compact metrics.last_started_at must be str or null")
        last_started_at = normalize_utc_iso(
            last_started_at, "compact metrics.last_started_at"
        )
    last_finished_at = payload.get("last_finished_at")
    if last_finished_at is not None:
        if not isinstance(last_finished_at, str):
            raise ValueError("compact metrics.last_finished_at must be str or null")
        last_finished_at = normalize_utc_iso(
            last_finished_at, "compact metrics.last_finished_at"
        )

    source_session_count = _require_non_negative_int(
        payload.get("source_session_count"),
        "compact metrics.source_session_count",
    )
    covered_session_count = _require_non_negative_int(
        payload.get("covered_session_count"),
        "compact metrics.covered_session_count",
    )
    summary_tokens = _require_non_negative_int(
        payload.get("summary_tokens"), "compact metrics.summary_tokens"
    )
    source_tokens = _require_non_negative_int(
        payload.get("source_tokens"), "compact metrics.source_tokens"
    )

    error_type = payload.get("error_type")
    if error_type is not None and not isinstance(error_type, str):
        raise ValueError("compact metrics.error_type must be str or null")
    error_message = payload.get("error_message")
    if error_message is not None and not isinstance(error_message, str):
        raise ValueError("compact metrics.error_message must be str or null")
    # error 状态下 error_type/error_message 必须为非空字符串。
    if last_status == "error":
        if not isinstance(error_type, str) or not error_type.strip():
            raise ValueError(
                "compact metrics.error_type must be non-empty str when last_status is 'error'"
            )
        if not isinstance(error_message, str) or not error_message.strip():
            raise ValueError(
                "compact metrics.error_message must be non-empty str when last_status is 'error'"
            )

    return CompactMetrics(
        last_started_at=last_started_at,
        last_finished_at=last_finished_at,
        last_status=last_status,
        source_session_count=source_session_count,
        covered_session_count=covered_session_count,
        summary_tokens=summary_tokens,
        source_tokens=source_tokens,
        error_type=error_type,
        error_message=error_message,
    )


# ---------------------------------------------------------------------------
# CompactStateStore
# ---------------------------------------------------------------------------


class CompactStateStore:
    """包装 RuntimeStore 读写 compact 状态。

    所有写入走 converter，所有读取走 parser。缺失 key 返回初始对象或 None。
    payload 结构错误时抛出 ValueError。
    """

    def __init__(
        self,
        *,
        runtime_store: RuntimeStore,
        estimator: ContextTokenEstimator | None = None,
    ) -> None:
        self.runtime_store = runtime_store
        self.estimator = estimator or ContextTokenEstimator()

    # --- summary -------------------------------------------------------

    def load_summary(self) -> RollingSummaryState:
        payload = self.runtime_store.get_json(ROLLING_SUMMARY_KEY)
        if payload is None:
            return empty_rolling_summary_state()
        return rolling_summary_from_payload(payload)

    def save_summary(self, state: RollingSummaryState) -> None:
        payload = rolling_summary_to_payload(state)
        self.runtime_store.set_json(ROLLING_SUMMARY_KEY, payload)

    def build_and_save_summary(
        self,
        *,
        summary: str,
        covered_session_ids: Iterable[Any],
        source_session_count: int,
        updated_at: str | None = None,
        last_error: dict[str, Any] | None = None,
    ) -> RollingSummaryState:
        state = build_rolling_summary_state(
            summary=summary,
            covered_session_ids=covered_session_ids,
            source_session_count=source_session_count,
            updated_at=updated_at,
            last_error=last_error,
            estimator=self.estimator,
        )
        self.save_summary(state)
        return state

    def clear_summary(self) -> None:
        self.runtime_store.delete(ROLLING_SUMMARY_KEY)

    # --- lock ----------------------------------------------------------

    def load_lock(self, *, now: datetime | None = None) -> CompactLock | None:
        payload = self.runtime_store.get_json(COMPACT_LOCK_KEY)
        if payload is None:
            return None
        lock = compact_lock_from_payload(payload)
        if is_compact_lock_expired(lock, now=now):
            self.runtime_store.delete(COMPACT_LOCK_KEY)
            return None
        return lock

    def acquire_lock(
        self,
        *,
        source: str,
        ttl_seconds: int = DEFAULT_COMPACT_LOCK_TTL_SECONDS,
        now: datetime | None = None,
    ) -> CompactLock | None:
        if not isinstance(ttl_seconds, int) or isinstance(ttl_seconds, bool):
            raise ValueError("ttl_seconds must be int")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be > 0")
        if source not in _COMPACT_LOCK_SOURCES:
            raise ValueError(
                f"source must be one of {sorted(_COMPACT_LOCK_SOURCES)}"
            )

        current = now if now is not None else datetime.now(UTC)
        existing = self.load_lock(now=current)
        if existing is not None:
            return None

        started_at = current.isoformat()
        expires_at = (current + timedelta(seconds=ttl_seconds)).isoformat()
        lock = CompactLock(
            owner=COMPACT_LOCK_OWNER,
            started_at=started_at,
            expires_at=expires_at,
            source=source,
        )
        self.runtime_store.set_json(
            COMPACT_LOCK_KEY, compact_lock_to_payload(lock)
        )
        return lock

    def release_lock(self, *, owner: str = COMPACT_LOCK_OWNER) -> bool:
        payload = self.runtime_store.get_json(COMPACT_LOCK_KEY)
        if payload is None:
            return False
        lock = compact_lock_from_payload(payload)
        if lock.owner != owner:
            return False
        self.runtime_store.delete(COMPACT_LOCK_KEY)
        return True

    def clear_lock(self) -> None:
        self.runtime_store.delete(COMPACT_LOCK_KEY)

    # --- metrics -------------------------------------------------------

    def load_metrics(self) -> CompactMetrics:
        payload = self.runtime_store.get_json(COMPACT_METRICS_KEY)
        if payload is None:
            return empty_compact_metrics()
        return compact_metrics_from_payload(payload)

    def save_metrics(self, metrics: CompactMetrics) -> None:
        payload = compact_metrics_to_payload(metrics)
        self.runtime_store.set_json(COMPACT_METRICS_KEY, payload)

    def save_success_metrics(
        self,
        *,
        started_at: str,
        finished_at: str,
        source_session_count: int,
        covered_session_count: int,
        summary_tokens: int,
        source_tokens: int,
    ) -> CompactMetrics:
        metrics = CompactMetrics(
            last_started_at=started_at,
            last_finished_at=finished_at,
            last_status="ok",
            source_session_count=source_session_count,
            covered_session_count=covered_session_count,
            summary_tokens=summary_tokens,
            source_tokens=source_tokens,
            error_type=None,
            error_message=None,
        )
        self.save_metrics(metrics)
        return metrics

    def save_error_metrics(
        self,
        *,
        started_at: str,
        finished_at: str,
        source_session_count: int,
        covered_session_count: int,
        summary_tokens: int,
        source_tokens: int,
        error_type: str,
        error_message: str,
    ) -> CompactMetrics:
        metrics = CompactMetrics(
            last_started_at=started_at,
            last_finished_at=finished_at,
            last_status="error",
            source_session_count=source_session_count,
            covered_session_count=covered_session_count,
            summary_tokens=summary_tokens,
            source_tokens=source_tokens,
            error_type=error_type,
            error_message=error_message,
        )
        self.save_metrics(metrics)
        return metrics

    def clear_metrics(self) -> None:
        self.runtime_store.delete(COMPACT_METRICS_KEY)


# ---------------------------------------------------------------------------
# compact planner
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CompactHistorySession:
    """compact 专用 session 视图，避免 planner 直接依赖 Pydantic 模型。"""

    session_id: str
    created_at: str
    question: str
    answer: str


@dataclass(frozen=True)
class CompactPlannerConfig:
    """本地 planner 配置，暂不接 config.py。"""

    raw_tail_turns: int = 2
    batch_session_limit: int = 12
    source_budget_tokens: int = 18000
    uncovered_session_threshold: int = 6
    history_trigger_tokens: int = 24000


@dataclass(frozen=True)
class CompactPlan:
    should_compact: bool
    trigger: str
    source_sessions: tuple[CompactHistorySession, ...]
    tail_sessions: tuple[CompactHistorySession, ...]
    skipped_covered_session_ids: tuple[str, ...]
    skipped_budget_session_ids: tuple[str, ...]
    uncovered_session_ids: tuple[str, ...]
    estimated_source_tokens: int
    estimated_tail_tokens: int
    actions: tuple[str, ...]


def _require_int_field(value: Any, name: str, *, min_value: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{name} must be int")
    if value < min_value:
        raise ValueError(f"{name} must be >= {min_value}")


def validate_compact_planner_config(
    config: CompactPlannerConfig,
) -> CompactPlannerConfig:
    """校验 planner 配置，返回原 config；非法时抛 ValueError。"""
    _require_int_field(config.raw_tail_turns, "raw_tail_turns", min_value=0)
    _require_int_field(config.batch_session_limit, "batch_session_limit", min_value=1)
    _require_int_field(config.source_budget_tokens, "source_budget_tokens", min_value=1)
    _require_int_field(
        config.uncovered_session_threshold,
        "uncovered_session_threshold",
        min_value=1,
    )
    _require_int_field(config.history_trigger_tokens, "history_trigger_tokens", min_value=1)
    return config


def compact_history_session_from_value(value: Any) -> CompactHistorySession | None:
    """把 ChatSession 对象或 dict 转成 CompactHistorySession。

    session_id 缺失/非 str/strip 后为空返回 None。
    question/answer 非 str 抛 ValueError，错误消息包含字段名。
    不修改输入对象或 dict。
    """
    if isinstance(value, dict):
        session_id = value.get("session_id")
        created_at = value.get("created_at")
        question = value.get("question")
        answer = value.get("answer")
    else:
        session_id = getattr(value, "session_id", None)
        created_at = getattr(value, "created_at", None)
        question = getattr(value, "question", None)
        answer = getattr(value, "answer", None)

    if not isinstance(session_id, str):
        return None
    cleaned_id = session_id.strip()
    if not cleaned_id:
        return None

    if not isinstance(question, str):
        raise ValueError("question must be str")
    if not isinstance(answer, str):
        raise ValueError("answer must be str")

    if created_at is None:
        created_at_str = ""
    elif isinstance(created_at, datetime):
        created_at_str = created_at.isoformat()
    elif isinstance(created_at, str):
        created_at_str = created_at
    else:
        created_at_str = str(created_at)

    return CompactHistorySession(
        session_id=cleaned_id,
        created_at=created_at_str,
        question=question,
        answer=answer,
    )


def estimate_compact_session_tokens(
    session: CompactHistorySession,
    *,
    estimator: ContextTokenEstimator | None = None,
) -> int:
    """估算 session 的 rough tokens：question + answer 各一条 message。"""
    est = estimator or ContextTokenEstimator()
    q_tokens = est.estimate_message(
        {"role": "user", "content": session.question}
    ).tokens
    a_tokens = est.estimate_message(
        {"role": "assistant", "content": session.answer}
    ).tokens
    return q_tokens + a_tokens


def plan_compact(
    *,
    sessions: Iterable[Any],
    covered_session_ids: Iterable[str],
    config: CompactPlannerConfig | None = None,
    force: bool = False,
    estimator: ContextTokenEstimator | None = None,
) -> CompactPlan:
    """纯选择器：依据 sessions、covered ids、config、force 决定本轮 compact 计划。

    不调用模型、不写 RuntimeStore、不修改输入。
    """
    cfg = validate_compact_planner_config(
        config if config is not None else CompactPlannerConfig()
    )
    est = estimator or ContextTokenEstimator()

    # 1-3: 转换 sessions，丢弃无 session_id 的项，保持 newest-first。
    compact_sessions: list[CompactHistorySession] = []
    for value in sessions:
        converted = compact_history_session_from_value(value)
        if converted is not None:
            compact_sessions.append(converted)

    # 4-5: 切分 tail 与 older。
    tail_list = compact_sessions[: cfg.raw_tail_turns]
    older_list = compact_sessions[cfg.raw_tail_turns :]

    # 6-8: 规范化 covered ids，分离 older 中已覆盖与未覆盖。
    covered = normalize_session_ids(covered_session_ids)
    covered_set = set(covered)
    skipped_covered: list[str] = []
    uncovered_candidates: list[CompactHistorySession] = []
    for session in older_list:
        if session.session_id in covered_set:
            skipped_covered.append(session.session_id)
        else:
            uncovered_candidates.append(session)

    # 9: 全部未覆盖候选 id，newest-first。
    uncovered_session_ids = tuple(s.session_id for s in uncovered_candidates)

    # 10: 全部未覆盖候选 rough tokens。
    total_uncovered_tokens = sum(
        estimate_compact_session_tokens(s, estimator=est) for s in uncovered_candidates
    )
    estimated_tail_tokens = sum(
        estimate_compact_session_tokens(s, estimator=est) for s in tail_list
    )

    # 11-13: 触发判定。
    actions: list[str] = []
    should_compact = False
    trigger = "none"

    if force:
        if uncovered_candidates:
            should_compact = True
            trigger = "manual"
            actions.append("manual_requested")
        else:
            should_compact = False
            trigger = "none"
            actions.append("manual_requested")
            actions.append("no_source_sessions")
    else:
        session_reached = (
            len(uncovered_candidates) >= cfg.uncovered_session_threshold
        )
        token_reached = total_uncovered_tokens >= cfg.history_trigger_tokens
        if session_reached or token_reached:
            should_compact = True
            if session_reached:
                trigger = "session_threshold"
                actions.append("session_threshold_reached")
                if token_reached:
                    actions.append("token_threshold_reached")
            else:
                trigger = "token_threshold"
                actions.append("token_threshold_reached")

    # 14: source 选择（仅触发时）。
    source_sessions: list[CompactHistorySession] = []
    skipped_budget: list[str] = []
    estimated_source_tokens = 0

    if should_compact:
        # oldest-first 顺序选择。
        oldest_first = list(reversed(uncovered_candidates))
        total = 0
        for i, session in enumerate(oldest_first):
            if len(source_sessions) >= cfg.batch_session_limit:
                break
            session_tokens = estimate_compact_session_tokens(session, estimator=est)
            if total + session_tokens > cfg.source_budget_tokens:
                if not source_sessions:
                    # 第一条 source 单独超过预算，仍选择这一条。
                    source_sessions.append(session)
                    total += session_tokens
                    actions.append("single_source_exceeds_budget")
                else:
                    # 已有 source 后遇到预算上限，停止选择。
                    actions.append("source_budget_reached")
                    skipped_budget = [s.session_id for s in oldest_first[i:]]
                break
            source_sessions.append(session)
            total += session_tokens
        estimated_source_tokens = total

    return CompactPlan(
        should_compact=should_compact,
        trigger=trigger,
        source_sessions=tuple(source_sessions),
        tail_sessions=tuple(tail_list),
        skipped_covered_session_ids=tuple(skipped_covered),
        skipped_budget_session_ids=tuple(skipped_budget),
        uncovered_session_ids=uncovered_session_ids,
        estimated_source_tokens=estimated_source_tokens,
        estimated_tail_tokens=estimated_tail_tokens,
        actions=tuple(actions),
    )


def compact_plan_to_dict(plan: CompactPlan) -> dict[str, Any]:
    """把 CompactPlan 转成可 JSON 序列化的 dict。不修改 plan。"""

    def _session_to_dict(session: CompactHistorySession) -> dict[str, str]:
        return {
            "session_id": session.session_id,
            "created_at": session.created_at,
            "question": session.question,
            "answer": session.answer,
        }

    return {
        "should_compact": plan.should_compact,
        "trigger": plan.trigger,
        "source_sessions": [_session_to_dict(s) for s in plan.source_sessions],
        "tail_sessions": [_session_to_dict(s) for s in plan.tail_sessions],
        "skipped_covered_session_ids": list(plan.skipped_covered_session_ids),
        "skipped_budget_session_ids": list(plan.skipped_budget_session_ids),
        "uncovered_session_ids": list(plan.uncovered_session_ids),
        "estimated_source_tokens": plan.estimated_source_tokens,
        "estimated_tail_tokens": plan.estimated_tail_tokens,
        "actions": list(plan.actions),
    }


# ---------------------------------------------------------------------------
# compact summarizer prompt
# ---------------------------------------------------------------------------

COMPACT_SUMMARY_REQUIRED_HEADINGS = (
    "## 当前任务",
    "## 当前判断",
    "## 卡点",
    "## 下一步检索指针",
    "## 用户偏好",
    "## 最近完成",
)

_COMPACT_SUMMARY_SYSTEM_PROMPT = """你是 compact summarizer。读取 previous summary 与本批 source sessions，按下列规则产出指针式摘要。

输出要求：
- 使用当前用户语言写 summary。
- 使用下列 headings，保持顺序：## 当前任务 / ## 当前判断 / ## 卡点 / ## 下一步检索指针 / ## 用户偏好 / ## 最近完成
- 只记录能帮助下一轮继续工作的事实。
- 保留 session_id、record_id、文件路径、窗口标题、错误文本。
- 敏感值写为 [REDACTED]。
- 输出 Markdown 正文。
- 禁止输出解释、寒暄、代码块包裹。
- 禁止写入 base64、data URL、完整图片内容。
- summary 总长度不超过 target_summary_tokens。
"""

# base64-like 片段：ASCII letters、digits、+、/、= 连续超过 500 字符。
_BASE64_LIKE_PATTERN = re.compile(r"[A-Za-z0-9+/=]{501,}")


@dataclass(frozen=True)
class CompactSummaryConfig:
    """compact summary 模型与 prompt 预算配置。"""

    model_max_input_tokens: int = 24000
    model_max_output_tokens: int = 1600
    source_budget_tokens: int = 18000
    template_budget_tokens: int = 2000
    previous_summary_budget_tokens: int = 2000
    target_summary_tokens: int = 1200
    session_answer_head_chars: int = 4000
    session_answer_tail_chars: int = 2000


@dataclass(frozen=True)
class CompactSummaryPrompt:
    messages: tuple[dict[str, str], ...]
    source_session_ids: tuple[str, ...]
    estimated_input_tokens: int
    estimated_source_tokens: int
    estimated_previous_summary_tokens: int
    estimated_template_tokens: int
    actions: tuple[str, ...]


@dataclass(frozen=True)
class CompactSummaryValidation:
    summary: str
    estimate: CompactEstimate
    headings: tuple[str, ...]


def _require_positive_int(value: Any, name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{name} must be int")
    if value < 1:
        raise ValueError(f"{name} must be >= 1")


def validate_compact_summary_config(
    config: CompactSummaryConfig,
) -> CompactSummaryConfig:
    """校验 summary 配置，返回原 config；非法时抛 ValueError。"""
    for name in (
        "model_max_input_tokens",
        "model_max_output_tokens",
        "source_budget_tokens",
        "template_budget_tokens",
        "previous_summary_budget_tokens",
        "target_summary_tokens",
        "session_answer_head_chars",
        "session_answer_tail_chars",
    ):
        _require_positive_int(getattr(config, name), name)
    if config.model_max_input_tokens <= (
        config.template_budget_tokens + config.previous_summary_budget_tokens
    ):
        raise ValueError(
            "model_max_input_tokens must be > template_budget_tokens + previous_summary_budget_tokens"
        )
    if config.source_budget_tokens >= config.model_max_input_tokens:
        raise ValueError(
            "source_budget_tokens must be < model_max_input_tokens"
        )
    return config


def compact_text_head_tail(text: str, *, head_chars: int, tail_chars: int) -> str:
    """文本超长时保留 head + marker + tail；否则原样返回。不修改输入。"""
    if not isinstance(text, str):
        raise ValueError("text must be str")
    if not isinstance(head_chars, int) or isinstance(head_chars, bool) or head_chars < 0:
        raise ValueError("head_chars must be non-negative int")
    if not isinstance(tail_chars, int) or isinstance(tail_chars, bool) or tail_chars < 0:
        raise ValueError("tail_chars must be non-negative int")
    if len(text) <= head_chars + tail_chars:
        return text
    omitted = len(text) - head_chars - tail_chars
    head = text[:head_chars] if head_chars > 0 else ""
    tail = text[-tail_chars:] if tail_chars > 0 else ""
    return f"{head}\n[TRUNCATED middle_chars={omitted}]\n{tail}"


def extract_markdown_h2_headings(text: str) -> tuple[str, ...]:
    """提取 Markdown H2 标题行，用于 summary schema 校验。"""
    if not isinstance(text, str):
        raise ValueError("text must be str")
    headings: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            headings.append(stripped)
    return tuple(headings)


def render_compact_source_sessions(
    sessions: tuple[CompactHistorySession, ...],
    *,
    config: CompactSummaryConfig,
    estimator: ContextTokenEstimator | None = None,
) -> tuple[str, tuple[str, ...], int, tuple[str, ...]]:
    """渲染 source sessions 为文本，按 source_budget_tokens 限制总量。

    返回 (source_text, included_session_ids, estimated_source_tokens, actions)。
    oldest-first 顺序，无跳洞选择。
    """
    est = estimator or ContextTokenEstimator()
    actions: list[str] = []
    blocks: list[str] = []
    included: list[str] = []
    total = 0

    for session in sessions:
        # question 优先完整保留；answer 超长时 head/tail 收缩。
        answer = session.answer
        session_actions: list[str] = []
        if len(answer) > config.session_answer_head_chars + config.session_answer_tail_chars:
            answer = compact_text_head_tail(
                answer,
                head_chars=config.session_answer_head_chars,
                tail_chars=config.session_answer_tail_chars,
            )
            session_actions.append("source_session_clipped")

        block = (
            f"### session_id: {session.session_id}\n"
            f"created_at: {session.created_at}\n\n"
            f"[question]\n{session.question}\n\n"
            f"[answer]\n{answer}"
        )
        block_tokens = est.estimate_text(block).tokens

        if not blocks:
            # 第一条始终保留（即使单独超预算），后续依据累加预算。
            blocks.append(block)
            included.append(session.session_id)
            total += block_tokens
            for action in session_actions:
                if action not in actions:
                    actions.append(action)
            continue

        if total + block_tokens > config.source_budget_tokens:
            if "source_budget_reached" not in actions:
                actions.append("source_budget_reached")
            break

        blocks.append(block)
        included.append(session.session_id)
        total += block_tokens
        for action in session_actions:
            if action not in actions:
                actions.append(action)
    source_text = "\n\n".join(blocks)
    return source_text, tuple(included), total, tuple(actions)


def build_compact_summary_prompt(
    *,
    previous_state: RollingSummaryState,
    plan: CompactPlan,
    config: CompactSummaryConfig | None = None,
    estimator: ContextTokenEstimator | None = None,
) -> CompactSummaryPrompt:
    """构建 compact summary 的 system + user messages。不修改输入。"""
    cfg = validate_compact_summary_config(
        config if config is not None else CompactSummaryConfig()
    )
    est = estimator or ContextTokenEstimator()

    if not plan.should_compact:
        raise ValueError("plan.should_compact must be True")
    if not plan.source_sessions:
        raise ValueError("plan.source_sessions must be non-empty")

    # previous summary 收缩。
    actions: list[str] = []
    previous_summary = previous_state.summary
    prev_tokens = est.estimate_text(previous_summary).tokens
    if prev_tokens > cfg.previous_summary_budget_tokens:
        # 按 chars 估算：rough 1 token ≈ 4 chars（ASCII）或 1 char（CJK），取保守 chars 限制。
        prev_chars_limit = cfg.previous_summary_budget_tokens * 2
        previous_summary = compact_text_head_tail(
            previous_summary,
            head_chars=prev_chars_limit // 2,
            tail_chars=prev_chars_limit // 2,
        )
        prev_tokens = est.estimate_text(previous_summary).tokens
        actions.append("previous_summary_clipped")

    # source sessions 渲染（plan.source_sessions 已是 oldest-first）。
    source_text, included_ids, source_tokens, source_actions = render_compact_source_sessions(
        plan.source_sessions,
        config=cfg,
        estimator=est,
    )
    for a in source_actions:
        if a not in actions:
            actions.append(a)

    user_content = (
        f"[previous summary]\n{previous_summary}\n\n"
        f"[source sessions]\n{source_text}\n\n"
        f"target_summary_tokens: {cfg.target_summary_tokens}"
    )

    system_message = {"role": "system", "content": _COMPACT_SUMMARY_SYSTEM_PROMPT}
    user_message = {"role": "user", "content": user_content}
    messages = (system_message, user_message)

    template_tokens = est.estimate_text(_COMPACT_SUMMARY_SYSTEM_PROMPT).tokens
    input_tokens = (
        est.estimate_messages(list(messages)).tokens
    )

    if input_tokens > cfg.model_max_input_tokens:
        raise ValueError(
            f"compact summary prompt exceeds model_max_input_tokens={cfg.model_max_input_tokens}"
        )

    return CompactSummaryPrompt(
        messages=messages,
        source_session_ids=included_ids,
        estimated_input_tokens=input_tokens,
        estimated_source_tokens=source_tokens,
        estimated_previous_summary_tokens=prev_tokens,
        estimated_template_tokens=template_tokens,
        actions=tuple(actions),
    )


def normalize_compact_summary_text(raw: str) -> str:
    """strip 首尾空白；若整段被单层 triple backticks 包裹则去掉 fence。"""
    if not isinstance(raw, str):
        raise ValueError("raw summary must be str")
    text = raw.strip()
    if text.startswith("```") and text.endswith("```"):
        # 去掉首尾 fence 行。
        inner = text[3:]
        # 去掉开头的 language 标记（如 ```markdown）。
        if inner.startswith("\n"):
            inner = inner[1:]
        elif inner and not inner.startswith("\n"):
            # 可能是 ```markdown\n...
            nl = inner.find("\n")
            if nl != -1:
                inner = inner[nl + 1 :]
        # 去掉结尾的 ```（已 strip 过，text 末尾就是 ```）。
        if inner.endswith("```"):
            inner = inner[:-3].rstrip()
        text = inner.strip()
    return text


def validate_compact_summary_text(
    raw: str,
    *,
    config: CompactSummaryConfig | None = None,
    estimator: ContextTokenEstimator | None = None,
) -> CompactSummaryValidation:
    """校验模型产出的 summary 文本。失败抛 ValueError。"""
    cfg = validate_compact_summary_config(
        config if config is not None else CompactSummaryConfig()
    )
    est = estimator or ContextTokenEstimator()

    summary = normalize_compact_summary_text(raw)
    if not summary:
        raise ValueError("summary must be non-empty")

    headings = extract_markdown_h2_headings(summary)
    if headings != COMPACT_SUMMARY_REQUIRED_HEADINGS:
        missing = [
            heading
            for heading in COMPACT_SUMMARY_REQUIRED_HEADINGS
            if heading not in headings
        ]
        if missing:
            raise ValueError(f"summary missing heading: {missing[0]}")
        raise ValueError("summary headings order/schema mismatch")
    tokens = est.estimate_text(summary)
    if tokens.tokens > cfg.target_summary_tokens:
        raise ValueError(
            f"summary tokens {tokens.tokens} exceed target_summary_tokens={cfg.target_summary_tokens}"
        )

    if "data:image/" in summary:
        raise ValueError("summary must not contain data:image/")

    if _BASE64_LIKE_PATTERN.search(summary):
        raise ValueError("summary must not contain long base64-like fragment")

    return CompactSummaryValidation(
        summary=summary,
        estimate=CompactEstimate(
            source=tokens.source, tokens=tokens.tokens, chars=tokens.chars
        ),
        headings=headings,
    )


def build_compact_success_state(
    *,
    previous_state: RollingSummaryState,
    summary: str,
    source_session_ids: Iterable[str],
    updated_at: str | None = None,
    config: CompactSummaryConfig | None = None,
    estimator: ContextTokenEstimator | None = None,
) -> RollingSummaryState:
    """合并 previous covered ids 与本批 source ids，生成新 RollingSummaryState。不写 store。"""
    cfg = validate_compact_summary_config(
        config if config is not None else CompactSummaryConfig()
    )
    # 先校验 summary 文本。
    validation = validate_compact_summary_text(
        summary, config=cfg, estimator=estimator
    )

    merged_ids = normalize_session_ids(
        list(previous_state.covered_session_ids) + list(source_session_ids)
    )
    source_session_count = len(merged_ids)

    return build_rolling_summary_state(
        summary=validation.summary,
        covered_session_ids=merged_ids,
        source_session_count=source_session_count,
        updated_at=updated_at,
        last_error=None,
        estimator=estimator,
    )


def compact_summary_prompt_to_dict(prompt: CompactSummaryPrompt) -> dict[str, Any]:
    """把 CompactSummaryPrompt 转成可 JSON 序列化的 dict。不共享可变引用。"""
    return {
        "messages": [
            {"role": msg["role"], "content": msg["content"]}
            for msg in prompt.messages
        ],
        "source_session_ids": list(prompt.source_session_ids),
        "estimated_input_tokens": prompt.estimated_input_tokens,
        "estimated_source_tokens": prompt.estimated_source_tokens,
        "estimated_previous_summary_tokens": prompt.estimated_previous_summary_tokens,
        "estimated_template_tokens": prompt.estimated_template_tokens,
        "actions": list(prompt.actions),
    }


# ---------------------------------------------------------------------------
# compact executor
# ---------------------------------------------------------------------------


class CompactModelClient(Protocol):
    """Compact executor 使用的最小模型客户端协议。"""

    def complete_chat(
        self,
        *,
        messages: list[dict[str, str]],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """返回 summary 文本。"""


@dataclass(frozen=True)
class CompactExecutionResult:
    attempted: bool
    compacted: bool
    status: str
    trigger: str
    plan: CompactPlan
    prompt: CompactSummaryPrompt | None
    summary_state: RollingSummaryState
    metrics: CompactMetrics
    lock: CompactLock | None
    started_at: str | None
    finished_at: str | None
    error_type: str | None
    error_message: str | None
    actions: tuple[str, ...]


def _compact_source_from_force(force: bool, source: str | None) -> str:
    compact_source = source if source is not None else ("manual" if force else "auto")
    if compact_source not in _COMPACT_LOCK_SOURCES:
        raise ValueError(f"source must be one of {sorted(_COMPACT_LOCK_SOURCES)}")
    return compact_source


def _compact_now(now: datetime | None = None) -> datetime:
    return now if now is not None else datetime.now(UTC)


def _compact_error_message(exc: Exception) -> str:
    message = str(exc).strip()
    if message:
        return message
    return exc.__class__.__name__


def execute_compact(
    *,
    sessions: Iterable[Any],
    state_store: CompactStateStore,
    model_client: CompactModelClient,
    planner_config: CompactPlannerConfig | None = None,
    summary_config: CompactSummaryConfig | None = None,
    force: bool = False,
    source: str | None = None,
    lock_ttl_seconds: int = DEFAULT_COMPACT_LOCK_TTL_SECONDS,
    now: datetime | None = None,
    estimator: ContextTokenEstimator | None = None,
) -> CompactExecutionResult:
    """执行一次 compact：plan -> lock -> model -> validate -> commit。

    自动和手动触发共用同一路径。未触发或锁占用时不调用模型。
    """
    compact_source = _compact_source_from_force(force, source)
    est = estimator or ContextTokenEstimator()
    previous_state = state_store.load_summary()
    plan = plan_compact(
        sessions=sessions,
        covered_session_ids=previous_state.covered_session_ids,
        config=planner_config,
        force=force,
        estimator=est,
    )

    if not plan.should_compact:
        metrics = state_store.load_metrics()
        return CompactExecutionResult(
            attempted=False,
            compacted=False,
            status="skipped",
            trigger=plan.trigger,
            plan=plan,
            prompt=None,
            summary_state=previous_state,
            metrics=metrics,
            lock=None,
            started_at=None,
            finished_at=None,
            error_type=None,
            error_message=None,
            actions=tuple(list(plan.actions) + ["no_compact_needed"]),
        )

    started_dt = _compact_now(now)
    started_at = started_dt.isoformat()
    lock = state_store.acquire_lock(
        source=compact_source,
        ttl_seconds=lock_ttl_seconds,
        now=started_dt,
    )
    if lock is None:
        metrics = state_store.load_metrics()
        return CompactExecutionResult(
            attempted=False,
            compacted=False,
            status="locked",
            trigger=plan.trigger,
            plan=plan,
            prompt=None,
            summary_state=previous_state,
            metrics=metrics,
            lock=None,
            started_at=started_at,
            finished_at=None,
            error_type=None,
            error_message=None,
            actions=tuple(list(plan.actions) + ["lock_active"]),
        )

    prompt: CompactSummaryPrompt | None = None
    prompt_actions: list[str] = []
    try:
        prompt = build_compact_summary_prompt(
            previous_state=previous_state,
            plan=plan,
            config=summary_config,
            estimator=est,
        )
        prompt_actions = list(prompt.actions)
        cfg = validate_compact_summary_config(
            summary_config if summary_config is not None else CompactSummaryConfig()
        )
        raw_summary = model_client.complete_chat(
            messages=[
                {"role": msg["role"], "content": msg["content"]}
                for msg in prompt.messages
            ],
            temperature=0.0,
            max_tokens=cfg.model_max_output_tokens,
        )
        finished_at = _compact_now(now).isoformat()
        new_state = build_compact_success_state(
            previous_state=previous_state,
            summary=raw_summary,
            source_session_ids=prompt.source_session_ids,
            updated_at=finished_at,
            config=cfg,
            estimator=est,
        )
        state_store.save_summary(new_state)
        metrics = state_store.save_success_metrics(
            started_at=started_at,
            finished_at=finished_at,
            source_session_count=len(prompt.source_session_ids),
            covered_session_count=len(new_state.covered_session_ids),
            summary_tokens=new_state.estimate.tokens,
            source_tokens=prompt.estimated_source_tokens,
        )
        return CompactExecutionResult(
            attempted=True,
            compacted=True,
            status="ok",
            trigger=plan.trigger,
            plan=plan,
            prompt=prompt,
            summary_state=new_state,
            metrics=metrics,
            lock=lock,
            started_at=started_at,
            finished_at=finished_at,
            error_type=None,
            error_message=None,
            actions=tuple(list(plan.actions) + prompt_actions + ["compact_succeeded"]),
        )
    except Exception as exc:
        finished_at = _compact_now(now).isoformat()
        error_type = exc.__class__.__name__
        error_message = _compact_error_message(exc)
        source_tokens = (
            prompt.estimated_source_tokens
            if prompt is not None
            else plan.estimated_source_tokens
        )
        metrics = state_store.save_error_metrics(
            started_at=started_at,
            finished_at=finished_at,
            source_session_count=len(plan.source_sessions),
            covered_session_count=len(previous_state.covered_session_ids),
            summary_tokens=0,
            source_tokens=source_tokens,
            error_type=error_type,
            error_message=error_message,
        )
        return CompactExecutionResult(
            attempted=True,
            compacted=False,
            status="error",
            trigger=plan.trigger,
            plan=plan,
            prompt=prompt,
            summary_state=previous_state,
            metrics=metrics,
            lock=lock,
            started_at=started_at,
            finished_at=finished_at,
            error_type=error_type,
            error_message=error_message,
            actions=tuple(list(plan.actions) + prompt_actions + ["compact_failed"]),
        )
    finally:
        state_store.release_lock(owner=COMPACT_LOCK_OWNER)


def compact_execution_result_to_dict(
    result: CompactExecutionResult,
) -> dict[str, Any]:
    """把 CompactExecutionResult 转为可 JSON 序列化 dict。"""
    return {
        "attempted": result.attempted,
        "compacted": result.compacted,
        "status": result.status,
        "trigger": result.trigger,
        "plan": compact_plan_to_dict(result.plan),
        "prompt": (
            compact_summary_prompt_to_dict(result.prompt)
            if result.prompt is not None
            else None
        ),
        "summary_state": rolling_summary_to_payload(result.summary_state),
        "metrics": compact_metrics_to_payload(result.metrics),
        "lock": compact_lock_to_payload(result.lock) if result.lock is not None else None,
        "started_at": result.started_at,
        "finished_at": result.finished_at,
        "error_type": result.error_type,
        "error_message": result.error_message,
        "actions": list(result.actions),
    }