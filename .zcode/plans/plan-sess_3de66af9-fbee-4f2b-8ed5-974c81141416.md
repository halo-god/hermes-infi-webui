# ai-agent-book 四大方向实现计划

## 方向 1：智能工具管理（主动工具发现 + Skills 渐进式披露）

### 1a. 主动工具发现
**问题**：当前 `_resolve_mcp_servers` 把所有绑定的 MCP 工具 schema 全量传给 ACP session，token 膨胀。
**实现**：
- 后端 `_resolve_mcp_servers` 改为：先收集所有工具 schema，再用 pg_trgm 相似度匹配用户消息 → 只传 top-3-5 最相关工具
- 新增 `ToolRegistry` 缓存层（Redis），按 server name → tool list 映射
- 兜底：工具数 ≤ 5 时全量传（小量不优化）

### 1b. Agent Skills 渐进式披露
**问题**：SOP 技能定义（nodes_json）可能很大，全量注入 system_prompt 浪费 token。
**实现**：
- Profile 新增 `skills_catalog`（薄目录：skill_id + name + trigger_intents，不含完整 nodes/edges）
- 启动时只注入薄目录（~100 tokens/skill）
- 匹配到 SOP 后才加载完整 nodes/edges/instructions
- 前端 SOP 编辑器增加"目录预览"字段

## 方向 2：RAG 与安全增强

### 2a. 上下文感知检索
**问题**：当前 KnowledgeChunk 的 content 是原始文本，缺少 chunk 级上下文前缀。
**实现**：
- `chunk_html_by_headings` 改进：每个 chunk 自动生成上下文前缀（文档名 + heading_path + 前一段摘要）
- 检索时用 `content_with_context` 做相似度匹配（而非原始 content）
- 迁移不需要（复用 knowledge_chunks 表，content 字段改为包含上下文前缀的版本）

### 2b. 成本可观测 tracing
**问题**：当前 usage 只在 message 级别记录，缺少"哪一步最贵"的细粒度 tracing。
**实现**：
- 新增 `AgentTrace` 模型：conversation_id + message_id + step_index + event_type + tokens_in + tokens_out + duration_ms + cost
- runner.py 的 `on_update` 在每个 tool_call/thought 事件时写一条 trace
- Admin 用量看板新增 "Trace 详情" 视图：展示单条消息的逐步 token 消耗

### 2c. LLM 二次审批工具调用
**问题**：当前 `set_session_mode("dont_ask")` 全自动，危险 MCP 工具操作无审批。
**实现**：
- conversation_service 新增 `_should_approve_tool(tool_name, args)` 规则引擎
- 配置 `dangerous_tools` 列表（如 delete_*, drop_*, execute_shell 等）
- 命中危险工具时走 `confirmation_request` 流程让用户确认
- runner.py 的 `on_update tool_call` 分支增加审批检查

## 方向 3：高级 Agent 模式

### 3a. Artifact 模式
**问题**：当前 AI 回复包含代码/SQL 时，用户需要手动复制执行。
**实现**：
- 新增 `Artifact` 模型：conversation_id + message_id + type(sql/code/json) + content + status(draft/executed) + result
- runner.py 检测 AI 回复中的代码块（```sql / ```python 等），自动创建 Artifact
- 前端消息渲染：代码块旁加"执行"按钮
- 执行结果回填到 Artifact + 消息追加

### 3b. 阶段化系统提示
**问题**：当前 Profile.system_prompt 是静态的，不随任务阶段变化。
**实现**：
- Profile 新增 `stage_prompts` JSONB：`{requirement: "...", implementation: "...", review: "..."}`
- dispatch 根据消息特征判断当前阶段（首次消息=需求澄清，含代码=实现，含"检查"=审查）
- 注入对应阶段的 system_prompt + 工具集
- 前端 Profile 编辑表单新增"阶段提示"编辑区

### 3c. Proposer-Reviewer 双 Agent
**问题**：当前圆桌模式是并行作答+合并，没有"生成+检查迭代"模式。
**实现**：
- 新增 dispatch 模式 `"review"`：先走 proposer agent 生成，再用 reviewer agent（带 Vision 或代码审查能力）检查
- reviewer 的反馈自动追加为 proposer 的第二轮输入
- 最多迭代 N 轮或 reviewer 通过

## 方向 4：异步 Agent 架构（Flux 式）

### 4a. 事件队列 + 优先级分派
**问题**：当前 runner 的 `handle_single` 是同步阻塞的 ReAct 循环，无法中途打断或并行。
**实现**：
- 新增 `AgentInbox` 模型：conversation_id + event_type(user_message/cancel/tool_result/timer) + priority(interrupt/immediate/queue) + payload
- runner 改为事件循环：从 inbox 取事件 → 按优先级处理
- `interrupt` 级别（如用户发"停止"）：立即中断当前 turn
- `immediate` 级别（如用户追问）：暂停当前 turn，处理新消息
- `queue` 级别（如定时任务结果）：排队等待

### 4b. 并行工具执行
**问题**：当前 ACP agent 串行调用工具。
**实现**：
- runner 的 `on_update tool_call` 检测到多个独立工具调用时，用 `asyncio.gather` 并行执行
- 工具结果合并后一次性返回给 agent

## 文件清单（预估 ~20 文件新建/修改）

**新建**：
- `backend/app/db/models/trace.py`（AgentTrace 模型）
- `backend/app/db/models/artifact.py`（Artifact 模型）
- `backend/app/db/models/inbox.py`（AgentInbox 模型）
- `backend/app/services/tool_discovery_service.py`（主动工具发现）
- `backend/app/services/artifact_service.py`（Artifact 执行）
- `backend/app/services/stage_service.py`（阶段化提示）
- `backend/alembic/versions/0061_*.py` ~ `0064_*.py`
- `frontend/src/api/artifacts.ts`
- `frontend/src/api/traces.ts`

**修改**：
- `backend/app/services/conversation_service.py`（工具发现/阶段提示/审批检查/Artifact 检测）
- `backend/app/core/files.py`（chunk 上下文前缀改进）
- `backend/agent_runner/runner.py`（trace 写入/审批检查/并行工具/事件队列）
- `backend/app/db/models/agent.py`（Profile 加 stage_prompts/skills_catalog）
- `frontend/src/views/AdminView.vue`（用量看板 trace 视图/阶段提示编辑器）
- `frontend/src/views/ChatView.vue`（Artifact 执行按钮）