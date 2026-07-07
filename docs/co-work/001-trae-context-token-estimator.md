# Trae 协作任务 001：ContextTokenEstimator 开荒

更新时间：2026-07-06

执行对象：Trae Work，模型建议 GLM-5.2

审查对象：Codex

## 任务目标

完成上下文管理改造的第一个小切片：新增一个纯函数式 token 估算模块，为后续 compact 提供可审计的 rough token 数字。

本任务只做估算器和测试，不接入当前对话请求链路。

## 背景文档

先阅读：

- `docs/co-work/context_management_refactor_spec_zh.md`
- 重点看 §5、§A、§D、§F、§H

现状依据：

- `assistant_chat.inspect_context` 当前使用 `chars // 2` 展示估算。
- `vision_model_client.build_chat_messages` 当前按最近 N 轮原样追加历史。
- compact MVP 的第 1 步是 `ContextTokenEstimator`。

## 改动范围

允许新增：

```text
backend/app/services/context_budget.py
backend/tests/test_context_budget.py
```

允许只读参考：

```text
backend/app/services/assistant_chat.py
backend/app/services/vision_model_client.py
backend/app/services/agent_tools.py
docs/co-work/context_management_refactor_spec_zh.md
```

本任务不修改：

```text
backend/app/services/assistant_chat.py
backend/app/services/vision_model_client.py
backend/app/services/agent_tools.py
backend/app/core/config.py
frontend / desktop app
```

## 实现要求

在 `backend/app/services/context_budget.py` 中实现：

```python
from dataclasses import dataclass, field
from typing import Any

@dataclass(frozen=True)
class TokenEstimate:
    source: str
    tokens: int
    chars: int
    details: dict[str, int] = field(default_factory=dict)

class ContextTokenEstimator:
    def estimate_text(self, text: str) -> TokenEstimate: ...
    def estimate_value(self, value: Any) -> TokenEstimate: ...
    def estimate_message(self, message: dict[str, Any]) -> TokenEstimate: ...
    def estimate_messages(self, messages: list[dict[str, Any]]) -> TokenEstimate: ...
```

`source` 固定为 `"rough"`。

### 估算规则

`estimate_text`：

- CJK/Hangul/Kana 字符：按 1 token 计。
- ASCII 字母数字和常见空白：按 `ceil(chars / 4)` 计。
- JSON/标点/符号：按 `ceil(chars / 2)` 计。
- 空字符串返回 0。

`estimate_value`：

- `str` 走 `estimate_text`。
- `dict` / `list` 使用 `json.dumps(..., ensure_ascii=False, separators=(",", ":"))` 转成紧凑 JSON，再估算。
- 其他值转成 `str` 再估算。

`estimate_message`：

- 估算 `message["content"]`。
- 加 role/key overhead，建议固定 10 tokens。
- 若存在 `tool_calls`，完整估算 tool call envelope，包括 id、type、function.name、function.arguments。
- 若 content 是 OpenAI style multimodal list，图片 part 按固定 2000 tokens 计，文本 part 正常估算。

`estimate_messages`：

- 汇总每条 message。
- details 至少包含：

```python
{
    "messages": len(messages),
    "text_tokens": ...,
    "json_tokens": ...,
    "image_tokens": ...,
    "overhead_tokens": ...,
    "tool_call_tokens": ...
}
```

## 测试要求

新增 `backend/tests/test_context_budget.py`。

覆盖：

1. 中文文本估算大于同长度英文按 `chars/4` 的估算。
2. 英文长文本接近 `ceil(chars / 4)`。
3. dict/list 使用紧凑 JSON，不依赖格式化缩进。
4. multimodal image part 计入 `image_tokens`。
5. assistant `tool_calls` envelope 计入 `tool_call_tokens`。
6. `estimate_messages([])` 返回 0 tokens 且 details messages 为 0。
7. 所有公开方法返回 `TokenEstimate(source="rough")`。

建议命令：

```powershell
cd D:\AI_Workspace\window
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider --basetemp=test-tmp\pytest backend\tests\test_context_budget.py
```

如果本地命令不可运行，请在交付说明中写明原因和已完成的静态检查。

## 质量约束

- 只使用标准库。
- 不引入 tokenizer、transformers、tiktoken、网络依赖。
- 不改现有业务行为。
- 不启动模型服务。
- 不写隐藏替代链路。
- 不吞异常；无法序列化的值使用 `str(value)` 明确处理。
- 注释只解释非显然规则。

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

Codex 后续审查：

- 估算规则是否和 spec 一致。
- tool_calls 是否完整计入。
- multimodal 图片是否按固定 token 计入。
- 代码是否保持纯函数式、无业务链路副作用。
- 测试是否覆盖中文、英文、JSON、图片、tool_calls。
