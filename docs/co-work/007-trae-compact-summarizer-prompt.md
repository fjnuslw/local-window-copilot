# Trae 协作任务 007：CompactSummarizer Prompt 与 State Builder

更新时间：2026-07-06

执行对象：Trae Work，模型建议 GLM-5.2

审查对象：Codex

## 核心思想

007 进入 CompactSummarizer 的第一段，只做可测试、可审计、无外部副作用的部分。

- 007 构建 compact summary prompt，使用总 spec §B 的 Minimal 指针式 summary 模板。
- 007 校验模型产出的 summary 文本，保证结构、长度和安全边界。
- 007 根据 previous summary state 与本批 source session ids 构造新的 `RollingSummaryState`。
- 007 禁止调用真实模型，禁止写 RuntimeStore，禁止释放 lock，禁止接入 `_append_history()`。
- 007 禁止修改聊天 messages 装配链路，禁止改 `vision_model_client.py`。
- 真实模型调用、lock、metrics、自动/手动触发执行器放到 008。

完成 007 后，008 可以直接把 prompt builder 接到注入式 model client，再走 `CompactStateStore` 提交。

## 背景文档

先阅读：

- `docs/co-work/context_management_refactor_spec_zh.md`
- 重点看 §A、§B、§C、§D、§G、§H
- `docs/co-work/005-trae-compact-state-store.md`
- `docs/co-work/006-trae-compact-planner.md`

当前代码事实：

- `backend/app/services/context_summary.py` 已有 `RollingSummaryState`、`build_rolling_summary_state(...)`、`CompactPlan`、`CompactHistorySession`、`estimate_compact_session_tokens(...)`。
- `CompactPlan.source_sessions` 已按 oldest-first 返回。
- `CompactPlan.tail_sessions` 已按 newest-first 返回。
- `VisionModelClient.complete_chat(...)` 已存在，但 007 禁止导入和调用。

## 改动范围

允许修改：

```text
backend/app/services/context_summary.py
```

允许新增：

```text
backend/tests/test_context_compact_summarizer_prompt.py
```

本任务禁止修改：

```text
backend/app/services/assistant_chat.py
backend/app/services/vision_model_client.py
backend/app/core/config.py
backend/app/api/routes
frontend / desktop app
```

本任务禁止新增：

```text
真实模型调用
RuntimeStore 写入
lock acquire/release
metrics 写入
后台任务
WebUI 接口
messages 注入
```

## 模块位置

继续使用：

```text
backend/app/services/context_summary.py
```

建议在 compact planner 分区后新增：

```python
# ---------------------------------------------------------------------------
# compact summarizer prompt
# ---------------------------------------------------------------------------
```

007 新增代码只依赖：

```text
标准库
ContextTokenEstimator
005/006 已有 dataclass 与 helper
```

## 常量

新增 required headings：

```python
COMPACT_SUMMARY_REQUIRED_HEADINGS = (
    "## 当前任务",
    "## 当前判断",
    "## 卡点",
    "## 下一步检索指针",
    "## 用户偏好",
    "## 最近完成",
)
```

新增 system prompt 常量或渲染函数均可。内容要求：

- 使用当前用户语言写 summary。
- 使用 required headings，保持顺序。
- 只记录能帮助下一轮继续工作的事实。
- 保留 session_id、record_id、文件路径、窗口标题、错误文本。
- 敏感值写为 `[REDACTED]`。
- 输出 Markdown 正文。
- 禁止输出解释、寒暄、代码块包裹。
- 禁止写入 base64、data URL、完整图片内容。

## 数据结构

全部使用 `@dataclass(frozen=True)`。

### CompactSummaryConfig

```python
@dataclass(frozen=True)
class CompactSummaryConfig:
    model_max_input_tokens: int = 24000
    model_max_output_tokens: int = 1600
    source_budget_tokens: int = 18000
    template_budget_tokens: int = 2000
    previous_summary_budget_tokens: int = 2000
    target_summary_tokens: int = 1200
    session_answer_head_chars: int = 4000
    session_answer_tail_chars: int = 2000
```

校验规则：

- 所有字段必须是 int，bool 拒绝。
- 所有字段必须 `>= 1`。
- `model_max_input_tokens` 必须大于 `template_budget_tokens + previous_summary_budget_tokens`。
- `source_budget_tokens` 必须小于 `model_max_input_tokens`。

新增：

```python
def validate_compact_summary_config(config: CompactSummaryConfig) -> CompactSummaryConfig:
    ...
```

### CompactSummaryPrompt

```python
@dataclass(frozen=True)
class CompactSummaryPrompt:
    messages: tuple[dict[str, str], ...]
    source_session_ids: tuple[str, ...]
    estimated_input_tokens: int
    estimated_source_tokens: int
    estimated_previous_summary_tokens: int
    estimated_template_tokens: int
    actions: tuple[str, ...]
```

