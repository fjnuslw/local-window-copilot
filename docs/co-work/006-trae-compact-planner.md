# Trae 协作任务 006：CompactPlanner 纯选择器

更新时间：2026-07-06

执行对象：Trae Work，模型建议 GLM-5.2

审查对象：Codex

## 核心思想

006 只实现 compact 前的选择与报告。

- `CompactPlanner` 是后端纯选择器，禁止注册为 Agent tool，禁止暴露给模型调用。
- 输入为已完成 chat sessions、已覆盖 session ids、planner 配置、触发来源。
- 输出为 `CompactPlan`，说明本轮是否需要 compact、触发原因、保留哪些 raw tail、本批处理哪些 source sessions、哪些 session 因已覆盖或预算限制被跳过。
- 006 禁止调用模型，禁止生成 summary，禁止写 RuntimeStore，禁止修改聊天 messages。
- 自动触发和手动触发在本任务中只体现为参数与报告字段：`force=True` 代表手动触发；自动触发依据未覆盖轮数和 rough tokens 判断。
- 后续 `CompactSummarizer` 读取 `CompactPlan` 后才会构造摘要 prompt、调用模型、提交 state。

完成 006 后，系统会具备 compact 决策能力；真实执行链路仍由后续任务接入。

## 背景文档

先阅读：

- `docs/co-work/context_management_refactor_spec_zh.md`
- 重点看 §A、§B、§C、§D、§G、§H
- `docs/co-work/005-trae-compact-state-store.md`

当前代码事实：

- `backend/app/services/assistant_chat.py` 的 `_append_history(...)` 把最新 session 插入 `CHAT_HISTORY_KEY` 第 0 位。
- `CHAT_HISTORY_KEY` 的存储顺序是 newest-first。
- `ChatSession` 字段包含 `session_id`、`question`、`answer`、`created_at`、`status`。
- `_stream_model_answer(...)` 已在 session done 后调用 `_append_history(session)`，随后调用 `_index_session_to_fts(session)`。
- `backend/app/services/context_budget.py` 已提供 `ContextTokenEstimator`。
- `backend/app/services/context_summary.py` 已提供 `normalize_session_ids(...)`、rolling summary state、compact lock、compact metrics、`CompactStateStore`。

## 改动范围

允许修改：

```text
backend/app/services/context_summary.py
```

允许新增：

```text
backend/tests/test_context_compact_planner.py
```

仅在复用确有必要时允许修改：

```text
backend/tests/test_context_summary_state.py
```

本任务禁止修改：

```text
backend/app/services/assistant_chat.py
backend/app/services/vision_model_client.py
backend/app/core/config.py
frontend / desktop app
```

本任务禁止新增：

```text
模型调用
线程或定时器
后台任务入口
WebUI API
RuntimeStore 写入
真实 messages 裁剪或注入
```

## 命名与模块位置

继续使用：

```text
backend/app/services/context_summary.py
```

建议把 006 新增内容放在 005 数据结构和 store 之后，分区标题可用：

```python
# ---------------------------------------------------------------------------
# compact planner
# ---------------------------------------------------------------------------
```

`context_summary.py` 允许继续依赖：

```text
标准库
ContextTokenEstimator
```

006 新增的 planner 逻辑禁止导入：

```text
ChatAgent
get_settings
RuntimeStore 实例
vision_model_client
OpenAI / requests / httpx
```

`CompactStateStore` 仍可保留现有 `RuntimeStore` 导入。planner 自身不能使用 store。

## 数据结构

全部使用 `@dataclass(frozen=True)`。

### CompactHistorySession

新增 compact 专用 session 视图，避免 planner 直接依赖 Pydantic 模型。

```python
@dataclass(frozen=True)
class CompactHistorySession:
    session_id: str
    created_at: str
    question: str
    answer: str
```

规则：

- `session_id` strip 后必须非空。
- `created_at` 必须是 str；允许空字符串，planner 只透传，不做排序。
- `question` 和 `answer` 必须是 str；`None` 视为非法输入。
- compact 只处理 done session；006 的输入侧先按当前 `history()` 结果提供，planner 内可忽略没有 `session_id` 的数据。

### CompactPlannerConfig

新增本地 planner 配置，暂时不接 `config.py`。

```python
@dataclass(frozen=True)
class CompactPlannerConfig:
    raw_tail_turns: int = 2
    batch_session_limit: int = 12
    source_budget_tokens: int = 18000
    uncovered_session_threshold: int = 6
    history_trigger_tokens: int = 24000
```

校验规则：

- `raw_tail_turns >= 0`
- `batch_session_limit >= 1`
- `source_budget_tokens >= 1`
- `uncovered_session_threshold >= 1`
- `history_trigger_tokens >= 1`
- bool 不能通过 int 校验。

新增 helper：

```python
def validate_compact_planner_config(config: CompactPlannerConfig) -> CompactPlannerConfig:
    ...
```

### CompactPlan

```python
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
```

`trigger` 只允许：

```text
none
manual
session_threshold
token_threshold
```

