# Trae 协作任务 004：Token 预算接入 inspect_context

更新时间：2026-07-06

执行对象：Trae Work，模型建议 GLM-5.2

审查对象：Codex

## 核心思想

本任务把 token 预算阶段收口：`inspect_context` 开始展示 003 的预算报告，旧的 `chars // 2` 展示估算停止使用。

- 本地 MiniCPM / OpenAI-compatible 调用链保持单一路径。
- 004 只影响上下文检查和状态展示。
- 真实模型请求 messages 保持原样。
- 004 只展示 `over_limit`，本轮不执行请求拦截。
- segment 归类使用显式 hints 修正纯 index 推断边界。
- compact、工具结果限量、请求前拦截进入后续任务。

## 背景文档

先阅读：

- `docs/co-work/context_management_refactor_spec_zh.md`
- 重点看 §2.1、§5、§6、§9、§10、§H
- `docs/co-work/003-trae-context-assembler-report.md`

当前代码边界：

- `assistant_chat.inspect_context(...)` 当前使用 `message_chars // 2` 估算。
- `context_status(...)` 从 `inspect_context(...).usage` 读取 `estimated_tokens`、`usage_percent` 等字段。
- `ContextAssembler` 已能生成 `ContextBudgetReport`。

## 改动范围

允许修改：

```text
backend/app/services/context_budget.py
backend/app/services/assistant_chat.py
backend/tests/test_context_budget.py
backend/tests/test_context_assembler.py
```

可以新增：

```text
backend/tests/test_context_budget_inspect.py
```

本任务不修改：

```text
backend/app/services/vision_model_client.py
backend/app/core/config.py
frontend / desktop app
```

## 实现要求

### 1. 输入预算计算

在 `context_budget.py` 中新增纯函数：

```python
CONTEXT_BUDGET_SAFETY_TOKENS = 8192

def calculate_context_input_limit(
    *,
    ctx_size: int,
    answer_max_tokens: int,
    safety_tokens: int = CONTEXT_BUDGET_SAFETY_TOKENS,
) -> int:
    ...
```

规则：

```text
input_limit = ctx_size - answer_max_tokens - safety_tokens
```

边界：

- `ctx_size` 至少按 1 处理。
- `answer_max_tokens` 和 `safety_tokens` 小于 0 时按 0 处理。
- 返回值至少为 1。

004 不新增配置项；使用已有 `settings.minicpm_ctx_size` 和 `settings.answer_max_tokens`。

### 2. 显式 segment hints

在 `context_budget.py` 中新增：

```python
@dataclass(frozen=True)
class ContextSegmentHint:
    kind: str
    label: str
    required: bool
    priority: int
    metadata: dict[str, Any] = field(default_factory=dict)
```

扩展：

```python
ContextAssembler.segments_from_messages(
    messages: list[dict[str, Any]],
    hints: dict[int, ContextSegmentHint] | None = None,
) -> list[ContextSegment]
```

规则：

- 有 hint 的 index 使用 hint 的 `kind/label/required/priority`。
- segment role 仍来自原 message。
- metadata 合并 `{"index": index}` 与 hint.metadata。
- 无 hint 的 index 沿用 003 的轻量推断。
- 输入 messages 不被原地修改。

新增纯函数：

```python
def build_chat_segment_hints(
    messages: list[dict[str, Any]],
    *,
    has_profile_packet: bool,
    has_context_packet: bool,
) -> dict[int, ContextSegmentHint]:
    ...
```

规则：

- index 0 且 role=system 标记 `system/base_prefix/required=True/priority=100`。
- 当 `has_profile_packet=True` 时，按当前 `build_chat_messages` 顺序标记 profile。
- 当 `has_context_packet=True` 时，按当前 `build_chat_messages` 顺序标记 context packet 为 `memory/context_packet`。
- 最后一条 role=user 标记 `question/current_question/required=True/priority=95`。
- profile 与 context packet 的 index 由上述顺序推进，不靠正文内容匹配。
- 当 context packet 为空时，历史 user 不会被 hint 标记为 memory。

### 3. inspect_context 预算 helper

在 `assistant_chat.py` 中新增可单测 helper，建议模块级函数：

```python
def build_context_budget_preview(
    *,
    messages: list[dict[str, Any]],
    profile_packet: str,
    context_packet: str,
    ctx_size: int,
    answer_max_tokens: int,
) -> dict[str, Any]:
    ...
```

职责：

