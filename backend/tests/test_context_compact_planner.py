from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from datetime import datetime, timezone

import pytest

from app.services.context_budget import ContextTokenEstimator
from app.services.context_summary import (
    CompactHistorySession,
    CompactPlan,
    CompactPlannerConfig,
    compact_history_session_from_value,
    compact_plan_to_dict,
    estimate_compact_session_tokens,
    plan_compact,
    validate_compact_planner_config,
)


def make_session(
    i: int, *, question: str | None = None, answer: str | None = None
) -> dict[str, str]:
    return {
        "session_id": f"s{i}",
        "created_at": f"2026-07-06T12:{i:02d}:00+00:00",
        "question": question if question is not None else f"question {i}",
        "answer": answer if answer is not None else f"answer {i}",
    }


def make_sessions(n: int) -> list[dict[str, str]]:
    # newest-first：i 越大越新
    return [make_session(i) for i in range(n, 0, -1)]


# ---------------------------------------------------------------------------
# 1. 空 sessions
# ---------------------------------------------------------------------------


def test_empty_sessions_returns_no_compact():
    plan = plan_compact(sessions=[], covered_session_ids=[])
    assert plan.should_compact is False
    assert plan.trigger == "none"
    assert plan.source_sessions == ()
    assert plan.tail_sessions == ()
    assert plan.estimated_source_tokens == 0
    assert plan.estimated_tail_tokens == 0


# ---------------------------------------------------------------------------
# 2. raw_tail_turns=2 保留最新两条
# ---------------------------------------------------------------------------


def test_raw_tail_turns_keeps_newest_two():
    sessions = make_sessions(5)  # [s5, s4, s3, s2, s1]
    plan = plan_compact(
        sessions=sessions,
        covered_session_ids=[],
        config=CompactPlannerConfig(uncovered_session_threshold=10, history_trigger_tokens=10**9),
    )
    tail_ids = [s.session_id for s in plan.tail_sessions]
    assert tail_ids == ["s5", "s4"]


# ---------------------------------------------------------------------------
# 3. source_sessions oldest-first
# ---------------------------------------------------------------------------


def test_source_sessions_returned_oldest_first():
    sessions = make_sessions(5)  # [s5, s4, s3, s2, s1]
    plan = plan_compact(
        sessions=sessions,
        covered_session_ids=[],
        config=CompactPlannerConfig(
            raw_tail_turns=2,
            uncovered_session_threshold=3,
            history_trigger_tokens=10**9,
        ),
    )
    assert plan.should_compact is True
    # older = [s3, s2, s1] newest-first -> source oldest-first = [s1, s2, s3]
    source_ids = [s.session_id for s in plan.source_sessions]
    assert source_ids == ["s1", "s2", "s3"]


# ---------------------------------------------------------------------------
# 4. covered ids 被跳过
# ---------------------------------------------------------------------------


def test_covered_ids_skipped_and_recorded():
    sessions = make_sessions(5)  # [s5, s4, s3, s2, s1]
    plan = plan_compact(
        sessions=sessions,
        covered_session_ids=["s3", "s1"],
        config=CompactPlannerConfig(
            raw_tail_turns=2,
            uncovered_session_threshold=1,
            history_trigger_tokens=10**9,
        ),
    )
    # older = [s3, s2, s1], covered {s3, s1} -> skipped_covered newest-first = [s3, s1]
    assert plan.skipped_covered_session_ids == ("s3", "s1")
    # 未覆盖候选 = [s2]
    assert plan.uncovered_session_ids == ("s2",)
    source_ids = [s.session_id for s in plan.source_sessions]
    assert source_ids == ["s2"]


def test_covered_ids_do_not_affect_raw_tail():
    sessions = make_sessions(3)  # [s3, s2, s1]
    plan = plan_compact(
        sessions=sessions,
        covered_session_ids=["s3", "s2"],
        config=CompactPlannerConfig(raw_tail_turns=2),
    )
    # 最新两轮即使已覆盖也按 raw tail 保留
    tail_ids = [s.session_id for s in plan.tail_sessions]
    assert tail_ids == ["s3", "s2"]


