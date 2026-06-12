# 确认弹窗（Confirm Modal）设计方案

## 决策记录

**选定方案**：`clarify` 工具调用方案（修改 hermes-agent）

**放弃方案**：纯文本标记 `[确认]...[/确认]` 方案

**原因**：
- 文本标记方案依赖 agent 自觉输出特定格式，不可靠
- `clarify` 是 hermes-agent 原生工具，agent 天然知道如何调用
- 工具调用有结构化参数（question + choices），解析零歧义
- Agent 调用工具时会阻塞等待回调，天然保证时序正确

## 架构总览

```
┌─────────────┐    ┌──────────────┐    ┌─────────────┐    ┌──────────┐
│  Frontend   │◄──►│  API Server  │◄──►│   Runner    │◄──►│  Agent   │
│  (Vue 3)    │    │  (FastAPI)   │    │ (async loop)│    │ (ACP)    │
└─────┬───────┘    └──────┬───────┘    └──────┬──────┘    └────┬─────┘
      │                   │                   │                │
      │   SSE stream      │   Redis pub/sub   │   ACP stdio    │
      └───────────────────┴───────────────────┴────────────────┘
                                      │
                                   Redis
                              (clarify_* keys)
```

## 数据流（完整链路）

```
1. 用户发送消息
   └─► API: POST /conversations/{id}/messages
       └─► Redis Stream: acp:prompt

2. Runner 消费消息
   └─► client.prompt(text) ──► ACP agent

3. Agent 分析消息，决定需要确认
   └─► 调用 clarify(question="...", choices=["A", "B", "C"])

4. clarify_callback 触发（agent 线程阻塞）
   ├─► Redis SET: hermes:clarify_pending:{session_id}
   ├─► Redis PUBLISH: hermes:clarify_notify:{session_id}
   └─► 阻塞等待 hermes:clarify_response:{session_id}:{clarify_id}

5. Runner on_update 检测到 tool_call (clarify)
   ├─► 读取 hermes:clarify_pending:{session_id}
   ├─► 发送 SSE: confirmation_request
   └─► 启动后台任务 _wait_and_unblock_clarify()

6. 前端收到 SSE
   └─► 显示 ConfirmModal（选项按钮/文本输入）

7. 用户选择/输入
   └─► POST /conversations/{id}/confirm
       ├─► Redis SET: confirm:{conv_id}:{clarify_id}
       └─► Redis PUBLISH: confirm_notify:{conv_id}

8. 后台任务收到 Redis 通知
   ├─► 发送 SSE: confirmation_response（前端关闭弹窗）
   ├─► Redis SET: hermes:clarify_response:{session_id}:{clarify_id}
   └─► Redis PUBLISH: hermes:clarify_notify:{session_id}

9. clarify_callback 解除阻塞，返回用户选择给 agent
   └─► Agent 继续处理（根据用户选择执行操作）

10. Agent 完成
    └─► Runner 发送 SSE: done
```

## 修改的文件

### hermes-agent（外部依赖）

| 文件 | 改动 |
|---|---|
| `toolsets.py` | `hermes-acp` toolset 加入 `"clarify"` |
| `acp_adapter/session.py` | 添加 `_acp_clarify_callback`，桥接到 Redis |

### hermes-python（本项目）

| 文件 | 改动 |
|---|---|
| `backend/app/services/conversation_service.py` | Prompt 引导 agent 调用 clarify 工具 |
| `backend/agent_runner/runner.py` | 检测 clarify tool_call，后台任务等用户回复 |
| `frontend/src/stores/chat.ts` | `respondConfirmation` 只调 API，不发新消息 |

## Redis Key 设计

| Key | 写入方 | 读取方 | 用途 |
|---|---|---|---|
| `hermes:clarify_pending:{session_id}` | clarify_callback | Runner | 存储待确认请求 |
| `hermes:clarify_response:{session_id}:{id}` | Runner | clarify_callback | 存储用户回复 |
| `hermes:clarify_notify:{session_id}` | 双方互相 | 双方互相 | pub/sub 通知 |
| `confirm:{conv_id}:{request_id}` | API | Runner | 用户回复存储 |
| `confirm_notify:{conv_id}` | API | Runner | pub/sub 通知 |

## 边界情况处理

| 情况 | 处理方式 |
|---|---|
| 用户 5 分钟不回复 | clarify_callback 超时返回 `{"choice": "超时"}`，agent 继续 |
| 用户点"跳过" | 返回 "跳过"，agent 继续 |
| Agent 不调用 clarify | regex fallback 检测文本中的问题（30s 超时） |
| Redis 连接断开 | clarify_callback 返回错误 JSON，agent 继续 |
| 多步骤确认 | 每次 clarify 调用独立阻塞，串行处理 |

## 已放弃的方案：文本标记 `[确认]...[/确认]`

**问题**：
- Agent 不一定遵守输出格式
- 流式 token 中标记可能被截断
- 需要在前端和后端同时做文本过滤
- 标记和正常文本可能冲突

**保留的代码**：
- `_extract_confirm_marker()` 方法仍可用于调试
- `_detect_clarification()` regex 仍作为 fallback
