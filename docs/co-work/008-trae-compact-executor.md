# 协作任务 008：CompactExecutor 最小闭环

更新时间：2026-07-06

## 目标

008 把 005-007 的准备件接成一次可审计 compact 执行：

- 读取 `RollingSummaryState`。
- 使用 `CompactPlanner` 生成 plan。
- 获取 `compact_lock`。
- 使用注入式 model client 生成 summary。
- 校验 summary schema 与预算。
- 成功时写入 rolling summary 与 metrics。
- 出错时保留原 summary 内容，写入 error metrics。
- 最后释放 lock。

## 范围

本任务只扩展 `backend/app/services/context_summary.py` 并新增 executor 单测。

新增对象：

- `CompactModelClient`：最小模型客户端协议，只要求 `complete_chat(...) -> str`。
- `CompactExecutionResult`：执行结果账本。
- `execute_compact(...)`：plan、lock、model、validate、commit 的单次执行入口。
- `compact_execution_result_to_dict(...)`：JSON 可序列化输出。

## 禁止范围

- 不接入 `_append_history()`。
- 不启动后台线程。
- 不注册 Agent tool。
- 不改 `VisionModelClient`。
- 不改 `build_chat_messages()`。
- 不新增 WebUI API。
- 不引入真实模型依赖，测试使用假 model client。

## 执行顺序

```text
1. load_summary
2. plan_compact
3. plan 未触发 -> skipped result
4. acquire_lock
5. lock 占用 -> locked result
6. build_compact_summary_prompt
7. model_client.complete_chat
8. build_compact_success_state
9. save_summary
10. save_success_metrics
11. release_lock
```

错误路径：

```text
1. 捕获模型调用或 summary 校验错误
2. 写 save_error_metrics
3. summary 内容与 covered_session_ids 保持原状
4. release_lock
5. 返回 error result
```

## 测试覆盖

新增 `backend/tests/test_context_compact_executor.py`：

- 成功 compact 写 summary、metrics，并释放 lock。
- 未触发时跳过，不获取 lock，不调用 model。
- active lock 存在时返回 locked，不调用 model。
- 模型错误写 error metrics，原 summary 保持原状。
- 非法 summary 写 error metrics，不提交 summary。
- 手动触发无 source sessions 时返回 skipped。
- 非法 source 在模型调用前抛出。
- execution result 可 JSON 序列化。

## 当前结果

命令：

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider --basetemp=..\test-tmp\pytest tests\test_context_summary_state.py tests\test_context_compact_planner.py tests\test_context_compact_summarizer_prompt.py tests\test_context_compact_executor.py -q
```

结果：`139 passed in 0.50s`。

## 下一步

009 接入触发面：

- 自动触发：`_append_history()` 和 FTS5 写入完成后调用 executor。
- 手动触发：新增调试接口或 WebUI 按钮。
- trace：写入 `context_summary.started/succeeded/failed`。
- 主聊天链路继续使用旧 summary 与 raw tail，compact 成功后下一轮读取新 summary。