`actions` 使用短 snake_case 字符串。建议值：

```text
manual_requested
session_threshold_reached
token_threshold_reached
no_source_sessions
source_budget_reached
single_source_exceeds_budget
```

## 输入规范

新增转换函数：

```python
def compact_history_session_from_value(value: Any) -> CompactHistorySession | None:
    ...
```

支持两类输入：

1. `ChatSession` 或类似对象：通过属性读取 `session_id`、`created_at`、`question`、`answer`。
2. dict payload：通过 key 读取同名字段。

转换规则：

- `session_id` 缺失、非 str、strip 后为空：返回 `None`。
- `question`、`answer` 非 str：抛 `ValueError`，错误消息包含字段名。
- `created_at` 是 `datetime` 时转 `isoformat()`；是 str 时原样使用；缺失时用空字符串；其他类型转 `str(value)`。
- 不修改输入对象或 dict。

输入 sessions 顺序要求：

- `plan_compact(...)` 接收 newest-first sessions，与 `CHAT_HISTORY_KEY` 一致。
- `tail_sessions` 返回 newest-first，供后续 messages 注入保留最近原文。
- `source_sessions` 返回 oldest-first，供后续 summary prompt 按时间正序读取。
- planner 禁止按 `created_at` 自行排序；排序依据只来自输入顺序。

## token 估算

新增 helper：

```python
def estimate_compact_session_tokens(
    session: CompactHistorySession,
    *,
    estimator: ContextTokenEstimator | None = None,
) -> int:
    ...
```

估算规则：

- 把 question 估算为一条 `{"role": "user", "content": session.question}`。
- 把 answer 估算为一条 `{"role": "assistant", "content": session.answer}`。
- 两条估算相加，包含 message overhead。
- 不估算 `session_id` 和 `created_at`；后续 prompt builder 会给元信息留模板预算。

## planner API

新增主函数：

```python
def plan_compact(
    *,
    sessions: Iterable[Any],
    covered_session_ids: Iterable[str],
    config: CompactPlannerConfig | None = None,
    force: bool = False,
    estimator: ContextTokenEstimator | None = None,
) -> CompactPlan:
    ...
```

处理流程：

1. 校验 config；`None` 时使用 `CompactPlannerConfig()`。
2. 读取 sessions，转换为 `CompactHistorySession`，丢弃无 session_id 的项。
3. 保持输入 newest-first 顺序。
4. `tail_sessions = sessions[:raw_tail_turns]`。
5. `older_sessions = sessions[raw_tail_turns:]`。
6. `covered_session_ids` 先用 `normalize_session_ids(...)` 规范化。
7. `older_sessions` 中已覆盖的 session 记录到 `skipped_covered_session_ids`。
8. 未覆盖 older sessions 形成候选集 `uncovered_candidates`。
9. `uncovered_session_ids` 按 newest-first 记录全部候选 id，便于 UI 与 trace 对照。
10. 计算全部未覆盖候选的 rough tokens，作为自动 token threshold 判断依据。
11. 自动触发规则：
    - 未覆盖候选数量达到 `uncovered_session_threshold`，trigger 为 `session_threshold`。
    - 未覆盖候选 rough tokens 达到 `history_trigger_tokens`，trigger 为 `token_threshold`。
    - 两者同时达到时，优先 `session_threshold`，actions 同时记录两个 reached。
12. 手动触发规则：
    - `force=True` 且存在未覆盖候选时，trigger 为 `manual`。
    - `force=True` 且无未覆盖候选时，`should_compact=False`，actions 包含 `manual_requested` 和 `no_source_sessions`。
13. 未触发时：
    - `should_compact=False`
    - `trigger="none"`
    - `source_sessions=()`
    - 仍返回 `tail_sessions`、`uncovered_session_ids`、`estimated_tail_tokens`
14. 触发后选择 source sessions：
    - 从 `uncovered_candidates` 的 oldest-first 顺序开始选择。
    - 最多选择 `batch_session_limit` 条。
    - 累加 `estimate_compact_session_tokens(...)`，总量受 `source_budget_tokens` 限制。
    - 如果第一条 source session 单独超过预算，仍选择这一条，actions 增加 `single_source_exceeds_budget`。
    - 如果已有 source session 后遇到预算上限，停止选择，剩余候选 ids 写入 `skipped_budget_session_ids`，actions 增加 `source_budget_reached`。
15. 返回 `CompactPlan`。

## report helper

新增 JSON 化函数：

```python
def compact_plan_to_dict(plan: CompactPlan) -> dict[str, Any]:
    ...
```

输出要求：

- 可直接 `json.dumps(..., ensure_ascii=False)`。
- `source_sessions`、`tail_sessions` 输出 list[dict]。
- tuple 字段输出 list。
- 不包含原始对象引用。
- 不修改 `plan`。

## 行为细节

### raw tail

raw tail 是最新少量原文轮次，默认 2。

示例：输入 newest-first：

```text
[s7, s6, s5, s4, s3, s2, s1]
```

`raw_tail_turns=2` 时：

