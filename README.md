# Local Window Copilot

Windows 本地陪伴式桌面伙伴。

它安静地在桌面上存在，理解用户的工作节奏，能被随时唤起。用户想互动时立刻进入对话状态，用户想分析时再调用视觉、历史和记忆认真拆解。截图、VLM、摘要、记忆是后台感知器官，不是产品本体。

产品原则（见 [ambient_companion_product_spec_zh.md](project_plan/ambient_companion_product_spec_zh.md)）：

```text
默认陪伴，不默认汇报
默认沉默，不默认分析
用户邀请时，才认真看、认真想、认真回应
不自动点击、不自动输入、不自动执行电脑操作
不要求 Redis / PostgreSQL / Docker
```

## 三层产品结构

```text
Presence Layer    存在层：桌宠呼吸、表情、姿态，不展示大段文字
Companion Layer   陪伴层：情绪回应、对话陪伴、记忆和人格一致性
Work Lens         工作透镜：用户邀请时调用视觉和上下文做认真分析
```

## Agent 工具层

```text
模型可见工具：
screen.look       看当前/最近屏幕，内部调用截图索引、局部裁剪和 VLM
memory.search     查 profile、短期记忆、最近对话和屏幕索引
memory.remember   只在用户明确要求记住时写入本地记忆

后端 provider：
current_screen / screen_history / vision.inspect / profile.md / runtime memory / conversation history
```

## 主链路

```text
Windows 桌面悬浮窗
-> FastAPI backend
-> SQLite RuntimeStore
-> llama.cpp server
-> MiniCPM-V 视觉语言模型
-> 桌宠存在感面板（入口按钮，不展示摘要）
-> 独立悬浮对话窗（陪伴回应 / 工具编排）
```

`RuntimeStore` 是本地 SQLite 文件，默认路径：

```text
backend/data/runtime/runtime.sqlite3
```

它保存助手状态、最近窗口分析、当前对话、历史对话、短期会话记忆、用户最近目标与困惑。普通用户不需要安装额外服务。

## 启动

```powershell
cd D:\AI_Workspace\window
.\scripts\start_dev.cmd
```

启动前检查：

```powershell
python .\scripts\check_environment.py --for-start
```

手动启动后端：

```powershell
cd D:\AI_Workspace\window\backend
uv run uvicorn app.main:app --host 127.0.0.1 --port 18080 --reload --no-access-log
```

手动启动悬浮窗：

```powershell
cd D:\AI_Workspace\window
.\apps\desktop-floating-window\start_desktop_window.cmd
```

WebUI 控制台：

```text
http://127.0.0.1:18080/webui/
```

## 接口

```text
GET  /health
GET  /api/assistant/state
POST /api/assistant/state
GET  /api/assistant/latest
POST /api/assistant/questions
GET  /api/assistant/conversation
GET  /api/assistant/conversations
POST /api/assistant/conversations/clear
GET  /api/assistant/context-preview
POST /api/assistant/resume
GET  /api/webui/config
PUT  /api/webui/config
POST /api/webui/reload
GET  /api/webui/window-summaries
POST /api/webui/window-summaries/clear
POST /api/window/capture
POST /api/window/watch/start
POST /api/window/watch/stop
GET  /api/window/watch/status
```

## 代码入口

```text
backend/app/main.py
backend/app/services/runtime_store.py
backend/app/services/window_capture.py
backend/app/services/window_watcher.py
backend/app/services/window_analysis.py
backend/app/services/observation_builder.py
backend/app/services/assistant_chat.py          # 对话 agent 会话、历史、状态入口
backend/app/services/agent_orchestrator.py      # Hermes-like 工具规划与回答编排
backend/app/services/agent_tools.py             # 三个模型可见工具 + 后端 provider
backend/app/services/situation_builder.py       # 情境状态构建器（spec §8.2）
backend/app/services/interaction_policy.py      # 主动提示策略（spec §8.3）
backend/app/services/screenshot_crop.py         # 局部截图裁剪（spec §9 Phase 4）
backend/app/services/vision_model_client.py     # VLM 客户端 + 分层 messages 构建
backend/app/services/profile_store.py           # profile md 管理
backend/app/services/memory.py
apps/desktop-floating-window/desktop_floating_window.py
experiments/prompts/companion_chat_v1.txt       # 陪伴模式 prompt
experiments/prompts/visual_question_answer_v1.txt
```

## 测试

```powershell
cd D:\AI_Workspace\window\backend
uv run pytest --basetemp D:\AI_Workspace\window\.tmp\pytest-basetemp
```
