# 011：Tool Result 预算化

## 目标

让 agent 工具结果进入模型 messages 前经过本地 rough token 预算，避免一次 `memory.search` 或后续工具结果把上下文窗口挤满。

011 只处理工具结果消息。召回排序、工具注册、compact、summary prompt、请求前总量拦截都保持现状。

## 背景

当前 `_execute_and_append_tools()` 在拿到 `AgentToolResult.content` 后直接追加：

```python
messages.append({
    "role": "tool",
    "tool_call_id": result.call_id or "",
    "name": result.model_name or result.name.replace(".", "_"),
    "content": result.content,
})
```

`memory.search` 的结果可能包含窗口观察、OCR 文本、UI elements、历史会话摘要等大字段。长期对话中，工具结果可能成为比 raw history 更大的上下文压力。

## 核心思想

- 完整工具原始结果仍留在工具执行阶段和 trace 中，模型输入拿预算后的结果文本。
- 预算器是纯函数：输入字符串、预算、估算器；输出预算后的字符串与报告。
- 模型收到的内容始终是合法 JSON 字符串。
- 预算器只做长度治理和可读说明，保持 source、record_id、时间、窗口名等指针信息。
- 截短说明必须告诉模型继续用更聚焦的 `memory.search(query)` 获取细节。

## 改动范围

允许修改：

- `backend/app/core/config.py`
- `backend/app/services/context_budget.py`
- `backend/app/services/assistant_chat.py`
- `backend/tests/test_context_budget.py` 或新增 `backend/tests/test_tool_result_budget.py`
- `backend/tests/test_assistant_chat_service.py`
- `docs/co-work/context_management_refactor_spec_zh.md`

保持稳定：

- `backend/app/services/agent_tools.py` 的召回逻辑
- `memory.search` 参数 schema
- compact state/executor/prompt
- `vision_model_client.py`
- 请求前总量拦截逻辑

## 配置项

新增到 `Settings`：

```python
tool_result_budget_tokens: int = 8000
tool_result_item_budget_tokens: int = 3000
```

规则：

- 单次工具调用内容上限：`tool_result_item_budget_tokens`。
- 一轮工具执行合计上限：`tool_result_budget_tokens`。
- 每个 tool call 都必须追加一条 tool message；合计预算用尽时追加最小 JSON 说明。
- 配置值小于 1 时按 1 处理。

## 预算器 API

在 `context_budget.py` 新增：

```python
@dataclass(frozen=True)
class ToolResultBudgetReport:
    tool_name: str
    call_id: str
    original_tokens: int
    final_tokens: int
    item_limit_tokens: int
    remaining_budget_tokens: int
    truncated: bool
    actions: tuple[str, ...]

@dataclass(frozen=True)
class ToolResultBudgetedContent:
    content: str
    report: ToolResultBudgetReport


def budget_tool_result_content(
    content: str,
    *,
    tool_name: str,
    call_id: str | None = None,
    item_limit_tokens: int = 3000,
    remaining_budget_tokens: int = 8000,
    estimator: ContextTokenEstimator | None = None,
) -> ToolResultBudgetedContent:
    ...
```

输出规则：

- `content` 必须是字符串，其他类型抛 `ValueError`。
- 原始 tokens 小于等于有效预算时原样返回。
- 超预算时返回 JSON 字符串：

```json
{
  "tool_result_budget": {
    "tool_name": "memory.search",
    "call_id": "call_1",
    "truncated": true,
    "original_tokens": 12000,
    "budget_tokens": 3000,
    "message": "Result shortened before model input. Use memory.search with a narrower query for more detail."
  },
  "content_preview": "...",
  "original_json_type": "dict"
}
```

- 若原始内容是 JSON，`original_json_type` 写 `dict`、`list` 或实际类型名。
- 若原始内容解析失败，`original_json_type` 写 `text`。
- `content_preview` 使用 head/tail 形式，格式固定：

```text
<head>
[TRUNCATED middle_chars=<n>]
<tail>
```

- 预算器需要循环收缩 preview，直到 rough tokens 小于等于有效预算，或达到最小可读说明。
- 最小可读说明仍可能占用少量 token；当预算极小，优先保证 JSON 合法和 tool_call 对齐。

## Assistant 接入

在 `_execute_and_append_tools()` 中：

1. 读取 settings。
2. 初始化 `remaining_tool_budget = settings.tool_result_budget_tokens`。
3. 对每个 `AgentToolResult` 调用 `budget_tool_result_content()`。
4. 使用预算后的 `content` 写入 tool message。
5. 根据 report 更新 remaining。
6. trace 新增阶段：`tool_result_budget`。

trace payload：

```json
{
  "reports": [
    {
      "tool_name": "memory.search",
      "call_id": "call_1",
      "original_tokens": 12000,
      "final_tokens": 2950,
      "item_limit_tokens": 3000,
      "remaining_budget_tokens": 8000,
      "truncated": true,
      "actions": ["tool_result_truncated"]
    }
  ],
  "total_final_tokens": 2950,
  "truncated_count": 1
}
```

`tool_results` trace 保持现状，用于开发审计；`tool_result_budget` trace 给 WebUI 与排查使用。

## 测试要求

纯函数测试：

1. 小结果原样返回，`truncated=false`。
2. 大文本结果返回合法 JSON，包含 `tool_result_budget` 与 `content_preview`。
3. 大 JSON 结果返回合法 JSON，`original_json_type=dict`。
4. `remaining_budget_tokens` 小于 item limit 时使用剩余额度。
5. 预算极小时仍返回合法 JSON。
6. 非字符串 content 抛 `ValueError`。

集成测试：

1. probe 工具调用路径中，tool message 使用预算后的 content。
2. stream 工具调用路径中，第二轮 stream messages 含预算后的 tool content。
3. trace 中出现 `tool_result_budget`，并包含 truncated 报告。
4. 设置较大预算时 tool content 原样进入 messages。

## 验证命令

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider tests\test_tool_result_budget.py tests\test_assistant_chat_service.py -q -k "not test_user_image_is_attached_to_direct_chat"
```

主线回归：

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider tests\test_context_budget.py tests\test_context_assembler.py tests\test_context_budget_inspect.py tests\test_context_summary_state.py tests\test_context_compact_planner.py tests\test_context_compact_summarizer_prompt.py tests\test_context_compact_executor.py tests\test_tool_result_budget.py tests\test_assistant_chat_service.py tests\test_assistant_state_api.py -q -k "not test_user_image_is_attached_to_direct_chat"
```

## 完成判定

- tool message 内容进入模型前受单项与合计预算控制。
- 预算后内容为合法 JSON。
- trace 可看到原始 tokens、最终 tokens、是否截短、actions。
- 011 测试与主线回归通过。
- 总计划书更新 011 状态。