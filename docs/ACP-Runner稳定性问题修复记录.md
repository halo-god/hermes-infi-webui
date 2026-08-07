# ACP Runner 稳定性问题修复记录

> 日期：2026-08-07
> 范围：Agent Runner（`backend/agent_runner/`）与 hermes-agent ACP 适配器之间的通信链路稳定性修复。

## 背景

Agent Runner 通过 ACP（Agent Client Protocol v1）驱动 `hermes acp` 子进程。两者之间是 **stdio + JSON-RPC** 通信：runner 发请求（`session/prompt` 等），agent 通过 `session/update` 通知流式回传输出。

排查中发现多个稳定性问题，均源于 **ACP 适配器（`hermes acp`）与 CLI/gateway 运行时能力的差异**：agent 端大量功能依赖进程内消息泵（completion queue drain、事件循环），而 ACP 模式是单请求-响应驱动，这些机制不存在或未接线。

## 问题清单

### 问题一：session/resume 历史重放污染当前回复

**现象**：agent 子进程重启后的第一轮回复，携带上一轮的全部旧内容（实测 2349+ 字符的过期调研叙述拼接在新回复前面）。

**根因**：
- hermes-agent 在 `session/load` / `session/resume` 时调用 `_replay_session_history`（`acp_adapter/server.py`），把持久化的完整 transcript 作为 `session/update` 通知重放给客户端 —— 这是 ACP 规范行为。
- 重放的 chunk 与实时输出**格式完全相同**（都是 `agent_message_chunk`），无任何区分标记。
- runner 的 `_dispatch` 无条件转发 `session/update` 给 `on_update`，runner 的 `on_update` 把重放内容累积进当前消息的 `acc["text"]`。

**修复**（`0c7a2c1`）：`ACPClient` 新增 `_suppress_updates` —— `session/load` / `session/resume` 请求窗口内丢弃 `session/update` 通知（重放是历史，不是新输出）；`finally` 保证异常时窗口也关闭，不泄漏到后续 prompt 流。

**验证**：重启 runner 后发"只回复收到测试四个字"，回复仅 4 字符，无旧内容。新增 2 个单测（重放被抑制 + 异常后标志复位）。

### 问题二：大帧超限导致读循环崩溃

**现象**：agent 一次输出大批 token（deepseek-v4-flash 一次 7179 tokens）时，单条 JSON-RPC 帧超过 StreamReader 默认 64KiB 行上限，`readline()` 抛 `ValueError`，读循环崩溃 → `subprocess closed` → 整轮失败（消息卡 streaming，用户看到"生成中"永远不结束）。

**根因**：`_read_loop` 只捕获了 `asyncio.CancelledError`，`ValueError` 直接冒泡；`finally` 里把所有 in-flight 请求置为 "subprocess closed" 错误。

**修复**（`802e1f8`）：
- `create_subprocess_exec(limit=16 * 1024 * 1024)` 加大行上限；
- `_read_loop` 捕获 `ValueError` 跳过超限帧（CPython 在 limit 检查前已消费超限字节，流位置有效，`continue` 安全）。

**验证**：重新生成原崩溃轮次，turn 完整完成（1374 字符交付），无 ValueError/closed 错误。

### 问题三：stuck streaming 回收崩溃

**现象**：runner 重启后，崩溃遗留的 `streaming` 消息永远卡住（UI 显示"生成中"），因为启动回收逻辑本身抛异常。

**根因**：`_reclaim_stuck_streaming` 使用 `func.jsonb_set(..., create_missing=True)` —— 该参数在项目所用 SQLAlchemy 版本（2.0.50）的 generic 函数路径上不接受，启动即崩溃，回收从未执行过。

**修复**（`802e1f8`）：改为逐条 Python 读取-更新（残留 streaming 消息极少，循环开销可忽略），避免 JSONB 表达式构造的版本兼容问题。

