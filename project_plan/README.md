# Local Window-Aware Agent Project Plan

这个文件夹用于保存项目计划、技术方案、阶段排期和面试展示材料。

当前文件：

- `interview_project_plan.md`：面向大模型应用实习面试的完整项目计划书。
- `module_breakdown_and_integration.md`：将庞大工程拆成多个可独立实现的小模块，并说明如何逐步串联成完整系统。
- `target_model_implementation_plan.md`：目标模型实现路线，直接按 MiniCPM-V 4.6 GGUF + llama.cpp 做，不先走 Ollama、PyTorch 或云端兼容接口。
- `invisible_deployment_plan.md`：用户无感部署方案，说明最终产品如何做到下载资源包、点击即用，而不是让用户配置 `.env` 或手动安装推理框架。
- `current_execution_status_and_roadmap.md`：当前已落实的真实桌面悬浮窗、技术栈选择、Rive/Tauri 路线调整和下一步执行清单。

当前执行原则：

- 不以 mock 或假数据作为主线。
- 不先做 Ollama、PyTorch、云端兼容接口等过渡路线。
- 直接按目标路线实现：`MiniCPM-V 4.6 GGUF + mmproj + 内置 llama.cpp runtime`。
- 当前 MVP 桌面壳固定为 `Python + Win32 layered window + Pillow`，先做真实桌面悬浮窗，再接 FastAPI 和模型链路。
- 第一阶段直接选择真实具体场景，例如 IDE / 终端报错、安装失败 / 权限错误窗口。
- 所有早期样本和代码都应能沉淀为最终产品能力或评估数据。