# ---------------------------------------------------------------------------
# 5. 未达阈值且 force=False 不选择 source
# ---------------------------------------------------------------------------


def test_below_threshold_no_source_selected():
    sessions = make_sessions(3)  # [s3, s2, s1]
    plan = plan_compact(
        sessions=sessions,
        covered_session_ids=[],
        config=CompactPlannerConfig(
            raw_tail_turns=2,
            uncovered_session_threshold=10,
            history_trigger_tokens=10**9,
        ),
    )
    assert plan.should_compact is False
    assert plan.trigger == "none"
    assert plan.source_sessions == ()
    assert plan.estimated_source_tokens == 0
    # 仍返回 tail 与 uncovered
    assert [s.session_id for s in plan.tail_sessions] == ["s3", "s2"]
    assert plan.uncovered_session_ids == ("s1",)
    assert plan.estimated_tail_tokens > 0
    assert plan.actions == ()


# ---------------------------------------------------------------------------
# 6. 未覆盖数量达阈值自动触发
# ---------------------------------------------------------------------------


def test_session_threshold_auto_trigger():
    sessions = make_sessions(8)  # [s8..s1]
    plan = plan_compact(
        sessions=sessions,
        covered_session_ids=[],
        config=CompactPlannerConfig(
            raw_tail_turns=2,
            uncovered_session_threshold=6,
            history_trigger_tokens=10**9,
        ),
    )
    assert plan.should_compact is True
    assert plan.trigger == "session_threshold"
    assert "session_threshold_reached" in plan.actions
    assert "token_threshold_reached" not in plan.actions


# ---------------------------------------------------------------------------
# 7. 未覆盖 rough tokens 达阈值自动触发
# ---------------------------------------------------------------------------


def test_token_threshold_auto_trigger():
    sessions = make_sessions(3)  # [s3, s2, s1]
    # 未覆盖候选 = [s1]，1 条不达 session threshold(6)，但把 token 阈值设很低
    plan = plan_compact(
        sessions=sessions,
        covered_session_ids=[],
        config=CompactPlannerConfig(
            raw_tail_turns=2,
            uncovered_session_threshold=6,
            history_trigger_tokens=1,
        ),
    )
    assert plan.should_compact is True
    assert plan.trigger == "token_threshold"
    assert "token_threshold_reached" in plan.actions
    assert "session_threshold_reached" not in plan.actions


# ---------------------------------------------------------------------------
# 8. 两阈值同时达到 trigger 取 session_threshold
# ---------------------------------------------------------------------------


def test_both_thresholds_session_priority():
    sessions = make_sessions(8)
    plan = plan_compact(
        sessions=sessions,
        covered_session_ids=[],
        config=CompactPlannerConfig(
            raw_tail_turns=2,
            uncovered_session_threshold=6,
            history_trigger_tokens=1,
        ),
    )
    assert plan.trigger == "session_threshold"
    assert "session_threshold_reached" in plan.actions
    assert "token_threshold_reached" in plan.actions


# ---------------------------------------------------------------------------
# 9. force=True 存在未覆盖候选触发 manual
# ---------------------------------------------------------------------------


def test_force_true_with_candidates_triggers_manual():
    sessions = make_sessions(3)
    plan = plan_compact(
        sessions=sessions,
        covered_session_ids=[],
        config=CompactPlannerConfig(
            raw_tail_turns=2,
            uncovered_session_threshold=10,
            history_trigger_tokens=10**9,
        ),
        force=True,
    )
    assert plan.should_compact is True
    assert plan.trigger == "manual"
    assert "manual_requested" in plan.actions
    # 手动触发仍遵守 batch/budget，选 s1
    assert [s.session_id for s in plan.source_sessions] == ["s1"]


