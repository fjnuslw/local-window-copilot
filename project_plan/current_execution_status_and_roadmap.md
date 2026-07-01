# 当前落实状态与下一步路线

更新时间：2026-07-01

## 1. 当前结论

项目已经从“网页原型”推进到“真实 Windows 桌面悬浮窗切片”。

当前 MVP 主线不再把 Rive 或 Tauri 放在关键路径上：

```text
Python + Win32 原生悬浮窗
  -> FastAPI 本地后端
  -> llama.cpp 常驻模型服务
  -> MiniCPM-V 4.6 F16
  -> Redis 异步状态
  -> PostgreSQL 脱敏行为记录
```

Rive 已确认不适合作为当前主路线，因为 runtime export 被 workspace plan 阻塞。Tauri 仍可作为后续产品化封装备选，但当前最重要的是先把真实桌面悬浮窗、真实后端、真实模型链路串起来。

## 2. 已完成切片

### 2.1 模型运行时验证

已准备：

```text
runtime/models/minicpm-v4.6/MiniCPM-V-4_6-F16.gguf
runtime/models/minicpm-v4.6/mmproj-model-f16.gguf
runtime/llama.cpp/llama-server.exe
runtime/llama.cpp/llama-mtmd-cli.exe
```

已验证：

```text
llama-server 冷启动到 ready：约 11.62 秒
512px JPEG 热请求：约 2.40 秒
固定使用 F16 主模型和 F16 mmproj
正式 server 路线需要 --reasoning off
```

### 2.2 Mascot 素材与状态

已完成本地分层素材路线：

```text
assets/mascot/rive_import/mascot_base_idle.png
assets/mascot/rive_import/face_observing_overlay.png
assets/mascot/rive_import/face_analyzing_overlay.png
assets/mascot/rive_import/face_privacy_overlay.png
assets/mascot/rive_import/face_error_overlay.png
```

状态枚举：

```text
idle：待命
observing：观察窗口
analyzing：模型推理中
privacy：隐私保护 / 权限边界
error：异常
```

### 2.3 Web 原型

路径：

```text
apps/floating-window/
```

作用：

```text
验证视觉样式、状态切换、分层素材和交互布局
```

它不是最终产品，只作为视觉和交互试验台保留。

### 2.4 真实桌面悬浮窗

路径：

```text
apps/desktop-floating-window/
```

核心文件：

```text
desktop_floating_window.py
set_state.py
start_desktop_window.cmd
state_bridge.json
```

当前能力：

```text
真实 Windows 桌面窗口
无边框
始终置顶
per-pixel alpha 真透明
可拖动
点击 mascot 循环状态
toolbar 按钮切换状态
通过 state_bridge.json 接收外部状态
```

当前技术栈：

```text
Python 3
ctypes 调 Win32 API
UpdateLayeredWindow 实现 per-pixel alpha
Pillow 做 RGBA 合成与动画渲染
本地 PNG 分层素材
JSON 文件作为临时状态桥
```

### 2.5 FastAPI 状态中枢

路径：

```text
backend/
```

当前能力：

```text
uv 管理 Python 环境
FastAPI 服务运行在 127.0.0.1:18080
GET /health
GET /api/assistant/state
POST /api/assistant/state
GET /api/assistant/events
写入 apps/desktop-floating-window/state_bridge.json
```

已验证：

```text
uv run pytest：2 passed
POST /api/assistant/state 可驱动桌面悬浮窗状态
state_bridge.json 写入 source=fastapi
```

## 3. 当前主线架构

```text
Desktop Floating Window
  Python + Win32 + Pillow
  |
  | 状态事件：idle / observing / analyzing / privacy / error
  v
FastAPI Local Backend
  |
  +-- Window Capture Service
  +-- Privacy Filter
  +-- Context Builder
  +-- ModelRuntimeManager
  |
  +-- Redis task status / cache
  +-- PostgreSQL event log / analysis runs
  |
  v
llama.cpp server
  |
  v
MiniCPM-V 4.6 F16
```

## 4. 下一步最高优先级

