from __future__ import annotations

import json

import pytest

from app.services.context_budget import (
    ContextTokenEstimator,
    budget_tool_result_content,
)


def test_small_tool_result_returns_original_content() -> None:
    content = '{"query":"q","results":[{"record_id":"r1","content":{"text":"short"}}]}'

    budgeted = budget_tool_result_content(
        content,
        tool_name="memory.search",
        call_id="call_1",
        item_limit_tokens=3000,
        remaining_budget_tokens=8000,
    )

    assert budgeted.content == content
    assert budgeted.report.truncated is False
    assert budgeted.report.original_tokens == budgeted.report.final_tokens
    assert budgeted.report.actions == ()


def test_large_text_tool_result_returns_valid_json_preview() -> None:
    content = "A" * 20000

    budgeted = budget_tool_result_content(
        content,
        tool_name="memory.search",
        call_id="call_2",
        item_limit_tokens=300,
        remaining_budget_tokens=300,
    )
    payload = json.loads(budgeted.content)

    assert budgeted.report.truncated is True
    assert payload["tool_result_budget"]["tool_name"] == "memory.search"
    assert payload["tool_result_budget"]["call_id"] == "call_2"
    assert payload["original_json_type"] == "text"
    assert "content_preview" in payload
    assert "TRUNCATED" in payload["content_preview"]


def test_large_json_tool_result_records_json_type() -> None:
    content = json.dumps(
        {
            "query": "当前窗口",
            "results": [
                {
                    "source": "window:summaries",
                    "record_id": "r1",
                    "content": {"visible_text": ["长文本" * 2000]},
                }
            ],
        },
        ensure_ascii=False,
    )

    budgeted = budget_tool_result_content(
        content,
        tool_name="memory.search",
        call_id="call_3",
        item_limit_tokens=400,
        remaining_budget_tokens=400,
    )
    payload = json.loads(budgeted.content)

    assert payload["original_json_type"] == "dict"
    assert payload["tool_result_budget"]["truncated"] is True
    assert budgeted.report.original_tokens > budgeted.report.final_tokens


def test_remaining_budget_limits_effective_budget() -> None:
    content = "abc " * 5000

    budgeted = budget_tool_result_content(
        content,
        tool_name="memory.search",
        item_limit_tokens=1000,
        remaining_budget_tokens=120,
    )

    assert budgeted.report.remaining_budget_tokens == 120
    assert "tool_result_total_budget_limited" in budgeted.report.actions


def test_tiny_budget_still_returns_json() -> None:
    content = "长文本" * 10000

    budgeted = budget_tool_result_content(
        content,
        tool_name="memory.search",
        item_limit_tokens=1,
        remaining_budget_tokens=1,
    )
    payload = json.loads(budgeted.content)

    assert payload["tool_result_budget"]["budget_tokens"] == 1
    assert budgeted.report.truncated is True
    assert "tool_result_minimal_notice" in budgeted.report.actions


def test_non_string_tool_result_raises() -> None:
    with pytest.raises(ValueError, match="content must be str"):
        budget_tool_result_content(
            {"content": "x"},  # type: ignore[arg-type]
            tool_name="memory.search",
        )


def test_budgeted_content_can_fit_normal_budget() -> None:
    content = json.dumps({"results": [{"content": "x" * 12000}]})
    estimator = ContextTokenEstimator()

    budgeted = budget_tool_result_content(
        content,
        tool_name="memory.search",
        item_limit_tokens=800,
        remaining_budget_tokens=800,
        estimator=estimator,
    )

    assert estimator.estimate_text(budgeted.content).tokens <= 800