# ---------------------------------------------------------------------------
# 10. force=True 无未覆盖候选不 compact
# ---------------------------------------------------------------------------


def test_force_true_no_candidates_no_compact():
    sessions = make_sessions(2)  # [s2, s1]，raw_tail_turns=2 全是 tail
    plan = plan_compact(
        sessions=sessions,
        covered_session_ids=[],
        config=CompactPlannerConfig(raw_tail_turns=2),
        force=True,
    )
    assert plan.should_compact is False
    assert plan.trigger == "none"
    assert "manual_requested" in plan.actions
    assert "no_source_sessions" in plan.actions
    assert plan.source_sessions == ()


# ---------------------------------------------------------------------------
# 11. batch_session_limit 限制 source 数量
# ---------------------------------------------------------------------------


def test_batch_session_limit_caps_source_count():
    sessions = make_sessions(8)  # older = [s6..s1] 共 6 条
    plan = plan_compact(
        sessions=sessions,
        covered_session_ids=[],
        config=CompactPlannerConfig(
            raw_tail_turns=2,
            uncovered_session_threshold=6,
            history_trigger_tokens=10**9,
            batch_session_limit=3,
            source_budget_tokens=10**9,
        ),
    )
    assert len(plan.source_sessions) == 3
    # oldest-first
    assert [s.session_id for s in plan.source_sessions] == ["s1", "s2", "s3"]
    # batch 限制停止，不记 skipped_budget
    assert plan.skipped_budget_session_ids == ()


# ---------------------------------------------------------------------------
# 12. source_budget_tokens 限制并记录 skipped budget
# ---------------------------------------------------------------------------


def test_source_budget_records_skipped_budget_ids():
    # 构造每条 token 较大、第一条后即超预算的场景
    big_question = "x" * 200
    big_answer = "y" * 200
    sessions = [make_session(i, question=big_question, answer=big_answer) for i in range(5, 0, -1)]
    plan = plan_compact(
        sessions=sessions,
        covered_session_ids=[],
        config=CompactPlannerConfig(
            raw_tail_turns=2,
            uncovered_session_threshold=3,
            history_trigger_tokens=10**9,
            batch_session_limit=10,
            source_budget_tokens=120,  # 一条 question+answer 估算约 100+overhead
        ),
    )
    assert plan.should_compact is True
    assert "source_budget_reached" in plan.actions
    # 至少选了一条
    assert len(plan.source_sessions) >= 1
    # 剩余记入 skipped_budget
    assert len(plan.skipped_budget_session_ids) > 0
    # 已选的不在 skipped_budget 中
    selected_ids = {s.session_id for s in plan.source_sessions}
    for sid in plan.skipped_budget_session_ids:
        assert sid not in selected_ids


# ---------------------------------------------------------------------------
# 13. 第一条 source 单独超过预算仍选择一条
# ---------------------------------------------------------------------------


def test_single_source_exceeds_budget_still_selects_one():
    huge_question = "q" * 5000
    huge_answer = "a" * 5000
    sessions = [make_session(i, question=huge_question, answer=huge_answer) for i in range(3, 0, -1)]
    plan = plan_compact(
        sessions=sessions,
        covered_session_ids=[],
        config=CompactPlannerConfig(
            raw_tail_turns=2,
            uncovered_session_threshold=1,
            history_trigger_tokens=10**9,
            batch_session_limit=10,
            source_budget_tokens=100,
        ),
    )
    assert plan.should_compact is True
    assert "single_source_exceeds_budget" in plan.actions
    assert len(plan.source_sessions) == 1
    assert plan.estimated_source_tokens > 100


# ---------------------------------------------------------------------------
# 14. tail token 估算写入 estimated_tail_tokens
# ---------------------------------------------------------------------------


