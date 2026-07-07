# Trae 协作任务 005：Compact 状态存储

更新时间：2026-07-06

执行对象：Trae Work，模型建议 GLM-5.2

审查对象：Codex

## 核心思想

本任务进入 compact 阶段的第一步，只实现状态存储层。

- 005 只新增 rolling summary、compact lock、compact metrics 的数据结构和 RuntimeStore 读写封装。
- 005 只处理 JSON payload 的规范化、校验、序列化和测试。
- 005 禁止调用模型，禁止生成摘要，禁止改变真实聊天 messages。
- 005 禁止接入 `_append_history()`、`build_chat_messages()`、`inspect_context()`。
- compact 的 planner、summary prompt、摘要执行、messages 注入进入后续任务。
- 代码表达保持单一路径、显式错误、可测试时间参数。

完成 005 后，后续任务可以安全地围绕同一份 compact state 继续实现 planner 和 summarizer。

## 背景文档

先阅读：

- `docs/co-work/context_management_refactor_spec_zh.md`
- 重点看 §A、§B、§C、§G、§H
- `docs/co-work/004-trae-context-budget-inspect.md`

当前代码边界：

- `backend/app/services/runtime_store.py` 已提供 `set_json(...)`、`get_json(...)`、`delete(...)`。
- `backend/app/services/assistant_chat.py` 中已有 `CHAT_HISTORY_KEY = "assistant:chat:history"`。
- `_append_history(...)` 负责写入历史；005 不接入该函数。
- `backend/app/services/context_budget.py` 已提供 `ContextTokenEstimator`，可用于估算 summary 文本。

## 改动范围

允许新增：

```text
backend/app/services/context_summary.py
backend/tests/test_context_summary_state.py
```

允许只在需要时修改：

```text
backend/app/services/context_budget.py
```

修改 `context_budget.py` 的条件：

- 只允许补充复用性很高的小型 helper。
- 不改变 001-004 已通过测试的行为。
- 不改变 `ContextTokenEstimator` 当前公开方法签名。

本任务禁止修改：

```text
backend/app/services/assistant_chat.py
backend/app/services/vision_model_client.py
backend/app/core/config.py
frontend / desktop app
```

## 新增模块

新增：

```text
backend/app/services/context_summary.py
```

建议模块职责：

```text
context_summary.py
  constants
  utc helpers
  frozen dataclasses
  payload converters
  CompactStateStore
```

本模块只依赖标准库、`RuntimeStore` 类型、`ContextTokenEstimator`。

## 常量

在 `context_summary.py` 中定义：

```python
ROLLING_SUMMARY_KEY = "assistant:chat:rolling_summary:v1"
COMPACT_LOCK_KEY = "assistant:chat:compact_lock:v1"
COMPACT_METRICS_KEY = "assistant:chat:compact_metrics:v1"
COMPACT_STATE_VERSION = 1
COMPACT_LOCK_OWNER = "assistant-chat"
DEFAULT_COMPACT_LOCK_TTL_SECONDS = 90
```

命名要求：

- key 字符串必须与总 spec §A/§G 一致。
- version 固定为 `1`。
- 常量集中放在文件顶部。

## 数据结构

全部使用 `@dataclass(frozen=True)`。

### CompactEstimate

```python
@dataclass(frozen=True)
class CompactEstimate:
    source: str
    tokens: int
    chars: int
```

规则：

- `source` 当前固定为 `"rough"`。
- `tokens`、`chars` 小于 0 时属于非法数据。
- payload 形式：

```json
{
  "source": "rough",
  "tokens": 0,
  "chars": 0
}
```

### RollingSummaryState

```python
@dataclass(frozen=True)
class RollingSummaryState:
    version: int
    summary: str
    covered_session_ids: tuple[str, ...]
    updated_at: str | None
    source_session_count: int
    estimate: CompactEstimate
    last_error: dict[str, Any] | None
```

payload 形式：

```json
{
  "version": 1,
  "summary": "",
  "covered_session_ids": [],
  "updated_at": null,
  "source_session_count": 0,
  "estimate": {
    "source": "rough",
    "tokens": 0,
    "chars": 0
  },
  "last_error": null
}
```

字段规则：

- `summary` 必须是字符串，允许空串作为初始状态。
- `covered_session_ids` 只接受非空字符串。
- `covered_session_ids` 保持首次出现顺序并去重。
- `updated_at` 使用 ISO-8601 UTC 字符串，初始状态为 `None`。
- `source_session_count` 大于等于 `len(covered_session_ids)`。
- `last_error` 只能是 `None` 或 JSON 可序列化 dict。

### CompactLock

```python
@dataclass(frozen=True)
class CompactLock:
    owner: str
    started_at: str
    expires_at: str
    source: str
```

payload 形式：