FastAPI 状态服务已经完成。下一步不再继续优化动画工具链，而是接入真实窗口采集。

### Step 1：FastAPI 状态服务

目标：

```text
让桌面悬浮窗不再靠手动 set_state.py，而是监听 FastAPI 状态事件。
```

状态：已完成第一版。当前仍通过 `state_bridge.json` 兼容桌面悬浮窗，后续可替换为 SSE / WebSocket 直连。

建议接口：

```http
GET /health
GET /api/assistant/state
POST /api/assistant/state
GET /api/assistant/events
```

状态写入逻辑：

```text
开始截图 -> observing
开始模型推理 -> analyzing
命中敏感窗口 -> privacy
推理失败 / 后端异常 -> error
任务完成或空闲 -> idle
```

验收：

```text
curl 或 Python 请求后端接口，桌面悬浮窗能自动变状态。
```

### Step 2：真实窗口采集

目标：

```text
采集当前活动窗口标题、应用名、窗口截图。
```

优先技术：

```text
pywin32
mss
Pillow
uiautomation 可后置
```

验收：

```text
POST /api/window/capture
返回 app_name、window_title、window_bounds、screenshot_hash、screenshot_path
桌面悬浮窗显示 observing
```

### Step 3：接 llama.cpp server

目标：

```text
FastAPI 常驻管理 llama-server，并把窗口截图送入 MiniCPM-V。
```

必须实现：

```text
模型文件校验
server 启动 / health check
截图缩放到 512px JPEG
--reasoning off
结构化 JSON 输出解析
错误兜底
```

验收：

```text
POST /api/window/analyze
桌面悬浮窗从 observing -> analyzing -> idle
返回 summary、key_points、candidate_questions、caution
```

### Step 4：Redis 与 PostgreSQL

Redis 先做：

```text
任务状态
短期结果缓存
screenshot_hash 去重
SSE 进度事件
```

PostgreSQL 后做：

```text
analysis_runs
window_contexts
user_events
conversations
```

验收：

```text
能统计模型延迟、失败率、JSON 解析成功率、候选问题点击事件。
```

## 5. 不再投入的路线

当前阶段停止投入：

```text
Rive runtime export
Ollama 包装
PyTorch / Transformers 本地推理路线
云端 VLM
完整 Tauri 重构
复杂自动执行 Agent
```

保留为后续可选：

```text
Tauri：如果后期需要更完整设置页、系统托盘、自动更新和安装包，可重新评估。
Rive：如果未来能导出 .riv，可只替换视觉渲染层，不改变状态机和后端。
```

## 6. 面试表达更新

当前可以这样讲：

```text
我没有停留在网页 demo，而是先做了一个真实 Windows 桌面悬浮窗。这个窗口使用 Python 直接调用 Win32 layered window，通过 UpdateLayeredWindow 实现 per-pixel alpha 透明，Pillow 负责本地 PNG 分层合成和状态动画。

桌面壳通过统一的 assistant state contract 和后端解耦：observing 表示窗口采集，analyzing 表示模型推理，privacy 表示敏感窗口保护，error 表示后端或模型异常。下一步 FastAPI 会把窗口采集、隐私过滤和 MiniCPM-V 推理过程中的状态推送给桌面壳。

这样项目不是一个网页 UI，而是一个能接真实本地模型、真实窗口采集和后端状态机的桌面 AI 应用切片。
```

## 7. 当前验收清单

已完成：

- MiniCPM-V 4.6 F16 文件与 llama.cpp runtime 准备。
- llama-server 热请求速度验证。
- mascot 分层素材准备。
- Web 视觉原型。
- 真实 Windows 置顶透明悬浮窗。
- 桌面悬浮窗状态切换。
- Rive 阻塞原因记录并止损。

未完成：

- FastAPI 后端项目结构。
- 后端状态事件推送。
- 真实窗口采集 API。
- MiniCPM-V 推理 API 封装。
- Redis 异步任务。
- PostgreSQL 脱敏行为记录。
- 安装包 / 用户无感启动。
