from __future__ import annotations

import copy
import json

import pytest

from app.services.context_budget import ContextTokenEstimator
from app.services.context_summary import (
    COMPACT_SUMMARY_REQUIRED_HEADINGS,
    CompactEstimate,
    CompactHistorySession,
    CompactPlan,
    CompactPlannerConfig,
    CompactSummaryConfig,
    CompactSummaryPrompt,
    CompactSummaryValidation,
    RollingSummaryState,
    build_compact_summary_prompt,
    build_compact_success_state,
    build_rolling_summary_state,
    compact_summary_prompt_to_dict,
    compact_text_head_tail,
    empty_rolling_summary_state,
    extract_markdown_h2_headings,
    normalize_compact_summary_text,
    plan_compact,
    render_compact_source_sessions,
    validate_compact_summary_config,
    validate_compact_summary_text,
)


def make_session(i: int, *, question: str | None = None, answer: str | None = None) -> dict:
    return {
        "session_id": f"s{i}",
        "created_at": f"2026-07-06T12:{i:02d}:00+00:00",
        "question": question if question is not None else f"question {i}",
        "answer": answer if answer is not None else f"answer {i}",
    }


def make_compact_history_session(
    i: int, *, question: str | None = None, answer: str | None = None
) -> CompactHistorySession:
    return CompactHistorySession(
        session_id=f"s{i}",
        created_at=f"2026-07-06T12:{i:02d}:00+00:00",
        question=question if question is not None else f"question {i}",
        answer=answer if answer is not None else f"answer {i}",
    )


def make_plan(
    *,
    source_sessions: tuple[CompactHistorySession, ...],
    should_compact: bool = True,
) -> CompactPlan:
    return CompactPlan(
        should_compact=should_compact,
        trigger="session_threshold" if should_compact else "none",
        source_sessions=source_sessions,
        tail_sessions=(),
        skipped_covered_session_ids=(),
        skipped_budget_session_ids=(),
        uncovered_session_ids=tuple(s.session_id for s in source_sessions),
        estimated_source_tokens=0,
        estimated_tail_tokens=0,
        actions=(),
    )


def valid_summary() -> str:
    return "\n".join([
        "## 当前任务",
        "整理上下文管理 compact 链路。",
        "## 当前判断",
        "Token 估算、state store、planner 已完成。",
        "## 卡点",
        "等待 summarizer prompt 审查。",
        "## 下一步检索指针",
        "- session_id: s1",
        "- 关键词: compact planner",
        "## 用户偏好",
        "直接、具体、避免冗余表达。",
        "## 最近完成",
        "006 planner 测试通过。",
    ])


# ---------------------------------------------------------------------------
# 1. required headings 与 spec 一致
# ---------------------------------------------------------------------------


def test_required_headings_match_spec():
    assert COMPACT_SUMMARY_REQUIRED_HEADINGS == (
        "## 当前任务",
        "## 当前判断",
        "## 卡点",
        "## 下一步检索指针",
        "## 用户偏好",
        "## 最近完成",
    )


# ---------------------------------------------------------------------------
# 2. config 默认值接受
# ---------------------------------------------------------------------------


def test_validate_config_accepts_defaults():
    cfg = CompactSummaryConfig()
    assert validate_compact_summary_config(cfg) is cfg


# ---------------------------------------------------------------------------
# 3. config 字段非法抛 ValueError
# ---------------------------------------------------------------------------


def test_validate_config_rejects_invalid_fields():
    with pytest.raises(ValueError, match="model_max_input_tokens"):
        validate_compact_summary_config(
            CompactSummaryConfig(model_max_input_tokens=True)
        )
    with pytest.raises(ValueError, match="source_budget_tokens"):
        validate_compact_summary_config(
            CompactSummaryConfig(source_budget_tokens=0)
        )
    with pytest.raises(ValueError, match="target_summary_tokens"):
        validate_compact_summary_config(
            CompactSummaryConfig(target_summary_tokens=-1)
        )


def test_validate_config_rejects_invalid_relation():
    with pytest.raises(ValueError, match="model_max_input_tokens"):
        validate_compact_summary_config(
            CompactSummaryConfig(
                model_max_input_tokens=3000,
                template_budget_tokens=2000,
                previous_summary_budget_tokens=2000,
                source_budget_tokens=1000,
            )
        )
    # source_budget_tokens >= model_max_input_tokens（且 model 关系合法）
    with pytest.raises(ValueError, match="source_budget_tokens"):
        validate_compact_summary_config(
            CompactSummaryConfig(
                model_max_input_tokens=10000,
                template_budget_tokens=1000,
                previous_summary_budget_tokens=1000,
                source_budget_tokens=10000,
            )
        )


