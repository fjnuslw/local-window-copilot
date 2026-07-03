# 目标模型实现计划

更新时间：2026-07-02

## 路线

当前模型主链路固定为：

```text
MiniCPM-V 4.6 F16 GGUF
+ mmproj-model-f16.gguf
+ llama.cpp llama-server
+ FastAPI ModelRuntimeManager
```

不引入多模型适配层。模型不可用时，后端进入明确错误状态。

## 模型文件

期望目录：

```text
runtime/models/minicpm-v4.6/
  MiniCPM-V-4_6-F16.gguf
  mmproj-model-f16.gguf
```

运行时：

```text
runtime/llama.cpp/
  llama-server.exe
  llama-mtmd-cli.exe
```

## llama-server

正式链路使用常驻 `llama-server`：

```text
runtime/llama.cpp/llama-server.exe
  -m runtime/models/minicpm-v4.6/MiniCPM-V-4_6-F16.gguf
  --mmproj runtime/models/minicpm-v4.6/mmproj-model-f16.gguf
  -c 8192
  --gpu-layers all
  --reasoning off
  --host 127.0.0.1
  --port 18181
  --no-webui
```

`llama-mtmd-cli.exe` 只用于手动单图验证，不作为应用主链路。

## 请求形态

FastAPI 通过 OpenAI-compatible `/v1/chat/completions` 调用本地 server：

```json
{
  "model": "minicpm-v4.6-f16",
  "temperature": 0.1,
  "max_tokens": 512,
  "messages": [
    {
      "role": "user",
      "content": [
        {
          "type": "text",
          "text": "<window analysis prompt>"
        },
        {
          "type": "image_url",
          "image_url": {
            "url": "data:image/jpeg;base64,<resized-window-image>"
          }
        }
      ]
    }
  ]
}
```

图片进入模型前压缩为 JPEG，默认最长边 512px。后续是否提高分辨率，应由真实窗口样本评估决定。

## 输出契约

窗口分析输出保持短、稳、可解析：

```json
{
  "window_type": "ide",
  "summary": "一句话总结当前窗口。",
  "key_points": ["关键点 1", "关键点 2"],
  "candidate_questions": [
    {
      "question": "用户可能想问的问题",
      "category": "understand",
      "reason": "为什么此时适合问",
      "priority": 0.8
    }
  ],
  "caution": null
}
```

原则：

- 不输出 Markdown。
- 不声称能点击、输入、提交或操作电脑。
- 不确定时明确说不确定。
- 只给建议和解释。

## 当前已验证

- `llama-server` 可启动到 `/health` ready。
- 截图压缩后可通过本地 server 分析。
- FastAPI 已封装 `ModelRuntimeManager` 和 `VisionModelClient`。
- 自动观察检测到新截图 hash 后可触发分析。

## 下一步

- 用 20 个真实窗口样本评估摘要质量。
- 记录延迟、解析失败、误读和不确定场景。
- 根据样本结果调整 prompt、图片尺寸和候选问题格式。
- 保持单一模型主链路，避免多路线同时调试。