- 调用 `calculate_context_input_limit(...)`。
- 调用 `build_chat_segment_hints(...)`。
- 调用 `ContextAssembler.segments_from_messages(..., hints=...)`。
- 调用 `ContextAssembler.build_report(...)`。
- 调用 `budget_report_to_dict(report)`。
- 在输出 dict 中补充：

```python
{
    "estimate_source": "rough",
    "output_reserve": answer_max_tokens,
    "safety_tokens": CONTEXT_BUDGET_SAFETY_TOKENS,
    "input_usage_percent": ...,
}
```

`input_usage_percent`：

```text
round(estimated_input_tokens / input_limit * 100, 1)
```

### 4. inspect_context 接入

在 `inspect_context(...)` 中：

- 使用 `build_context_budget_preview(...)` 生成 `context_budget`。
- `message_chars` 改用 `context_budget["estimated_chars"]`。
- `estimated_tokens` 改用 `context_budget["estimated_input_tokens"]`。
- `ctx_size` 继续来自 `settings.minicpm_ctx_size`。
- `usage_percent` 继续表示 `estimated_tokens / ctx_size * 100`，保持旧 UI 字段含义。
- `usage` 中新增：

```python
{
    "estimate_source": "rough",
    "input_limit": ...,
    "input_usage_percent": ...,
    "over_limit": ...,
}
```

- `inspect_context(...)` 返回值新增顶层字段：

```python
"context_budget": context_budget
```

兼容要求：

- `usage["estimated_tokens"]`、`usage["total_chars"]`、`usage["ctx_size"]`、`usage["usage_percent"]` 继续保留。
- `context_status(...)` 可继续读取旧字段。
- 不改变 `context.messages`。
- 不改变真实模型请求。

## 测试要求

新增或扩展测试，覆盖：

1. `calculate_context_input_limit(ctx_size=256000, answer_max_tokens=32768)` 返回 `215040`。
2. 输入预算函数处理负数参数，返回值至少为 1。
3. `segments_from_messages(..., hints=...)` 使用 hint 覆盖默认推断。
4. hint metadata 与原始 index 合并。
5. `build_chat_segment_hints(..., has_context_packet=False)` 时，index 2 的历史 user 不会被标记为 memory。
6. `build_chat_segment_hints(..., has_context_packet=True)` 时，context packet 被标记为 memory/context_packet。
7. `build_context_budget_preview(...)` 返回 `estimate_source/input_limit/output_reserve/safety_tokens/input_usage_percent/segments/totals`。
8. `build_context_budget_preview(...)` 的 `estimated_input_tokens` 与 segment token 之和一致。
9. `inspect_context(...)` 返回顶层 `context_budget`。
10. `inspect_context(...).usage["estimated_tokens"]` 等于 `context_budget["estimated_input_tokens"]`。
11. `inspect_context(...).usage["total_chars"]` 等于 `context_budget["estimated_chars"]`。
12. `context_status(...)` 继续返回 `estimated_tokens/usage_percent/remaining_percent`。

如果 `inspect_context` 的完整测试依赖较多，可优先测试 `build_context_budget_preview(...)`，并为 `inspect_context` 写一个最小 fake agent 或 monkeypatch 测试。交付说明中写清测试覆盖边界。

## 质量约束

- 只使用标准库。
- 不引入 tokenizer、transformers、tiktoken、网络依赖。
- 不改模型请求内容。
- 不启动模型服务。
- 不新增 provider 计数、模型切换、提示词替换等额外运行路径。
- 不执行 over-limit 拦截。
- 注释只解释非显然规则。

## 建议验证命令

```powershell
cd D:\AI_Workspace\window\backend
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider --basetemp=..\test-tmp\pytest tests\test_context_budget.py tests\test_context_assembler.py tests\test_context_budget_inspect.py -v
```

如果未新增 `test_context_budget_inspect.py`：

```powershell
cd D:\AI_Workspace\window\backend
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider --basetemp=..\test-tmp\pytest tests\test_context_budget.py tests\test_context_assembler.py -v
```

文案检查：按项目禁用词表检查本任务涉及文件。

期望：测试通过；文案检查无命中。

## 交付格式

Trae 完成后请回复：

```text
变更文件：
- ...

核心逻辑：
- ...

测试：
- 命令：...
- 结果：...

需要 Codex 审查的点：
- ...
```

## Codex 审查重点

- `chars // 2` 展示估算是否已停止使用。
- 旧 UI 字段是否保持兼容。
- `context_budget` 是否完整且可 JSON 序列化。
- explicit hints 是否修正 context packet 为空时的 index 边界。
- `over_limit` 是否只展示，没有改变真实请求。