规则：

- `messages` 固定两条：system + user。
- `source_session_ids` 只包含实际进入 prompt 的 source sessions。
- `actions` 使用 snake_case，例如 `previous_summary_clipped`、`source_session_clipped`、`source_budget_reached`。

### CompactSummaryValidation

```python
@dataclass(frozen=True)
class CompactSummaryValidation:
    summary: str
    estimate: CompactEstimate
    headings: tuple[str, ...]
```

用途：保存校验后的 summary 与 rough estimate。

## prompt 构建函数

新增主函数：

```python
def build_compact_summary_prompt(
    *,
    previous_state: RollingSummaryState,
    plan: CompactPlan,
    config: CompactSummaryConfig | None = None,
    estimator: ContextTokenEstimator | None = None,
) -> CompactSummaryPrompt:
    ...
```

处理规则：

1. 校验 config；`None` 时使用 `CompactSummaryConfig()`。
2. 要求 `plan.should_compact is True`。
3. 要求 `plan.source_sessions` 非空。
4. previous summary 进入 prompt 前按 `previous_summary_budget_tokens` 收缩。
5. source sessions 使用 `plan.source_sessions` 的 oldest-first 顺序。
6. source block 总量受 `source_budget_tokens` 限制。
7. prompt 总量受 `model_max_input_tokens` 限制。
8. 超出 input limit 时抛 `ValueError`，错误消息包含 `model_max_input_tokens`。
9. 不修改 `previous_state` 和 `plan`。

### source session 渲染

新增 helper：

```python
def render_compact_source_sessions(
    sessions: tuple[CompactHistorySession, ...],
    *,
    config: CompactSummaryConfig,
    estimator: ContextTokenEstimator | None = None,
) -> tuple[str, tuple[str, ...], int, tuple[str, ...]]:
    ...
```

返回：

```text
(source_text, included_session_ids, estimated_source_tokens, actions)
```

每个 session 推荐格式：

```text
### session_id: <id>
created_at: <created_at>

[question]
<question>

[answer]
<answer>
```

渲染规则：

- 保留 session_id 与 created_at。
- question 优先完整保留。
- answer 超长时使用 head/tail 收缩。
- 收缩标记使用 `[TRUNCATED middle_chars=<n>]`。
- 单条 source session 很大时，保留这一条并收缩 answer。
- 多条 source sessions 超出 `source_budget_tokens` 时，从 oldest-first 顺序开始保留可容纳的连续前缀，剩余 session ids 不进入 `included_session_ids`。
- 保持无跳洞选择。

### 文本收缩 helper

新增 helper：

```python
def compact_text_head_tail(text: str, *, head_chars: int, tail_chars: int) -> str:
    ...
```

规则：

- 输入必须是 str。
- 文本长度小于等于 `head_chars + tail_chars` 时原样返回。
- 否则返回 head + marker + tail。
- marker 记录被省略字符数。
- 不修改输入。

## summary 输出校验

新增：

```python
def normalize_compact_summary_text(raw: str) -> str:
    ...
```

规则：

- `raw` 必须是 str。
- strip 首尾空白。
- 如果整个输出被单层 triple backticks 包裹，去掉最外层 fence。
- 返回 strip 后文本。

新增：

```python
def validate_compact_summary_text(
    raw: str,
    *,
    config: CompactSummaryConfig | None = None,
    estimator: ContextTokenEstimator | None = None,
) -> CompactSummaryValidation:
    ...
```

校验规则：

- summary 非空。
- required headings 全部存在，顺序一致。
- summary rough tokens 小于等于 `target_summary_tokens`。
- summary 不包含 `data:image/`。
- summary 不包含超过 500 字符的连续 base64-like 片段。base64-like 字符集：ASCII letters、digits、`+`、`/`、`=`。
- 成功返回 `CompactSummaryValidation`。
- 失败抛 `ValueError`，错误消息包含字段或规则名。

## success state 构造

新增：

```python
def build_compact_success_state(
    *,
    previous_state: RollingSummaryState,
    summary: str,
    source_session_ids: Iterable[str],
    updated_at: str | None = None,
    config: CompactSummaryConfig | None = None,
    estimator: ContextTokenEstimator | None = None,
) -> RollingSummaryState:
    ...
```

处理规则：

1. 先调用 `validate_compact_summary_text(...)`。
2. 合并 `previous_state.covered_session_ids` 与 `source_session_ids`。
3. 使用 `normalize_session_ids(...)` 去重并保持首次出现顺序。
4. `source_session_count = len(merged_covered_session_ids)`。
5. `last_error=None`。
6. 调用已有 `build_rolling_summary_state(...)` 生成新 state。
7. 不写 RuntimeStore。
8. 不修改 previous_state。

