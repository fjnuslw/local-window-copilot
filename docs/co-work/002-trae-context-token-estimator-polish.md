# Trae 协作任务 002：ContextTokenEstimator 精简修正

更新时间：2026-07-06

执行对象：Trae Work，模型建议 GLM-5.2

审查对象：Codex

## 核心思想

本项目当前按本地 MiniCPM / OpenAI-compatible 调用链推进。上下文治理要少路径、强可见、易调试。

- 请求前只依赖本地 deterministic rough estimate 做预算判断。
- 响应 usage 若已经存在，只用于展示、校准和后续调参。
- compact 是后续主线；估算器只提供可审计数字。
- 图片和文档按固定成本估算，base64 长度不进入文本预算。
- 代码保持单一路径，错误要显式暴露，trace 要能解释原因。
- 本任务只修正估算器，不接入聊天请求链路。

## 背景文档

先阅读：

- `docs/co-work/context_management_refactor_spec_zh.md`
- 重点看 §2.1、§5、§A、§D、§F、§H

## 改动范围

允许修改：

```text
backend/app/services/context_budget.py
backend/tests/test_context_budget.py
```

本任务不修改：

```text
backend/app/services/assistant_chat.py
backend/app/services/vision_model_client.py
backend/app/services/agent_tools.py
backend/app/core/config.py
frontend / desktop app
```

## 修正要求

1. 图片固定估算值改为 2000 tokens。

2. multimodal 图片识别覆盖三种 part type：

```text
image_url
image
input_image
```

三者统一计入 `image_tokens`，不估算 base64 字符串长度。

3. CJK 范围补齐 Extension G/H：

```text
0x30000 <= cp <= 0x323AF
```

4. 不可 JSON 序列化值的测试与注释改为“按字符串估算”语义。

建议命名：

```python
test_value_uses_str_for_non_serializable
```

注释使用“按字符串估算”语义，避免引入额外路径暗示。

5. 增加测试覆盖：

- `image_url` part 计入 2000。
- `image` part 计入 2000。
- `input_image` part 计入 2000。
- 含 base64 data URL 的图片不会按字符串长度膨胀。
- Extension G/H 字符按 CJK token 规则估算。

## 质量约束

- 只使用标准库。
- 不引入 tokenizer、transformers、tiktoken、网络依赖。
- 不改现有业务行为。
- 不启动模型服务。
- 不新增 provider 计数、模型切换、提示词替换等额外运行路径。
- 注释只解释非显然规则。

## 建议验证命令

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

- 图片固定值是否统一为 2000。
- `image_url`、`image`、`input_image` 是否都走图片桶。
- base64 是否没有进入文本估算。
- CJK Extension G/H 是否覆盖。
- 文案是否保持直接、具体、可执行。