```json
{
  "owner": "assistant-chat",
  "started_at": "ISO-8601 UTC",
  "expires_at": "ISO-8601 UTC",
  "source": "auto"
}
```

字段规则：

- `owner` 默认 `COMPACT_LOCK_OWNER`。
- `source` 只接受 `"auto"` 或 `"manual"`。
- `started_at`、`expires_at` 必须能被 `datetime.fromisoformat(...)` 解析。
- `expires_at <= now` 时视为过期。

### CompactMetrics

```python
@dataclass(frozen=True)
class CompactMetrics:
    last_started_at: str | None
    last_finished_at: str | None
    last_status: str
    source_session_count: int
    covered_session_count: int
    summary_tokens: int
    source_tokens: int
    error_type: str | None
    error_message: str | None
```

payload 形式：

```json
{
  "last_started_at": null,
  "last_finished_at": null,
  "last_status": "idle",
  "source_session_count": 0,
  "covered_session_count": 0,
  "summary_tokens": 0,
  "source_tokens": 0,
  "error_type": null,
  "error_message": null
}
```

字段规则：

- `last_status` 只接受 `"idle"`、`"ok"`、`"error"`。
- 所有 count/token 字段必须大于等于 0。
- error 字段在 `"ok"` 或 `"idle"` 时可以为 `None`。

## 纯函数要求

在 `context_summary.py` 中实现下列纯函数。

### 时间 helper

```python
def utc_now_iso() -> str:
    ...
```

规则：

- 使用 `datetime.now(UTC).isoformat()`。
- 禁止使用 `datetime.utcnow()`。
- 测试中涉及时间判断的方法必须允许传入 `now`。

```python
def parse_utc_iso(value: str) -> datetime:
    ...
```

规则：

- 接收 ISO 字符串。
- 如果字符串没有 timezone，按 UTC 处理。
- 非字符串或无法解析时抛出 `ValueError`。

### covered_session_ids 规范化

```python
def normalize_session_ids(values: Iterable[Any]) -> tuple[str, ...]:
    ...
```

规则：

- 只保留 `str` 且 `strip()` 后非空的值。
- 去除首尾空白。
- 保持首次出现顺序。
- 去重。

### estimate converters

```python
def compact_estimate_to_payload(estimate: CompactEstimate) -> dict[str, int | str]:
    ...

def compact_estimate_from_payload(payload: Any) -> CompactEstimate:
    ...
```

规则：

- `from_payload` 对类型错误、缺字段、负数值抛出 `ValueError`。
- `to_payload` 返回 JSON 可序列化 dict。

### rolling summary converters

```python
def empty_rolling_summary_state() -> RollingSummaryState:
    ...

def build_rolling_summary_state(
    *,
    summary: str,
    covered_session_ids: Iterable[Any],
    source_session_count: int,
    updated_at: str | None = None,
    last_error: dict[str, Any] | None = None,
    estimator: ContextTokenEstimator | None = None,
) -> RollingSummaryState:
    ...

def rolling_summary_to_payload(state: RollingSummaryState) -> dict[str, Any]:
    ...

def rolling_summary_from_payload(payload: Any) -> RollingSummaryState:
    ...
```

规则：

- `empty_rolling_summary_state()` 返回初始状态，`updated_at=None`。
- `build_rolling_summary_state(...)` 使用 `ContextTokenEstimator.estimate_text(summary)` 计算 estimate。
- `updated_at` 未传入时使用 `utc_now_iso()`。
- `summary` 类型错误时抛出 `ValueError`。
- `source_session_count < len(covered_session_ids)` 时抛出 `ValueError`。
- `last_error` 如果传入，必须可被 `json.dumps(..., ensure_ascii=False)` 处理。
- `from_payload` 对非 dict、version 错误、字段缺失、字段类型错误抛出 `ValueError`。
- 转换函数不修改输入 payload。

### lock converters

```python
def compact_lock_to_payload(lock: CompactLock) -> dict[str, str]:
    ...

def compact_lock_from_payload(payload: Any) -> CompactLock:
    ...

def is_compact_lock_expired(lock: CompactLock, *, now: datetime | None = None) -> bool:
    ...
```

规则：

- `from_payload` 对非 dict、字段缺失、非法 source、非法时间抛出 `ValueError`。
- `is_compact_lock_expired(...)` 默认使用当前 UTC 时间。
- 测试中必须传入固定 `now`。

### metrics converters

```python
def empty_compact_metrics() -> CompactMetrics:
    ...

def compact_metrics_to_payload(metrics: CompactMetrics) -> dict[str, Any]:
    ...

def compact_metrics_from_payload(payload: Any) -> CompactMetrics:
    ...
```

规则：