def test_estimated_tail_tokens_written():
    sessions = make_sessions(3)  # [s3, s2, s1]
    plan = plan_compact(
        sessions=sessions,
        covered_session_ids=[],
        config=CompactPlannerConfig(raw_tail_turns=2),
    )
    expected = (
        estimate_compact_session_tokens(
            CompactHistorySession(
                session_id="s3",
                created_at="2026-07-06T12:03:00+00:00",
                question="question 3",
                answer="answer 3",
            )
        )
        + estimate_compact_session_tokens(
            CompactHistorySession(
                session_id="s2",
                created_at="2026-07-06T12:02:00+00:00",
                question="question 2",
                answer="answer 2",
            )
        )
    )
    assert plan.estimated_tail_tokens == expected


# ---------------------------------------------------------------------------
# 15. invalid config 抛 ValueError
# ---------------------------------------------------------------------------


def test_invalid_config_raises_value_error():
    with pytest.raises(ValueError, match="raw_tail_turns"):
        validate_compact_planner_config(
            CompactPlannerConfig(raw_tail_turns=-1)
        )
    with pytest.raises(ValueError, match="batch_session_limit"):
        validate_compact_planner_config(
            CompactPlannerConfig(batch_session_limit=0)
        )
    with pytest.raises(ValueError, match="source_budget_tokens"):
        validate_compact_planner_config(
            CompactPlannerConfig(source_budget_tokens=0)
        )
    with pytest.raises(ValueError, match="uncovered_session_threshold"):
        validate_compact_planner_config(
            CompactPlannerConfig(uncovered_session_threshold=0)
        )
    with pytest.raises(ValueError, match="history_trigger_tokens"):
        validate_compact_planner_config(
            CompactPlannerConfig(history_trigger_tokens=0)
        )


def test_invalid_config_via_plan_compact_raises():
    with pytest.raises(ValueError):
        plan_compact(
            sessions=[],
            covered_session_ids=[],
            config=CompactPlannerConfig(batch_session_limit=0),
        )


# ---------------------------------------------------------------------------
# 16. dict 与对象输入都能转换
# ---------------------------------------------------------------------------


@dataclass
class _FakeSession:
    session_id: str
    created_at: object
    question: str
    answer: str


def test_dict_and_object_inputs_both_convert():
    plan = plan_compact(
        sessions=[
            make_session(1),
            _FakeSession(
                session_id="s2",
                created_at=datetime(2026, 7, 6, 12, 2, 0, tzinfo=timezone.utc),
                question="question 2",
                answer="answer 2",
            ),
        ],
        covered_session_ids=[],
        config=CompactPlannerConfig(raw_tail_turns=2, uncovered_session_threshold=10),
    )
    # raw_tail_turns=2 -> 两条均进入 tail（newest-first 输入 [s1, s2]）
    assert [s.session_id for s in plan.tail_sessions] == ["s1", "s2"]
    # 验证对象输入的 created_at 被转成 isoformat
    obj_session = next(s for s in plan.tail_sessions if s.session_id == "s2")
    assert obj_session.created_at.startswith("2026-07-06T12:02")


def test_compact_history_session_from_value_dict():
    cs = compact_history_session_from_value(make_session(1))
    assert cs is not None
    assert cs.session_id == "s1"
    assert cs.question == "question 1"
    assert cs.answer == "answer 1"


def test_compact_history_session_from_value_object():
    obj = _FakeSession(
        session_id="s1",
        created_at="2026-07-06T12:01:00+00:00",
        question="q",
        answer="a",
    )
    cs = compact_history_session_from_value(obj)
    assert cs is not None
    assert cs.session_id == "s1"


def test_compact_history_session_from_value_missing_id_returns_none():
    assert compact_history_session_from_value({"question": "q", "answer": "a"}) is None
    assert compact_history_session_from_value({"session_id": "  ", "question": "q", "answer": "a"}) is None
    assert compact_history_session_from_value({"session_id": 123, "question": "q", "answer": "a"}) is None


# ---------------------------------------------------------------------------
# 17. 非 str question/answer 抛 ValueError
# ---------------------------------------------------------------------------


