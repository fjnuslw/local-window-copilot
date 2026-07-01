# 目标模型实现计划

## 1. 路线定版

本项目不再先走 Ollama、PyTorch、Transformers、云端兼容接口等过渡方案。

目标路线直接锁定为：

```text
MiniCPM-V 4.6 GGUF
  + mmproj-MiniCPM-V-4.6-F16.gguf
  + 内置 llama.cpp runtime
  + FastAPI ModelRuntimeManager
  + 桌面主进程 / 后端 RuntimeManager 管理进程
```

原因：

```text
这条路线最接近最终用户体验
最符合 local-first 和隐私优先定位
不会因为临时探索路线造成后续重构
可以从第一天开始沉淀最终产品代码
```

## 2. 模型文件

目标模型资源包包含：

```text
MiniCPM-V-4_6-F16.gguf
mmproj-model-f16.gguf
model_manifest.json
```

固定使用 F16：

```text
主模型 F16
视觉投影器 F16
面向 16GB 显存开发与演示机器
优先保证模型效果和窗口 OCR / 视觉理解质量
```

当前阶段不做：

```text
Thinking 版本
```

MVP 只认一个默认模型：

```text
MiniCPM-V-4_6-F16.gguf
```

## 3. 运行时文件

目标运行时：

```text
llama.cpp b9049 或之后版本
```

需要打包的可执行文件：

```text
llama-server.exe
llama-mtmd-cli.exe
```

优先顺序：

```text
1. 先用 llama-mtmd-cli 跑通单张窗口截图。
2. 再封装为 FastAPI 调用的本地子进程。
3. 最后切换到常驻 llama-server，减少重复加载模型。
```

这不是更换技术路线，而是同一条 llama.cpp 路线的三个落地阶段。

## 4. 第一阶段目标

第一阶段不做宽泛实验，只做一个真实场景：

```text
IDE / 终端报错窗口
```

第一阶段目标链路：

```text
真实窗口截图
  → 保存为 current_window.png
  → llama-mtmd-cli + MiniCPM-V 4.6 GGUF
  → analyze_window_v1 prompt
  → 输出严格 JSON
  → 保存 raw output 和 parsed JSON
```

完成后再接入 FastAPI。

## 5. 第一天行动

只做这些：

```text
1. 创建 runtime/models/minicpm-v4.6/ 目录。
2. 下载或放入 MiniCPM-V-4_6-F16.gguf。
3. 下载或放入 mmproj-model-f16.gguf。
4. 准备 llama.cpp 可执行文件。
5. 准备 3 到 5 张 IDE / 终端报错截图。
6. 编写 analyze_window_v1.txt。
7. 使用 llama-mtmd-cli 跑通单张图片。
8. 记录输出 JSON、耗时和失败原因。
```

第一天不做：

```text
Ollama
PyTorch
Transformers
云端 API
Redis
PostgreSQL
Tauri
```

## 6. Prompt v1

```text
你是一个本地窗口感知助手。你只能分析当前窗口内容并生成文字反馈，不能执行任何系统操作。

请根据窗口截图完成以下任务：

1. 判断窗口类型，只能从以下类型中选择：
   error_dialog, form, document, webpage, ide, settings, installer, chat, file_explorer, unknown
2. 用一句话总结当前窗口。
3. 提取 3 到 5 个关键点。
4. 生成 3 到 5 个用户可能想问的候选问题。
5. 如果涉及密码、支付、账号、删除、安装、权限变更、私钥、验证码等风险，给出 caution。

要求：
- 输出必须是严格 JSON。
- 不要输出 Markdown。
- 不要输出解释性前后缀。
- 不要声称你会点击、输入、提交或操作电脑。
- 只能给建议，不能执行动作。
- 如果信息不足，请明确说明不确定。
- 用中文输出。

输出格式：
{
  "window_type": "...",
  "summary": "...",
  "key_points": ["..."],
  "candidate_questions": [
    {
      "question": "...",
      "category": "...",
      "reason": "...",
      "priority": 0.9
    }
  ],
  "caution": "..."
}
```

## 7. llama.cpp 命令形态

MiniCPM-V 4.6 的官方使用说明和 llama.cpp Cookbook 对 Instruct 版本有一个关键要求：关闭 thinking/reasoning，以免结构化输出被思考内容污染。

```text
llama-server:
  --reasoning off

vLLM:
  --default-chat-template-kwargs '{"enable_thinking": false}'
```