# ---------------------------------------------------------------------------
# 4. compact_text_head_tail 短文本原样返回
# ---------------------------------------------------------------------------


def test_compact_text_head_tail_short_returns_original():
    text = "short text"
    assert compact_text_head_tail(text, head_chars=100, tail_chars=100) == text


# ---------------------------------------------------------------------------
# 5. compact_text_head_tail 长文本保留头尾与 marker
# ---------------------------------------------------------------------------


def test_compact_text_head_tail_long_preserves_head_tail_marker():
    text = "x" * 1000
    result = compact_text_head_tail(text, head_chars=10, tail_chars=20)
    assert result.startswith("x" * 10)
    assert result.endswith("x" * 20)
    assert "[TRUNCATED middle_chars=970]" in result


def test_compact_text_head_tail_rejects_non_str():
    with pytest.raises(ValueError, match="text must be str"):
        compact_text_head_tail(123, head_chars=1, tail_chars=1)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 6. build_compact_summary_prompt 生成 system + user
# ---------------------------------------------------------------------------


def test_build_prompt_generates_system_and_user_messages():
    plan = make_plan(source_sessions=(make_compact_history_session(1),))
    prompt = build_compact_summary_prompt(
        previous_state=empty_rolling_summary_state(),
        plan=plan,
    )
    assert len(prompt.messages) == 2
    assert prompt.messages[0]["role"] == "system"
    assert prompt.messages[1]["role"] == "user"


# ---------------------------------------------------------------------------
# 7. prompt user 内容包含 previous summary
# ---------------------------------------------------------------------------


def test_prompt_user_content_includes_previous_summary():
    prev_state = build_rolling_summary_state(
        summary="previous summary content",
        covered_session_ids=["old1"],
        source_session_count=1,
    )
    plan = make_plan(source_sessions=(make_compact_history_session(1),))
    prompt = build_compact_summary_prompt(
        previous_state=prev_state,
        plan=plan,
    )
    assert "previous summary content" in prompt.messages[1]["content"]


# ---------------------------------------------------------------------------
# 8. prompt user 内容包含 session 字段
# ---------------------------------------------------------------------------


def test_prompt_user_content_includes_session_fields():
    plan = make_plan(source_sessions=(make_compact_history_session(1),))
    prompt = build_compact_summary_prompt(
        previous_state=empty_rolling_summary_state(),
        plan=plan,
    )
    user_content = prompt.messages[1]["content"]
    assert "s1" in user_content
    assert "2026-07-06T12:01:00+00:00" in user_content
    assert "question 1" in user_content
    assert "answer 1" in user_content


# ---------------------------------------------------------------------------
# 9. source sessions 保持 oldest-first
# ---------------------------------------------------------------------------


def test_prompt_source_sessions_oldest_first():
    sessions = (
        make_compact_history_session(1),
        make_compact_history_session(2),
        make_compact_history_session(3),
    )
    plan = make_plan(source_sessions=sessions)
    prompt = build_compact_summary_prompt(
        previous_state=empty_rolling_summary_state(),
        plan=plan,
    )
    assert prompt.source_session_ids == ("s1", "s2", "s3")
    # user content 中 s1 出现在 s2 之前
    user_content = prompt.messages[1]["content"]
    assert user_content.index("s1") < user_content.index("s2") < user_content.index("s3")


# ---------------------------------------------------------------------------
# 10. answer 超长被收缩
# ---------------------------------------------------------------------------


def test_prompt_answer_clipped_when_too_long():
    long_answer = "y" * 10000
    plan = make_plan(
        source_sessions=(make_compact_history_session(1, answer=long_answer),)
    )
    prompt = build_compact_summary_prompt(
        previous_state=empty_rolling_summary_state(),
        plan=plan,
    )
    assert "source_session_clipped" in prompt.actions
    assert "[TRUNCATED middle_chars=" in prompt.messages[1]["content"]


# ---------------------------------------------------------------------------
# 11. previous summary 超预算被收缩
# ---------------------------------------------------------------------------


def test_prompt_previous_summary_clipped():
    long_summary = "z" * 20000
    prev_state = build_rolling_summary_state(
        summary=long_summary,
        covered_session_ids=["old1"],
        source_session_count=1,
    )
    plan = make_plan(source_sessions=(make_compact_history_session(1),))
    prompt = build_compact_summary_prompt(
        previous_state=prev_state,
        plan=plan,
        config=CompactSummaryConfig(previous_summary_budget_tokens=100),
    )
    assert "previous_summary_clipped" in prompt.actions


