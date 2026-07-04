from __future__ import annotations

import pytest

from app.services.agent_orchestrator import _normalize_tool_name
from app.services.agent_tools import AGENT_TOOL_NAMES


ALLOWED_TOOL_NAMES = ("none", *AGENT_TOOL_NAMES)


def test_planner_parser_accepts_prefixed_tool_step() -> None:
    assert _normalize_tool_name("current_step screen.look", ALLOWED_TOOL_NAMES) == "screen.look"


def test_planner_parser_accepts_json_tool_payload() -> None:
    assert _normalize_tool_name('{"tool":"memory.search"}', ALLOWED_TOOL_NAMES) == "memory.search"


def test_planner_parser_rejects_ambiguous_tool_names() -> None:
    with pytest.raises(ValueError, match="multiple tool names"):
        _normalize_tool_name("screen.look then memory.search", ALLOWED_TOOL_NAMES)