- 初始 metrics 的 `last_status` 为 `"idle"`。
- `from_payload` 对非法 status、负数 count/token、字段缺失抛出 `ValueError`。

## CompactStateStore

新增：

```python
class CompactStateStore:
    def __init__(
        self,
        *,
        runtime_store: RuntimeStore,
        estimator: ContextTokenEstimator | None = None,
    ) -> None:
        ...
```

职责：

- 只包装 RuntimeStore 读写。
- 所有写入都走 converter。
- 所有读取都走 parser。
- 缺失 key 返回初始对象或 `None`。
- payload 结构错误时抛出 `ValueError`。

### summary 方法

```python
def load_summary(self) -> RollingSummaryState:
    ...

def save_summary(self, state: RollingSummaryState) -> None:
    ...

def build_and_save_summary(
    self,
    *,
    summary: str,
    covered_session_ids: Iterable[Any],
    source_session_count: int,
    updated_at: str | None = None,
    last_error: dict[str, Any] | None = None,
) -> RollingSummaryState:
    ...

def clear_summary(self) -> None:
    ...
```

规则：

- `load_summary()` 在 key 缺失时返回 `empty_rolling_summary_state()`。
- `save_summary(...)` 写入 `ROLLING_SUMMARY_KEY`。
- `build_and_save_summary(...)` 先构造 state，再写入 RuntimeStore，最后返回 state。
- `clear_summary()` 删除 `ROLLING_SUMMARY_KEY`。

### lock 方法

```python
def load_lock(self, *, now: datetime | None = None) -> CompactLock | None:
    ...

def acquire_lock(
    self,
    *,
    source: str,
    ttl_seconds: int = DEFAULT_COMPACT_LOCK_TTL_SECONDS,
    now: datetime | None = None,
) -> CompactLock | None:
    ...

def release_lock(self, *, owner: str = COMPACT_LOCK_OWNER) -> bool:
    ...

def clear_lock(self) -> None:
    ...
```

规则：

- `load_lock()` 在 key 缺失时返回 `None`。
- `load_lock()` 遇到过期 lock 时删除 key 并返回 `None`。
- `acquire_lock()` 在已有未过期 lock 时返回 `None`。
- `acquire_lock()` 在无有效 lock 时写入新 lock 并返回它。
- `ttl_seconds <= 0` 时抛出 `ValueError`。
- `source` 只接受 `"auto"` 或 `"manual"`。
- `release_lock()` 只在 owner 匹配时删除 key 并返回 `True`。
- owner 不匹配时返回 `False`，保留原 lock。

MVP 并发边界：

- 005 面向单后端进程。
- RuntimeStore 单次 `set_json/get_json/delete` 已有内部锁。
- 005 不改 RuntimeStore SQL，不新增跨进程 CAS。
- 后续 compact execution 如需跨进程强一致，再单独开任务。

### metrics 方法

```python
def load_metrics(self) -> CompactMetrics:
    ...

def save_metrics(self, metrics: CompactMetrics) -> None:
    ...

def save_success_metrics(
    self,
    *,
    started_at: str,
    finished_at: str,
    source_session_count: int,
    covered_session_count: int,
    summary_tokens: int,
    source_tokens: int,
) -> CompactMetrics:
    ...

def save_error_metrics(
    self,
    *,
    started_at: str,
    finished_at: str,
    source_session_count: int,
    covered_session_count: int,
    summary_tokens: int,
    source_tokens: int,
    error_type: str,
    error_message: str,
) -> CompactMetrics:
    ...

def clear_metrics(self) -> None:
    ...
```

规则：

- `load_metrics()` 在 key 缺失时返回 `empty_compact_metrics()`。
- success metrics 的 `last_status="ok"`，error 字段为 `None`。
- error metrics 的 `last_status="error"`，error 字段使用传入值。
- 所有 metrics 写入 `COMPACT_METRICS_KEY`。

## 编码规范

### 文件组织

- 文件顶部顺序：`from __future__ import annotations`、标准库 import、项目内 import、常量、dataclass、纯函数、Store class。
- import 保持最小集合。
- 类型注解覆盖所有公开函数、方法参数和返回值。
- 公开 converter 名称使用 `*_to_payload`、`*_from_payload`。
- RuntimeStore key 只通过常量引用，禁止在方法体内重复写字符串字面量。

### 数据处理

- 所有 dataclass 使用 `frozen=True`。
- `covered_session_ids` 在 dataclass 内使用 tuple，payload 中使用 list。
- 所有对外 payload 必须能被 `json.dumps(..., ensure_ascii=False)` 处理。
- 所有从 payload 读取出的 list/dict 都要复制到新对象。
- 禁止原地修改调用方传入的 payload、list、dict。
- 非法持久化数据抛出 `ValueError`，错误消息包含字段名。

### 时间处理