def test_non_str_question_raises():
    with pytest.raises(ValueError, match="question"):
        compact_history_session_from_value(
            {"session_id": "s1", "question": 123, "answer": "a"}
        )


def test_non_str_answer_raises():
    with pytest.raises(ValueError, match="answer"):
        compact_history_session_from_value(
            {"session_id": "s1", "question": "q", "answer": None}
        )


# ---------------------------------------------------------------------------
# 18. compact_plan_to_dict 可 JSON 序列化
# ---------------------------------------------------------------------------


def test_compact_plan_to_dict_json_serializable():
    sessions = make_sessions(5)
    plan = plan_compact(
        sessions=sessions,
        covered_session_ids=["s1"],
        config=CompactPlannerConfig(
            raw_tail_turns=2,
            uncovered_session_threshold=2,
            history_trigger_tokens=1,
        ),
        force=False,
    )
    payload = compact_plan_to_dict(plan)
    serialized = json.dumps(payload, ensure_ascii=False)
    assert isinstance(serialized, str)
    restored = json.loads(serialized)
    assert restored["should_compact"] == plan.should_compact
    assert restored["trigger"] == plan.trigger
    assert isinstance(restored["source_sessions"], list)
    assert isinstance(restored["tail_sessions"], list)
    assert isinstance(restored["skipped_covered_session_ids"], list)
    assert isinstance(restored["actions"], list)


def test_compact_plan_to_dict_does_not_modify_plan():
    sessions = make_sessions(5)
    plan = plan_compact(
        sessions=sessions,
        covered_session_ids=["s1"],
        config=CompactPlannerConfig(
            raw_tail_turns=2,
            uncovered_session_threshold=2,
            history_trigger_tokens=1,
        ),
    )
    snapshot_actions = tuple(plan.actions)
    snapshot_source = tuple(plan.source_sessions)
    compact_plan_to_dict(plan)
    assert plan.actions == snapshot_actions
    assert plan.source_sessions == snapshot_source


# ---------------------------------------------------------------------------
# 19. planner 不修改输入 list 或 dict
# ---------------------------------------------------------------------------


def test_plan_compact_does_not_modify_input():
    sessions = make_sessions(4)
    sessions_snapshot = copy.deepcopy(sessions)
    covered = ["s1", "s2"]
    covered_snapshot = list(covered)

    plan_compact(
        sessions=sessions,
        covered_session_ids=covered,
        config=CompactPlannerConfig(raw_tail_turns=2, uncovered_session_threshold=2),
    )
    assert sessions == sessions_snapshot
    assert covered == covered_snapshot


def test_compact_history_session_from_value_does_not_modify_dict():
    original = make_session(1)
    snapshot = copy.deepcopy(original)
    compact_history_session_from_value(original)
    assert original == snapshot


# ---------------------------------------------------------------------------
# 额外：covered ids 规范化（strip/去重）
# ---------------------------------------------------------------------------


def test_covered_ids_normalized():
    sessions = make_sessions(4)  # [s4, s3, s2, s1]
    plan = plan_compact(
        sessions=sessions,
        covered_session_ids=["  s2  ", "s2", "s1"],
        config=CompactPlannerConfig(raw_tail_turns=2, uncovered_session_threshold=10),
    )
    # older = [s2, s1]，均被覆盖
    assert plan.skipped_covered_session_ids == ("s2", "s1")
    assert plan.uncovered_session_ids == ()


def test_tail_empty_when_raw_tail_turns_zero():
    sessions = make_sessions(3)
    plan = plan_compact(
        sessions=sessions,
        covered_session_ids=[],
        config=CompactPlannerConfig(raw_tail_turns=0, uncovered_session_threshold=10),
    )
    assert plan.tail_sessions == ()
    assert plan.estimated_tail_tokens == 0
    assert plan.uncovered_session_ids == ("s3", "s2", "s1")