本项目最终使用 `llama-server` 常驻服务。`llama-mtmd-cli` 只用于早期单图命令行验证，因为本地 b9846 的 `llama-mtmd-cli` 不接受 `--reasoning` / `-rea` 参数，而 `llama-server` 支持。

目标 server 命令形态：

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

单图 CLI 验证命令形态：

```text
llama-mtmd-cli.exe
  -m runtime/models/minicpm-v4.6/MiniCPM-V-4_6-F16.gguf
  --mmproj runtime/models/minicpm-v4.6/mmproj-model-f16.gguf
  -c 8192
  --image experiments/target_model_samples/ide_errors/current_window.png
  -p "<analyze_window_v1 prompt>"
```

注意：CLI 验证输出可能带 `<think></think>`，FastAPI 解析层需要清洗；正式服务端用 `--reasoning off`。

## 7.1 server 请求方式

FastAPI 后续通过 OpenAI-compatible `/v1/chat/completions` 调用本地 `llama-server`。

请求内容：

```json
{
  "model": "minicpm-v4.6-f16",
  "temperature": 0.1,
  "max_tokens": 512,
  "messages": [
    {
      "role": "user",
      "content": [
        {"type": "text", "text": "<analyze_window_v1 prompt>"},
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

图片输入策略：

```text
1. FastAPI 截取窗口原图。
2. 进入模型前压缩为 JPEG。
3. 默认最长边先限制到 512px。
4. 后续按 OCR 效果再调整到 768px 或 1024px。
5. 使用 UTF-8 JSON 请求体，避免中文 prompt 编码问题。
```

原因：

```text
原始 1672x941 PNG 通过 server base64 接口会触发 413 Payload Too Large。
缩放到 512x288、JPEG 约 28KB 后，server 请求成功。
```

## 7.2 当前实测结果

测试图片：

```text
D:/Downloads/ChatGPT Image 2026年6月25日 18_38_50.png
原始尺寸：1672x941
原始大小：约 1.29MB
server 输入：512x288 JPEG，约 28KB
```

实测结果：

```text
llama-mtmd-cli 冷启动单图：约 106.9 秒
llama-server 冷启动到 /health ready：约 11.62 秒
llama-server 热请求 512px 图：约 2.40 秒
```

结论：

```text
命令行 CLI 适合验证，不适合产品请求路径。
最终产品应使用 llama-server 常驻服务。
FastAPI 必须做图片缩放、UTF-8 请求和 JSON 输出提取。
```

## 8. 文件结构

```text
runtime/
  llama.cpp/
    llama-server.exe
    llama-mtmd-cli.exe
  models/
    minicpm-v4.6/
      MiniCPM-V-4_6-F16.gguf
      mmproj-model-f16.gguf
      model_manifest.json

experiments/
  target_model_samples/
    ide_errors/
    installer_errors/
    forms_settings/
  prompts/
    analyze_window_v1.txt
  outputs/
    raw/
    parsed/
  reports/
    target_model_report.md
```

## 9. 成功标准

第一阶段成功标准：

```text
MiniCPM-V 4.6 GGUF 能被 llama.cpp 成功加载
能输入窗口截图
能输出中文分析结果
至少 3 张 IDE / 终端报错截图输出可解析 JSON
单次推理耗时被记录
失败原因被记录
```

进入 FastAPI 的条件：

```text
命令行调用已经稳定
模型文件路径和参数固定
prompt v1 可复用
JSON 解析策略明确
```

## 10. 后续接入 FastAPI

命令行跑通后，直接封装：

```text
ModelRuntimeManager
  +-- verify_model_files()
  +-- build_llamacpp_command()
  +-- run_single_image()
  +-- parse_output_json()
  +-- collect_latency()
```

FastAPI 第一版接口：

```text
POST /api/window/analyze-image
```

输入：

```json
{
  "image_path": "experiments/target_model_samples/ide_errors/current_window.png"
}
```

输出：

```json
{
  "window_type": "ide",
  "summary": "...",
  "key_points": ["..."],
  "candidate_questions": [],
  "caution": null,
  "latency_ms": 6200
}
```

然后再接入真实窗口采集。

## 11. 明确不走的路线

当前阶段不做：

```text
Ollama 路线
PyTorch / Transformers 路线
OpenAI-compatible 临时接口
云端 VLM
多模型适配层
模型微调
```

这些不是现在的主线。主线只有一条：

```text
MiniCPM-V 4.6 GGUF + llama.cpp + 用户无感本地部署
```