- 统一使用 timezone-aware UTC。
- 时间字符串统一用 `datetime.now(UTC).isoformat()` 形态。
- 需要比较时间的方法必须支持传入 `now`。
- 测试禁止依赖真实时间流逝。

### 错误处理

- 禁止裸 `except`。
- 禁止吞掉 JSON 结构错误。
- 允许捕获 `TypeError`、`ValueError` 后重新抛出带字段名的 `ValueError`。
- Store 方法只处理 RuntimeStore 缺失 key；结构错误交给调用方看到。

### 依赖边界

- 只使用标准库和 `ContextTokenEstimator`。
- 禁止新增 tokenizer、transformers、tiktoken、网络依赖。
- 禁止启动模型服务。
- 禁止在 005 中读取设置项或新增配置项。
- 禁止导入 `ChatAgent`，避免 compact state 与聊天执行链路耦合。
- 禁止新增后台线程、async task、定时器。

### 表达规范

- 注释只解释非显然规则。
- docstring 保持短句。
- 命名使用当前项目风格，避免抽象缩写。
- 提示文本、注释、测试名使用直接、具体、可执行表达。
- 禁止隐藏旁路、自动改写状态、悄悄修复损坏 payload。

## 测试要求

新增：

```text
backend/tests/test_context_summary_state.py
```

测试使用 FakeRuntimeStore，形态参考 `test_memory_service.py`：

```python
class FakeRuntimeStore:
    def __init__(self) -> None:
        self.data: dict[str, object] = {}
        self.deleted: list[str] = []

    def set_json(self, name: str, payload: object, *, ttl_seconds: int | None = None) -> None:
        self.data[name] = payload

    def get_json(self, name: str) -> object | None:
        return self.data.get(name)

    def delete(self, name: str) -> None:
        self.deleted.append(name)
        self.data.pop(name, None)
```

必须覆盖：

1. 常量 key 与 spec 一致。
2. `empty_rolling_summary_state()` 返回 version=1、summary 空串、covered ids 空 tuple、estimate rough 0。
3. `normalize_session_ids(...)` 去空、去重、保持顺序。
4. `build_rolling_summary_state(...)` 计算 summary rough estimate。
5. `rolling_summary_to_payload(...)` 可 JSON 序列化。
6. `rolling_summary_from_payload(...)` roundtrip 后字段一致。
7. malformed rolling summary payload 抛出 `ValueError`，至少覆盖非 dict、version 错误、estimate 缺字段。
8. `CompactStateStore.load_summary()` 在缺失 key 时返回初始状态。
9. `CompactStateStore.save_summary()` 写入 `ROLLING_SUMMARY_KEY`。
10. `build_and_save_summary(...)` 返回值与 RuntimeStore 中 payload 一致。
11. `acquire_lock(source="auto", now=fixed_now)` 写入 lock，并返回 lock。
12. active lock 存在时再次 `acquire_lock(...)` 返回 `None`。
13. expired lock 存在时 `acquire_lock(...)` 可写入新 lock。
14. `release_lock(owner=COMPACT_LOCK_OWNER)` 删除匹配 owner 的 lock 并返回 `True`。
15. `release_lock(owner="other")` 返回 `False`，保留原 lock。
16. malformed lock payload 抛出 `ValueError`，至少覆盖非法 source 和非法时间。
17. `empty_compact_metrics()` 返回 idle 状态。
18. success metrics 写入后 load 得到 `last_status="ok"`。
19. error metrics 写入后 load 得到 `last_status="error"` 与错误字段。
20. malformed metrics payload 抛出 `ValueError`，至少覆盖非法 status 和负数 token。
21. 所有 payload converter 不修改输入 dict/list。

建议测试固定时间：

```python
from datetime import UTC, datetime, timedelta

FIXED_NOW = datetime(2026, 7, 6, 12, 0, 0, tzinfo=UTC)
```

## 建议验证命令

```powershell
cd D:\AI_Workspace\window\backend
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider --basetemp=..\test-tmp\pytest tests\test_context_budget.py tests\test_context_assembler.py tests\test_context_budget_inspect.py tests\test_context_summary_state.py -v
```

文案检查：使用项目禁用词表检查本任务涉及文件。

期望：

- pytest 全部通过。
- 文案检查无命中。
- 004 已有测试继续通过。

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

- 005 是否只实现 compact state store。
- RuntimeStore key 是否与总 spec 一致。
- dataclass 是否 frozen，payload 是否可 JSON 序列化。
- malformed payload 是否抛出显式 `ValueError`。
- lock 过期、获取、释放逻辑是否可预测。
- summary estimate 是否复用 `ContextTokenEstimator`。
- 是否未修改真实聊天 messages 和模型请求链路。
- 测试是否覆盖状态、锁、metrics 三块。
