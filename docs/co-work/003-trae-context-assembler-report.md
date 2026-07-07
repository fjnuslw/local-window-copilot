# Trae 协作任务 003：ContextAssembler 预算报告

更新时间：2026-07-06

执行对象：Trae Work，模型建议 GLM-5.2

审查对象：Codex

## 核心思想

本任务做上下文治理的第二个可审计切片：把现有 messages 映射成 segment，并生成预算报告。

- 本地 MiniCPM / OpenAI-compatible 调用链保持单一路径。
- 003 只新增纯数据结构与纯函数式报告能力。
- 003 不接入真实聊天请求，不改变 `build_chat_messages` 输出。
- rough estimate 是本阶段唯一预算判断来源。
- 响应 usage 若已存在，后续只做展示和校准；本任务不实现 usage 读取。
- compact 是后续主线；003 只提供 compact 需要的 segment 账本。
- 报告要能解释每段上下文的 token 来源、角色、优先级和是否超限。

## 背景文档

先阅读：

- `docs/co-work/context_management_refactor_spec_zh.md`
- 重点看 §2.1、§4、§5、§6、§9、§10、§H
- `docs/co-work/001-trae-context-token-estimator.md`
- `docs/co-work/002-trae-context-token-estimator-polish.md`

当前代码边界：

- `vision_model_client.build_chat_messages(...)` 已生成 OpenAI style messages。
- `assistant_chat._build_answer_context(...)` 当前直接保存 `context.messages`。
- `assistant_chat.inspect_context(...)` 当前用 `chars // 2` 展示估算。

003 只为后续替换 `chars // 2` 准备报告能力。

## 改动范围

允许修改：

```text
backend/app/services/context_budget.py
backend/tests/test_context_budget.py
```

也可以新增：

```text
backend/tests/test_context_assembler.py
```

本任务只读参考：

```text
backend/app/services/assistant_chat.py
backend/app/services/vision_model_client.py
docs/co-work/context_management_refactor_spec_zh.md
```

本任务不修改：

```text
backend/app/services/assistant_chat.py
backend/app/services/vision_model_client.py
backend/app/core/config.py
frontend / desktop app
```

## 实现要求

在 `backend/app/services/context_budget.py` 中扩展数据结构。

建议结构：

```python
@dataclass(frozen=True)
class ContextSegment:
    kind: str
    role: str
    label: str
    message: dict[str, Any]
    required: bool
    priority: int
    metadata: dict[str, Any] = field(default_factory=dict)

@dataclass(frozen=True)
class ContextSegmentReport:
    kind: str
    role: str
    label: str
    tokens: int
    chars: int
    required: bool
    priority: int
    metadata: dict[str, Any] = field(default_factory=dict)
    details: dict[str, int] = field(default_factory=dict)

@dataclass(frozen=True)
class ContextBudgetReport:
    ctx_size: int
    input_limit: int
    estimated_input_tokens: int
    estimated_chars: int
    over_limit: bool
    segments: list[ContextSegmentReport]
    totals: dict[str, int] = field(default_factory=dict)
    actions: list[str] = field(default_factory=list)
```

新增 `ContextAssembler`：

```python
class ContextAssembler:
    def __init__(self, estimator: ContextTokenEstimator | None = None) -> None: ...
    def segments_from_messages(self, messages: list[dict[str, Any]]) -> list[ContextSegment]: ...
    def build_report(
        self,
        segments: list[ContextSegment],
        *,
        ctx_size: int,
        input_limit: int,
    ) -> ContextBudgetReport: ...
```

### segment 推断规则

`segments_from_messages` 只按 message 顺序和 role 做轻量推断，不解析提示词正文。

推荐规则：

| 条件 | kind | label | required | priority |
|---|---|---|---:|---:|
| index=0 且 role=system | system | base_prefix | true | 100 |
| index=1 且 role=user | profile | profile_packet | true | 90 |
| index=2 且 role=user | memory | context_packet | false | 60 |
| 最后一条 message 且 role=user | question | current_question | true | 95 |
| role=tool | tool_result | tool_result:{index} | false | 50 |
| 其他 user/assistant | history | history:{index} | false | 70 |
| 其他 role | other | message:{index} | false | 10 |

注意：

- 最后一条 user message 优先标记为 `question`。
- 输入 messages 不能被原地修改。
- metadata 至少包含 `index`。

### report 规则

`build_report`：

- 使用 `ContextTokenEstimator.estimate_message(segment.message)` 估算每个 segment。
- `estimated_input_tokens` 是所有 segment token 之和。
- `estimated_chars` 是所有 segment chars 之和。
- `over_limit = estimated_input_tokens > input_limit`。
- `totals` 至少包含：

```python
{
    "segments": len(segments),
    "text_tokens": ...,
    "json_tokens": ...,
    "image_tokens": ...,
    "overhead_tokens": ...,
    "tool_call_tokens": ...,
}
```

- `actions`：
  - 正常为空列表。
  - 超限时包含 `"over_limit_detected"`。

### 序列化

为 report 提供可 JSON 序列化输出。

可以选择：

- 在 dataclass 上提供 `to_dict()` 方法。
- 或新增纯函数 `budget_report_to_dict(report)`。

输出中不要包含不可序列化对象。

## 测试要求

新增或扩展测试，覆盖：

1. canonical messages 映射成 `system/profile/memory/history/question`。
2. `system/profile/question` 为 required。
3. segment metadata 保留原始 index。
4. 输入 messages 在调用后保持不变。
5. report 总 tokens 等于逐段 tokens 之和。
6. report totals 汇总 text/json/image/overhead/tool_call 五个桶。
7. 图片 message 的 `image_tokens` 为 2000。
8. assistant `tool_calls` 计入 `tool_call_tokens`。
9. `estimated_input_tokens > input_limit` 时 `over_limit=True` 且 actions 包含 `over_limit_detected`。
10. report 序列化结果可被 `json.dumps(..., ensure_ascii=False)` 处理。

## 质量约束

- 只使用标准库。
- 不引入 tokenizer、transformers、tiktoken、网络依赖。
- 不改现有业务行为。
- 不启动模型服务。
- 不新增 provider 计数、模型切换、提示词替换等额外运行路径。
- 不接入 `assistant_chat.inspect_context`。
- 不接入 `vision_model_client.build_chat_messages`。
- 注释只解释非显然规则。

## 建议验证命令

```powershell
cd D:\AI_Workspace\window\backend
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider --basetemp=..\test-tmp\pytest tests\test_context_budget.py tests\test_context_assembler.py -v
```

如果只扩展了 `test_context_budget.py`：

```powershell
cd D:\AI_Workspace\window\backend
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider --basetemp=..\test-tmp\pytest tests\test_context_budget.py -v
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

- segment 推断是否稳定、简单、可解释。
- report token 汇总是否等于逐段估算之和。
- 输入 messages 是否保持原样。
- 超限状态是否只进入 report，没有改变业务链路。
- 序列化输出是否适合后续 `inspect_context` 和 WebUI 展示。