# ---------------------------------------------------------------------------
# 12. source budget 达到上限只保留连续前缀
# ---------------------------------------------------------------------------


def test_prompt_source_budget_reached_keeps_prefix():
    big_answer = "x" * 20000
    sessions = tuple(
        make_compact_history_session(i, answer=big_answer) for i in range(1, 5)
    )
    plan = make_plan(source_sessions=sessions)
    prompt = build_compact_summary_prompt(
        previous_state=empty_rolling_summary_state(),
        plan=plan,
        config=CompactSummaryConfig(source_budget_tokens=4000),
    )
    assert "source_budget_reached" in prompt.actions
    # 只保留连续前缀
    assert len(prompt.source_session_ids) < 4
    # 包含第一条
    assert prompt.source_session_ids[0] == "s1"


# ---------------------------------------------------------------------------
# 13. should_compact=False 抛 ValueError
# ---------------------------------------------------------------------------


def test_build_prompt_rejects_should_compact_false():
    plan = make_plan(
        source_sessions=(make_compact_history_session(1),),
        should_compact=False,
    )
    with pytest.raises(ValueError, match="should_compact"):
        build_compact_summary_prompt(
            previous_state=empty_rolling_summary_state(),
            plan=plan,
        )


# ---------------------------------------------------------------------------
# 14. source_sessions 空抛 ValueError
# ---------------------------------------------------------------------------


def test_build_prompt_rejects_empty_source_sessions():
    plan = make_plan(source_sessions=(), should_compact=True)
    with pytest.raises(ValueError, match="source_sessions"):
        build_compact_summary_prompt(
            previous_state=empty_rolling_summary_state(),
            plan=plan,
        )


# ---------------------------------------------------------------------------
# 15. prompt 超过 model_max_input_tokens 抛 ValueError
# ---------------------------------------------------------------------------


def test_build_prompt_rejects_exceeds_input_limit():
    huge_summary = "z" * 50000
    prev_state = build_rolling_summary_state(
        summary=huge_summary,
        covered_session_ids=["old1"],
        source_session_count=1,
    )
    plan = make_plan(
        source_sessions=(make_compact_history_session(1, answer="x" * 50000),)
    )
    with pytest.raises(ValueError, match="model_max_input_tokens"):
        build_compact_summary_prompt(
            previous_state=prev_state,
            plan=plan,
            config=CompactSummaryConfig(
                model_max_input_tokens=1000,
                template_budget_tokens=100,
                previous_summary_budget_tokens=100,
                source_budget_tokens=500,
            ),
        )


# ---------------------------------------------------------------------------
# 16. normalize 去掉 code fence
# ---------------------------------------------------------------------------


def test_normalize_strips_code_fence():
    fenced = "```\n## 当前任务\nbody\n```"
    assert normalize_compact_summary_text(fenced) == "## 当前任务\nbody"


def test_normalize_strips_language_fence():
    fenced = "```markdown\n## 当前任务\nbody\n```"
    assert normalize_compact_summary_text(fenced) == "## 当前任务\nbody"


def test_normalize_no_fence_just_strips():
    assert normalize_compact_summary_text("  hello  ") == "hello"


# ---------------------------------------------------------------------------
# 17. validate 接受完整 headings
# ---------------------------------------------------------------------------


def test_validate_accepts_valid_summary():
    validation = validate_compact_summary_text(valid_summary())
    assert validation.summary == valid_summary()
    assert validation.headings == COMPACT_SUMMARY_REQUIRED_HEADINGS
    assert validation.estimate.tokens > 0


# ---------------------------------------------------------------------------
# 18. summary 缺 heading 抛 ValueError
# ---------------------------------------------------------------------------


def test_validate_rejects_missing_heading():
    summary = valid_summary().replace("## 卡点\n等待 summarizer prompt 审查。\n", "")
    with pytest.raises(ValueError, match="missing heading"):
        validate_compact_summary_text(summary)


# ---------------------------------------------------------------------------
# 19. headings 顺序错误抛 ValueError
# ---------------------------------------------------------------------------


def test_validate_rejects_wrong_heading_order():
    lines = valid_summary().split("\n")
    # 交换前两个 heading
    idx_task = lines.index("## 当前任务")
    idx_judge = lines.index("## 当前判断")
    lines[idx_task], lines[idx_judge] = lines[idx_judge], lines[idx_task]
    summary = "\n".join(lines)
    with pytest.raises(ValueError, match="order"):
        validate_compact_summary_text(summary)