## dict helper

新增：

```python
def compact_summary_prompt_to_dict(prompt: CompactSummaryPrompt) -> dict[str, Any]:
    ...
```

输出要求：

- 可 `json.dumps(..., ensure_ascii=False)`。
- tuple 输出为 list。
- messages 输出为 list[dict]，每个 dict 复制一份。
- 不共享可变引用。

## 测试要求

新增：

```text
backend/tests/test_context_compact_summarizer_prompt.py
```

至少覆盖：

1. `COMPACT_SUMMARY_REQUIRED_HEADINGS` 与总 spec §B 顺序一致。
2. `validate_compact_summary_config` 接受默认值。
3. config 字段为 bool、0、负数时抛 `ValueError`，错误消息包含字段名。
4. `compact_text_head_tail` 对短文本原样返回。
5. `compact_text_head_tail` 对长文本保留头尾与 marker。
6. `build_compact_summary_prompt` 生成两条 messages：system + user。
7. prompt user 内容包含 previous summary。
8. prompt user 内容包含 source session_id、created_at、question、answer。
9. source sessions 保持 oldest-first 顺序。
10. answer 超长时被 head/tail 收缩，actions 包含 `source_session_clipped`。
11. previous summary 超预算时被收缩，actions 包含 `previous_summary_clipped`。
12. source budget 达到上限时只保留连续前缀，actions 包含 `source_budget_reached`。
13. `plan.should_compact=False` 时 `build_compact_summary_prompt` 抛 `ValueError`。
14. `plan.source_sessions=()` 时 `build_compact_summary_prompt` 抛 `ValueError`。
15. prompt 超过 `model_max_input_tokens` 时抛 `ValueError`。
16. `normalize_compact_summary_text` 去掉包裹全文的 code fence。
17. `validate_compact_summary_text` 接受完整 headings 的 summary。
18. summary 缺 heading 时抛 `ValueError`。
19. summary headings 顺序错误时抛 `ValueError`。
20. summary 为空时抛 `ValueError`。
21. summary 超过 `target_summary_tokens` 时抛 `ValueError`。
22. summary 含 `data:image/` 时抛 `ValueError`。
23. summary 含超长 base64-like 片段时抛 `ValueError`。
24. `build_compact_success_state` 合并 previous covered ids 与 source ids。
25. `build_compact_success_state` 去重并保持顺序。
26. `build_compact_success_state` 设置 `last_error=None`。
27. `compact_summary_prompt_to_dict` 可 JSON 序列化。
28. dict helper 不共享 messages 可变引用。
29. prompt builder 不修改 `previous_state`、`plan`、source session 对象。
30. 本任务文件按项目禁用词表扫描通过。

建议测试 helper：

```python
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
```

## 验证命令

在 `backend` 目录运行：

```powershell
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider --basetemp=..\test-tmp\pytest tests\test_context_budget.py tests\test_context_assembler.py tests\test_context_budget_inspect.py tests\test_context_summary_state.py tests\test_context_compact_planner.py tests\test_context_compact_summarizer_prompt.py -v
```

文案检查：

- 使用项目约定的禁用词表扫描本任务涉及文件。
- 检查命令在本地临时输入，词表内容不要写进代码注释或 spec 正文。

## 提交给 Codex 的汇报格式

完成后汇报：

```text
任务 007 完成。

变更文件：
- backend/app/services/context_summary.py
- backend/tests/test_context_compact_summarizer_prompt.py

核心逻辑：
- 新增 CompactSummaryConfig / CompactSummaryPrompt / CompactSummaryValidation。
- build_compact_summary_prompt 构建 system+user messages。
- source sessions oldest-first 渲染，超长 answer 使用 head/tail 收缩。
- validate_compact_summary_text 校验 headings、长度和图片/base64 边界。
- build_compact_success_state 合并 covered ids 并生成 RollingSummaryState。
- 未接真实模型、未写 RuntimeStore、未改 messages 装配。

测试：
- 命令：...
- 结果：...

需要 Codex 审查的点：
- answer 收缩策略是否足够保留排查信息。
- required headings 是否需要允许英文标题。
- target_summary_tokens 超限时直接抛错是否适合 008 的执行器。
```

## Codex 审查重点

Codex 审查时重点看：

- prompt 是否严格遵守 §B 模板。
- source sessions 顺序是否稳定。
- 预算动作是否可解释。
- summary 校验是否足够严格。
- success state 是否只覆盖本批真正进入 summary 的 source_session_ids。
- 007 是否保持无外部副作用。