**验证**：重启 runner，日志出现 `Reclaimed 1 stuck streaming message(s)`，UI 正确显示"⚠ 生成中断 + 重新生成"。

### 问题四：delegate_task 提前结束，子代理结果永不回灌

**现象**：调研类任务（派 3 个并行子代理）回复 350-686 字符的中间状态就 complete —— "还没结果就结束了"。子代理在后台正常跑、文件都写好了，结果却永远回不来。

**根因**（两层）：
1. `delegate_task(background=true)` 的契约：子代理完成后结果 push 到 `process_registry.completion_queue`，由 **CLI（process_loop）或 gateway（`_run_process_watcher`）的队列 drain** 消费并伪造新 turn 回灌。`hermes acp` 适配器**没有这个 drain** → 结果永不投递。
2. 修复 1（prompt 入口 `declare_stateless_channel()`）被覆盖：`_run_agent` 里 `set_session_vars()` 的 `async_delivery` 参数默认 `True`，把 ContextVar 覆盖回 True → `async_delivery_supported()` 仍返回 True → delegate 仍走 background。

**修复**（agent 端 `acp_adapter/server.py`）：
- `_run_agent` 里 `set_session_vars(..., async_delivery=False)` —— 在会话上下文建立处声明 stateless channel；
- 保留 prompt 入口的 `declare_stateless_channel()` 作为其它路径的兜底。

`async_delivery=False` 使 `delegate_task` 回退**同步执行**：子代理结果在 turn 内返回，agent 继续整合输出完整报告。

**验证**：重新生成后日志 `Dispatched async delegation` 出现次数 = 0；回复 complete（1374 字符）且包含完整交付（报告路径 + 7 条核心结论 + 投资视角 + 风险提示）；17 个工具调用（turn 内同步读取 3 份子代理产出并整合）。

## 关键链路图

```
runner (FastAPI 进程)                    hermes acp (子进程)
┌──────────────────────┐                ┌──────────────────────────┐
│ ACPClient._read_loop │  ←stdin/stdout→│ acp_adapter/server.py    │
│  - _suppress_updates │                │  - session/resume 重放   │
│  - limit 16MiB       │  session/update│  - set_session_vars(     │
│  - ValueError 跳过   │◄───────────────│      async_delivery=False)│
│ on_update → acc.text │                │  - delegate 同步执行      │
└──────────────────────┘                └──────────────────────────┘
```

## 已知遗留

- **write_file 工具返回 "Result unavailable"**：agent 端 write_file 工具在 ACP 通道下返回结果不可用（本日报告均靠 `execute_code` 的 python 写文件落盘）。独立问题，未修复，建议后续排查 ACP 工具桥（`acp_adapter/tools.py`）的 write_file 执行路径。

## 验证速查

```bash
# 单元测试（ACP client + 事件修复）
cd backend && .venv/bin/pytest tests/test_acp_client.py tests/test_acp_event_fixes.py

# runner 日志（确认无崩溃 / 回收生效）
grep -i "reclaim\|ValueError\|subprocess closed" /tmp/runner.log

# 确认 delegate 未走 background（应无输出）
grep -c "Dispatched async delegation" /tmp/runner.log

# 重启 runner 的完整流程（SIGKILL 不释放 Redis 锁，需手动清）
pkill -9 -f "python -m agent_runner.runner"
redis-cli -p 1979 -a "<password>" DEL hermes:runner:lock
cd backend && nohup .venv/bin/python -m agent_runner.runner > /tmp/runner.log 2>&1 &
```

## 相关提交

| 提交 | 内容 |
|---|---|
| `0c7a2c1` | fix(runner): 抑制 session/resume 期间的历史重放 |
| `802e1f8` | fix(runner): 大帧不崩读循环 + stuck streaming 回收修复 |

agent 端改动（`declare_stateless_channel` / `async_delivery=False`）位于 hermes-agent 目录，不在本仓库。
