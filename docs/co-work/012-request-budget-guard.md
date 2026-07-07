# 012：请求前预算拦截与最终收口

## 目标

把 004 的预算展示升级为真实运行边界：每次调用模型前都检查 messages 的 rough input tokens，超过 input_limit 时停止本次模型请求，记录 trace，并给用户可读错误。

012 是上下文管理改造的最后收口切片。它不改 compact、tool result 预算器、工具召回排名和 WebUI 面板，只把已有预算账本接到模型请求前。

## 背景

001-004 已完成 token 估算、segment 账本和 `inspect_context` 展示。

005-010 已完成 compact state、planner、summary、执行、自动/手动触发、summary 注入和 WebUI 状态面板。

011 已完成 tool result 单项与合计预算治理。

剩余风险是：预算报告已经能识别 `over_limit`，但正常对话链路仍可能继续发请求。012 需要把这个边界变成真实拦截。

## 拦截位置

在 `ChatAgent._stream_model_answer()` 中检查：

1. probe 调用前：`VisionModelClient.complete_chat_response(...)` 之前。
2. 每轮 stream 调用前：`VisionModelClient.stream_chat(...)` 之前。
3. 工具结果追加后进入下一轮 stream 时，会自然再次检查。

不需要在 `_execute_and_append_tools()` 内拦截；工具结果预算由 011 负责，最终 messages 总量由本任务在下一次模型请求前检查。

## 行为规则

- 使用已有 `build_context_budget_preview(...)` 生成预算 payload。
- `over_limit=false` 时写 trace：`context_budget.checked`。
- `over_limit=true` 时写 trace：`context_budget.over_limit`，随后抛出专用异常。
- 专用异常被 `_answer()` 现有错误链路接住，session 进入 `error`。
- 错误文本必须包含：估算 tokens、input_limit、建议动作。
- 超限时不调用 provider，不调用 stream，不执行后续模型请求。
- 已经保存的 RuntimeStore、FTS5、compact_state 保持现状。

## trace payload

`context_budget.checked` 与 `context_budget.over_limit` 使用同一结构：

```json
{
  "phase": "probe",
  "stream_round": null,
  "messages_count": 3,
  "ctx_size": 256000,
  "input_limit": 215040,
  "estimated_input_tokens": 12000,
  "input_usage_percent": 5.6,
  "over_limit": false,
  "totals": {
    "segments": 3,
    "text_tokens": 11000,
    "json_tokens": 100,
    "image_tokens": 0,
    "overhead_tokens": 30,
    "tool_call_tokens": 0
  },
  "actions": []
}
```

限制：

- trace 不放完整 messages。
- trace 可以放 segment 报告摘要；默认只放 totals 和 actions。
- 需要排查具体 segment 时继续用 `/api/assistant/context-preview`。

## 错误文案

推荐格式：

```text
上下文预算超限：本次请求约 {estimated_input_tokens} tokens，输入上限 {input_limit}。请缩小问题范围、清理历史，或运行 compact 后重试。
```

## 实现范围

允许修改：

- `backend/app/services/assistant_chat.py`
- `backend/app/services/context_budget.py` 中过时注释
- `backend/tests/test_assistant_chat_service.py`
- `backend/tests/test_context_budget_inspect.py` 如需更新注释
- `docs/co-work/context_management_refactor_spec_zh.md`

保持稳定：

- `vision_model_client.py`
- `context_summary.py`
- `agent_tools.py`
- WebUI compact 面板
- tool result 预算器行为

## 测试要求

1. `_ensure_messages_within_budget()` 在预算内返回 payload，并写 `context_budget.checked` trace。
2. `_ensure_messages_within_budget()` 超限时抛专用异常，并写 `context_budget.over_limit` trace。
3. probe 前超限时，`complete_chat_response()` 和 `stream_chat()` 均未被调用，session 状态为 error。
4. stream 前超限时，`stream_chat()` 未被调用，session 状态为 error。
5. 工具结果追加后造成下一轮 stream 超限时，第二轮 stream 未被调用，session 状态为 error。
6. 正常路径已有测试继续通过。

## 验证命令

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider tests\test_assistant_chat_service.py -q -k "not test_user_image_is_attached_to_direct_chat"
```

主线回归：

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider tests\test_context_budget.py tests\test_context_assembler.py tests\test_context_budget_inspect.py tests\test_context_summary_state.py tests\test_context_compact_planner.py tests\test_context_compact_summarizer_prompt.py tests\test_context_compact_executor.py tests\test_tool_result_budget.py tests\test_assistant_chat_service.py tests\test_assistant_state_api.py -q -k "not test_user_image_is_attached_to_direct_chat"
```

## 完成判定

- 任意模型请求前都有预算检查。
- 超限时没有 provider 调用。
- session error 和 trace 都能解释超限原因。
- 001-012 主线回归通过。
- 总计划书标记上下文管理主线完成。