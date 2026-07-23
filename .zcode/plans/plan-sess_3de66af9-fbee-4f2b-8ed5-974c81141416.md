# P1 + P2 完整实现计划

## P1-A：usage/context 状态持久化与恢复

### 后端
1. **`runner.py _finalize()`**：在写入 content 时增加 `content["usage"] = {input_tokens, output_tokens, context_size, context_used}`；同时写 `msg.tokens_in` / `msg.tokens_out` 列
2. **`runner.py on_update` usage 分支**：把 usage 数据累积到 `acc["usage"]`（当前只推 SSE 不存内存）
3. **`schemas/conversation.py MessageOut`**：加 `usage?: dict`、`tokens_in: int`、`tokens_out: int` 字段
4. **`api/v1/conversations.py get_conversation / get_messages_page`**：确保 `tokens_in`/`tokens_out` 在序列化中返回

### 前端
5. **`chat.ts openConversation`**：映射时从最后一条 agent message 的 `content.usage`（或 `m.usage`）恢复 `contextTokens` / `contextSize`
6. **`chat.ts loadMoreMessages`**：补上 `steps`/`thinking`/`plan` 的 content 映射（当前完全缺失，向上翻页老消息丢失状态）
7. **`chatStream.ts usage handler`**：把 usage 也写入当前 streaming message 的 `content.usage`（为 finalize 后的恢复提供一致数据）

## P1-B：子 Agent 转录富事件转发

### 后端
8. **`runner_subagent.py _run_turn on_update`**：从只处理 `agent_message_chunk` 扩展为也处理 `tool_call`/`agent_thought`/`plan`/`usage`，转发对应 SSE 事件（`tool_call`/`thought`/`plan`/`usage`）
9. **`runner_subagent.py _finalize_message`**：从只写 `content={"text": text}` 扩展为也写 `content["tool_calls"]`/`content["thinking"]`/`content["plan"]`
10. **`runner_subagent.py _run_turn`**：累积 `acc["steps"]`/`acc["thinking"]`/`acc["plan"]` 内存状态

### 前端
11. **`SubagentPanel.vue` 转录渲染**：从只读 `content.text` 扩展为渲染 steps（工具调用步骤）、thinking（思考过程）、plan（执行计划）
12. **`chatStream.ts`**：子 Agent 的 SSE 事件（`tool_call`/`thought`/`plan`/`usage`）已有 handler，但当前只在主会话生效；确认 subagent 的子会话事件流也走同一 handler

## P1-C：子 Agent 面板增强

### 后端
13. **`schemas/subagent.py SubagentOut`**：加 `last_snippet: str | None`（最近 agent 回复前 100 字）、`step_count: int`（已完成工具调用数）
14. **`api/v1/conversations.py _subagent_out`**：查询子会话最近一条 agent message 的 text snippet + content.tool_calls 长度

### 前端
15. **`SubagentPanel.vue`**：列表项显示 snippet + step_count + 状态 pill；展开后完整转录含 steps/thinking/plan

## P2-A：后端用量查询端点

16. **新增 `api/v1/admin.py GET /admin/usage`**：支持 `period`(month/week) + `breakdown`(user/profile/model) 参数
17. **聚合查询**：`Message.tokens_in + tokens_out`，按 `owner_id` / `profile_id`(JOIN Profile.default_model) / `created_at` 分组
18. **返回结构**：`{total_tokens_in, total_tokens_out, total_cost, by_dimension: [{key, tokens_in, tokens_out, cost, count}], daily: [{date, tokens_in, tokens_out}]}`

## P2-B：模型单价配置

19. **`SystemSettings.data` 增加 `model_pricing` 节**：`{model_id: {input_per_1k: float, output_per_1k: float}}`
20. **AdminView 系统设置 tab**：增加模型单价编辑区
21. **后端用量查询**：按 JOIN 出的 model 匹配单价计算成本

## P2-C：前端用量看板 tab

22. **`AdminView.vue`**：新增 `"usage"` tab
23. **新建 `AdminUsage.vue` 组件**（或内联在 AdminView）：
    - 顶部：本月 token 总量 + 成本 + 配额进度条
    - 中部：按助手/用户/模型的三维 breakdown 表格（tab 切换）
    - 底部：每日趋势图（简单 bar chart，用 CSS 而非引入图表库）
24. **`api/admin.ts`**：新增 `getUsage(params)` 封装

## 验证
- `ruff check .` 通过
- `npm run type-check` + `npm run build` 通过
- 手动验证：发消息后切换会话再回来，context 环不丢失；子 Agent 面板显示 steps；Admin 用量 tab 有数据