# 历史草稿：模块拆解与集成计划

状态：已归档。

当前实现已收敛为更小的桌宠式窗口 Copilot 主链路：

```text
桌面悬浮窗
-> FastAPI
-> 窗口截图与 ObservationCard
-> MiniCPM-V 窗口分析
-> SQLite RuntimeStore 状态与短期记忆
-> 用户追问回答
```

新的模块边界以当前代码和 `application_agent_spec_zh.md` 为准。