# ---------------------------------------------------------------------------
# 20. summary 空抛 ValueError
# ---------------------------------------------------------------------------


def test_validate_rejects_empty_summary():
    with pytest.raises(ValueError, match="non-empty"):
        validate_compact_summary_text("   ")


# ---------------------------------------------------------------------------
# 21. summary 超过 target_summary_tokens 抛 ValueError
# ---------------------------------------------------------------------------


def test_validate_rejects_summary_exceeds_target_tokens():
    # 构造一个超长但保留 headings 的 summary
    long_body = "x" * 10000
    summary = (
        f"## 当前任务\n{long_body}\n"
        "## 当前判断\nx\n"
        "## 卡点\nx\n"
        "## 下一步检索指针\nx\n"
        "## 用户偏好\nx\n"
        "## 最近完成\nx\n"
    )
    with pytest.raises(ValueError, match="target_summary_tokens"):
        validate_compact_summary_text(
            summary,
            config=CompactSummaryConfig(target_summary_tokens=100),
        )


# ---------------------------------------------------------------------------
# 22. summary 含 data:image/ 抛 ValueError
# ---------------------------------------------------------------------------


def test_validate_rejects_data_image():
    summary = valid_summary() + "\n![img](data:image/png;base64,AAAA)"
    with pytest.raises(ValueError, match="data:image/"):
        validate_compact_summary_text(summary)


# ---------------------------------------------------------------------------
# 23. summary 含超长 base64-like 片段抛 ValueError
# ---------------------------------------------------------------------------


def test_validate_rejects_long_base64_fragment():
    b64 = "A" * 600
    summary = valid_summary() + f"\n{b64}"
    with pytest.raises(ValueError, match="base64"):
        validate_compact_summary_text(summary)


# ---------------------------------------------------------------------------
# 24. build_compact_success_state 合并 covered ids
# ---------------------------------------------------------------------------


def test_build_success_state_merges_covered_ids():
    prev_state = build_rolling_summary_state(
        summary="old summary",
        covered_session_ids=["s1", "s2"],
        source_session_count=2,
    )
    new_state = build_compact_success_state(
        previous_state=prev_state,
        summary=valid_summary(),
        source_session_ids=["s3", "s4"],
    )
    assert set(new_state.covered_session_ids) == {"s1", "s2", "s3", "s4"}
    assert new_state.source_session_count == 4


# ---------------------------------------------------------------------------
# 25. build_compact_success_state 去重并保持顺序
# ---------------------------------------------------------------------------


def test_build_success_state_dedup_preserves_order():
    prev_state = build_rolling_summary_state(
        summary="old",
        covered_session_ids=["s1", "s2"],
        source_session_count=2,
    )
    new_state = build_compact_success_state(
        previous_state=prev_state,
        summary=valid_summary(),
        source_session_ids=["s2", "s3"],
    )
    assert new_state.covered_session_ids == ("s1", "s2", "s3")


# ---------------------------------------------------------------------------
# 26. build_compact_success_state 设置 last_error=None
# ---------------------------------------------------------------------------


def test_build_success_state_sets_last_error_none():
    prev_state = RollingSummaryState(
        version=1,
        summary="old",
        covered_session_ids=("s1",),
        updated_at="2026-07-06T12:00:00+00:00",
        source_session_count=1,
        estimate=CompactEstimate(source="rough", tokens=1, chars=1),
        last_error={"type": "previous_error"},
    )
    new_state = build_compact_success_state(
        previous_state=prev_state,
        summary=valid_summary(),
        source_session_ids=["s2"],
    )
    assert new_state.last_error is None


# ---------------------------------------------------------------------------
# 27. compact_summary_prompt_to_dict 可 JSON 序列化
# ---------------------------------------------------------------------------


def test_prompt_to_dict_json_serializable():
    plan = make_plan(source_sessions=(make_compact_history_session(1),))
    prompt = build_compact_summary_prompt(
        previous_state=empty_rolling_summary_state(),
        plan=plan,
    )
    payload = compact_summary_prompt_to_dict(prompt)
    serialized = json.dumps(payload, ensure_ascii=False)
    assert isinstance(serialized, str)


# ---------------------------------------------------------------------------
# 28. dict helper 不共享 messages 可变引用
# ---------------------------------------------------------------------------


