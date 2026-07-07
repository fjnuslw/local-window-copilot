# 协作任务 009：Compact 触发接入与 Summary 注入

更新时间：2026-07-06

## 目标

009 把 008 的单次 executor 接入对话主链路，完成 compact MVP 的运行入口。

- 自动触发：每轮回答完成后，先写 `CHAT_HISTORY_KEY`，再写 FTS5，然后检查 compact。
- 手动触发：新增 `/api/assistant/compact` 调试接口，显式执行一次 compact。
- Summary 注入：下一轮构建 messages 时，把 rolling summary 注入到 profile 后、memory/context packet 前。
- Trace：记录 `context_summary.started`、`context_summary.succeeded`、`context_summary.failed`，并记录 skipped/locked 状态。
- 清理：清空对话历史时同步清理 rolling summary 与 compact lock。

## 设计规约

### 1. 自动触发合同

自动触发只处理 `status == "done"` 的历史 session。

执行位置：

```text
_stream_model_answer
  -> _append_history(session)
  -> _index_session_to_fts(session)
  -> maybe_compact_history(force=False, source="auto", session=session)
```

规则：

- compact 不影响当前轮已经生成的回答。
- compact 成功后，下轮 `_build_answer_context()` 读取新 summary。
- compact 未触发、锁占用、模型错误都不阻断当前 session 完成。
- 自动触发受 `compact_enabled` 和 `compact_auto_enabled` 控制。

### 2. 手动触发合同

新增接口：

```text
POST /api/assistant/compact
```

规则：

- 等价于 `maybe_compact_history(force=True, source="manual")`。
- 返回 compact execution result 的 JSON 化结构。
- 无 source sessions 时返回 skipped result。
- 不启动后台线程。

### 3. Summary 注入合同

`build_chat_messages` 新增 `compact_summary` 参数。

顺序：

```text
system: BASE_PREFIX
user: profile_packet
user: compact_state
user: memory/context packet
user/assistant: raw tail
user: current question
```

summary packet 格式：

```text
[compact_state]
以下是较早对话的工作状态指针。逐字历史通过 memory.search(query) 检索。

{summary}
```

规则：

- summary 为空时不注入。
- 不改变 system/profile 字节。
- `inspect_context` 中 summary segment 标记为 `kind="summary"`、`label="rolling_summary"`。

### 4. Trace 合同

自动与手动都写结构化 trace。payload 只放精简账本：

- status / trigger / attempted / compacted
- source_session_ids / tail_session_ids / uncovered_session_ids
- summary_tokens / covered_session_count
- metrics 状态
- error_type / error_message

不把完整 prompt messages 写入 trace。

### 5. 配置合同

新增 settings 字段，默认值沿用总 spec：

```python
compact_enabled: bool = True
compact_auto_enabled: bool = True
compact_raw_tail_turns: int = 2
compact_batch_session_limit: int = 12
compact_source_budget_tokens: int = 18000
compact_uncovered_session_threshold: int = 6
compact_history_trigger_tokens: int = 24000
compact_model_max_input_tokens: int = 24000
compact_model_max_output_tokens: int = 1600
compact_template_budget_tokens: int = 2000
compact_previous_summary_budget_tokens: int = 2000
compact_target_summary_tokens: int = 1200
compact_timeout_seconds: int = 90
```

## 测试要求

新增或扩展测试覆盖：

1. rolling summary 注入 messages，位置在 profile 后。
2. context budget hints 把 compact summary 标为 summary segment。
3. 自动 compact 在 session done 后触发，写 summary、metrics、trace。
4. compact 模型错误不影响 session done，trace 写 failed。
5. 手动 API 调用 `compact_history(force=True)`。
6. clear history 同步清理 summary 与 lock。

## 完成判定

- 001~009 相关测试全部通过。
- 文档总 spec 同步 009 状态。
- 关键词扫描不出现禁用表达。
- 009 不引入后台线程、不注册 Agent tool、不改变当前轮回答内容。
## 当前结果

变更完成：

- `Settings` 新增 compact 配置字段。
- `build_chat_messages` 支持 `compact_summary`，注入顺序为 system/profile/compact_state/context/tail/question。
- `build_chat_segment_hints` 支持 summary segment。
- `ChatAgent` 新增自动 compact、手动 compact、trace 精简账本。
- `_stream_model_answer` 在历史与 FTS5 写入后自动检查 compact。
- `/api/assistant/compact` 已接入。
- `clear_history()` 同步清理 rolling summary 与 compact lock。

测试命令：

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider tests\test_context_budget.py tests\test_context_assembler.py tests\test_context_budget_inspect.py tests\test_context_summary_state.py tests\test_context_compact_planner.py tests\test_context_compact_summarizer_prompt.py tests\test_context_compact_executor.py tests\test_assistant_chat_service.py tests\test_assistant_state_api.py -q -k "not test_user_image_is_attached_to_direct_chat"
```

结果：`205 passed, 1 deselected, 1 warning`。

说明：完整命令包含旧图片上传测试时，pytest 在当前沙箱中无法访问 `C:\Users\xiongsir\AppData\Local\Temp\pytest-of-xiongsir`，该错误发生在 `tmp_path` fixture setup 阶段。