```text
tail_sessions = [s7, s6]
older_sessions = [s5, s4, s3, s2, s1]
```

候选进入 summary 时转 oldest-first：

```text
source selection order = [s1, s2, s3, s4, s5]
```

### covered ids

covered ids 表示旧 summary 已包含的 session。

- covered ids 不再进入 source sessions。
- covered ids 不影响 raw tail；最新两轮即使已覆盖，也按 raw tail 保留。
- `skipped_covered_session_ids` 按 older_sessions 的 newest-first 顺序记录。

### threshold

自动 compact 只看 raw tail 之外的未覆盖候选。

- 最近 raw tail 不参与 uncovered threshold。
- 全部未覆盖候选参与 token threshold 估算。
- 触发阈值只决定是否需要 compact，source batch 仍受 batch limit 和 source budget 控制。

### 手动触发

`force=True` 用于未来 WebUI 或调试接口的“立即 compact”。

- 有未覆盖候选：`should_compact=True`，trigger 为 `manual`。
- 无未覆盖候选：`should_compact=False`，trigger 为 `none`，actions 包含 `manual_requested`、`no_source_sessions`。
- 手动触发仍遵守 batch limit 与 source budget。

## 测试要求

新增：

```text
backend/tests/test_context_compact_planner.py
```

至少覆盖以下用例：

1. 空 sessions 返回 no compact，tail/source 均为空。
2. newest-first 输入时，`raw_tail_turns=2` 保留最新两条为 tail。
3. `source_sessions` 按 oldest-first 返回。
4. covered ids 被跳过，并记录到 `skipped_covered_session_ids`。
5. 未达到阈值且 `force=False` 时不选择 source sessions。
6. 未覆盖数量达到 `uncovered_session_threshold` 时自动触发。
7. 未覆盖 rough tokens 达到 `history_trigger_tokens` 时自动触发。
8. 两个阈值同时达到时 trigger 取 `session_threshold`，actions 记录两个原因。
9. `force=True` 且存在未覆盖候选时触发 manual。
10. `force=True` 且无未覆盖候选时不 compact，并记录 `no_source_sessions`。
11. `batch_session_limit` 限制本批 source 数量。
12. `source_budget_tokens` 限制本批 source，总量超出时记录 skipped budget ids。
13. 第一条 source 单独超过预算时仍选择一条，并记录 `single_source_exceeds_budget`。
14. tail token 估算写入 `estimated_tail_tokens`。
15. invalid config 抛 `ValueError`，错误消息包含字段名。
16. dict 与对象输入都能转换为 `CompactHistorySession`。
17. 非 str question/answer 抛 `ValueError`，错误消息包含字段名。
18. `compact_plan_to_dict(...)` 可 JSON 序列化。
19. planner 不修改输入 list 或 dict。
20. 文案检查：本任务涉及文件按项目禁用词表扫描通过。

建议测试 helper：

```python
def make_session(i: int, *, question: str | None = None, answer: str | None = None) -> dict[str, str]:
    return {
        "session_id": f"s{i}",
        "created_at": f"2026-07-06T12:{i:02d}:00+00:00",
        "question": question if question is not None else f"question {i}",
        "answer": answer if answer is not None else f"answer {i}",
    }
```

## 验证命令

在 `backend` 目录运行：

```powershell
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider --basetemp=..\test-tmp\pytest tests\test_context_budget.py tests\test_context_assembler.py tests\test_context_budget_inspect.py tests\test_context_summary_state.py tests\test_context_compact_planner.py -v
```

文案检查：

- 使用项目约定的禁用词表扫描本任务涉及文件。
- 检查命令在本地临时输入，词表内容不要写进代码注释或 spec 正文。

## 提交给 Codex 的汇报格式

完成后汇报：

```text
任务 006 完成。

变更文件：
- backend/app/services/context_summary.py
- backend/tests/test_context_compact_planner.py

核心逻辑：
- 新增 CompactHistorySession / CompactPlannerConfig / CompactPlan。
- plan_compact 保持输入 newest-first，tail 返回 newest-first，source 返回 oldest-first。
- 自动触发依据未覆盖轮数与 rough tokens；手动触发通过 force=True。
- source batch 受 batch_session_limit 与 source_budget_tokens 控制。
- 未接模型、未写 RuntimeStore、未改 messages。

测试：
- 命令：...
- 结果：...

需要 Codex 审查的点：
- source budget 达到上限时停止选择，是否符合后续 summarizer 分批策略。
- 第一条 source 单独超过预算时仍选择一条，是否需要后续 prompt builder 做 head/tail 压缩。
- trigger 优先级 session_threshold > token_threshold 是否符合 WebUI 展示预期。
```

## Codex 审查重点

Codex 审查时重点看：

- planner 是否完全纯函数化。
- newest-first 与 oldest-first 两个顺序是否稳定。
- covered ids 是否只影响 older sessions。
- threshold 与 source selection 是否分层清楚。
- source budget 达到上限时是否无跳洞选择。
- 代码是否提前接入模型、store、WebUI 或 messages。