# 本地 RuntimeStore 策略

更新时间：2026-07-03

## 结论

本地桌宠主打轻量，即插即用版本只使用随应用一起运行的本地能力。

当前主链路：

```text
FastAPI backend
-> SQLite RuntimeStore
-> llama.cpp / MiniCPM-V
-> Windows 悬浮窗
```

## 存储边界

`RuntimeStore` 保存运行时状态和短期数据：

- 助手状态
- 最近窗口分析
- 当前对话缓冲
- 最近历史对话
- working observation
- session memory

默认文件：

```text
backend/data/runtime/runtime.sqlite3
```

## 设计原则

- 不把本地运行时状态拆到额外基础设施。
- 不做隐式降级链。
- 本地文件不可写时直接报错。
- 长期人格记忆、账号同步、团队后台都不进入当前桌宠主链路。