def test_prompt_to_dict_does_not_share_messages():
    plan = make_plan(source_sessions=(make_compact_history_session(1),))
    prompt = build_compact_summary_prompt(
        previous_state=empty_rolling_summary_state(),
        plan=plan,
    )
    payload = compact_summary_prompt_to_dict(prompt)
    payload["messages"][0]["content"] = "mutated"
    assert prompt.messages[0]["content"] != "mutated"


# ---------------------------------------------------------------------------
# 29. prompt builder 不修改输入
# ---------------------------------------------------------------------------


def test_build_prompt_does_not_modify_inputs():
    prev_state = build_rolling_summary_state(
        summary="previous",
        covered_session_ids=["s1"],
        source_session_count=1,
    )
    prev_snapshot = copy.deepcopy(prev_state)
    session = make_compact_history_session(2, answer="answer 2")
    plan = make_plan(source_sessions=(session,))
    plan_snapshot = copy.deepcopy(plan)

    build_compact_summary_prompt(previous_state=prev_state, plan=plan)

    assert prev_state == prev_snapshot
    assert plan == plan_snapshot


# ---------------------------------------------------------------------------
# 额外：render_compact_source_sessions 单独测试
# ---------------------------------------------------------------------------


def test_render_source_sessions_returns_text_and_ids():
    sessions = (
        make_compact_history_session(1),
        make_compact_history_session(2),
    )
    text, ids, tokens, actions = render_compact_source_sessions(
        sessions, config=CompactSummaryConfig()
    )
    assert "s1" in text
    assert "s2" in text
    assert ids == ("s1", "s2")
    assert tokens > 0
    assert actions == ()


def test_render_source_sessions_first_exceeds_budget_still_included():
    big_answer = "x" * 20000
    sessions = (make_compact_history_session(1, answer=big_answer),)
    text, ids, tokens, actions = render_compact_source_sessions(
        sessions, config=CompactSummaryConfig(source_budget_tokens=100)
    )
    # 第一条始终保留
    assert ids == ("s1",)
    assert "source_session_clipped" in actions


# ---------------------------------------------------------------------------
# 额外：build_compact_success_state 不接受非法 summary
# ---------------------------------------------------------------------------


def test_build_success_state_rejects_invalid_summary():
    prev_state = empty_rolling_summary_state()
    with pytest.raises(ValueError):
        build_compact_success_state(
            previous_state=prev_state,
            summary="invalid summary without headings",
            source_session_ids=["s1"],
        )

# ---------------------------------------------------------------------------
# 007-fix: Markdown H2 schema 校验
# ---------------------------------------------------------------------------


def test_extract_markdown_h2_headings_ignores_body_mentions_and_h3():
    summary = "\n".join([
        "## 当前任务",
        "正文提到 ## 当前判断，但它只是普通正文。",
        "### 当前判断",
        "## 当前判断",
    ])
    assert extract_markdown_h2_headings(summary) == ("## 当前任务", "## 当前判断")


def test_validate_rejects_body_mention_without_heading_line():
    summary = "\n".join([
        "## 当前任务",
        "正文提到 ## 当前判断，但没有独立标题行。",
        "## 卡点",
        "x",
        "## 下一步检索指针",
        "x",
        "## 用户偏好",
        "x",
        "## 最近完成",
        "x",
    ])
    with pytest.raises(ValueError, match="missing heading"):
        validate_compact_summary_text(summary)


def test_validate_rejects_duplicate_required_heading():
    summary = valid_summary() + "\n## 当前任务\n重复标题"
    with pytest.raises(ValueError, match="schema"):
        validate_compact_summary_text(summary)


def test_validate_accepts_heading_lines_with_outer_spaces():
    summary = valid_summary().replace("## 当前任务", "  ## 当前任务  ", 1)
    validation = validate_compact_summary_text(summary)
    assert validation.headings == COMPACT_SUMMARY_REQUIRED_HEADINGS


# ---------------------------------------------------------------------------
# 007-fix: action 只描述进入 prompt 的 source block
# ---------------------------------------------------------------------------


def test_render_source_does_not_mark_clipped_for_skipped_budget_session():
    sessions = (
        make_compact_history_session(1, answer="small"),
        make_compact_history_session(2, answer="x" * 20000),
    )
    _text, ids, _tokens, actions = render_compact_source_sessions(
        sessions,
        config=CompactSummaryConfig(source_budget_tokens=100),
    )
    assert ids == ("s1",)
    assert "source_budget_reached" in actions
    assert "source_session_clipped" not in actions
