# 数字员工管理平台改造计划

## 改造原则
- **Profile 模型不重命名**（避免全局 rename 风险），但在前端和 API 文档中全面使用"数字员工"语义
- **后端扩展 Profile 模型**：新增 employee 相关字段
- **前端全面改造**：AdminView 助手管理 -> 数字员工花名册（卡片式），ChatView landing -> 员工选择

## 第一部分：后端扩展

### 1. Profile 模型新增字段
文件：`backend/app/db/models/agent.py`

新增字段：
- `employee_no: str | None` -- 员工工号（可选，不填则用 handle）
- `department: str | None` -- 所属部门（文本，不建部门表，保持轻量）
- `position: str | None` -- 岗位名称（与 desc 互补：desc 是简介，position 是正式岗位名）
- `employee_status: str` -- 在职状态（active/archived/leave，默认 active，与 is_active 互补：is_active 是技术开关，employee_status 是 HR 状态）
- `hired_at: datetime | None` -- 入职时间（默认用 created_at）

### 2. 新增 WorkRecord 模型 + 迁移
文件：`backend/app/db/models/agent.py` 新增 `EmployeeWorkRecord` 表

字段：
- `id` (UUID PK)
- `profile_id` (FK -> profiles.id, CASCADE)
- `conversation_id` (FK -> conversations.id, SET NULL)
- `message_id` (FK -> messages.id, SET NULL)
- `event_type: str` -- 事件类型（chat/task/skill/tool/knowledge）
- `summary: str` -- 事件摘要（如"处理了用户关于XX的咨询"）
- `tokens_used: int` -- 本次消耗 token
- `duration_ms: int | None` -- 响应耗时
- `feedback: str | None` -- 用户反馈（positive/negative/none）
- `created_at` (Timestamps)

**触发时机**：在 `runner.py _finalize()` 完成后，写一条 WorkRecord（从 message 的 usage + status 提取）

### 3. 迁移文件 `0055_employee_work_records.py`
- Profile 加 5 个新列
- 新建 employee_work_records 表 + 索引 (profile_id, created_at)

### 4. Profile Schema 扩展
文件：`backend/app/schemas/agent.py`
- `ProfileOut` 加 employee_no/department/position/employee_status/hired_at
- `ProfileCreate` / `ProfileUpdate` 同步加这些字段

### 5. 新增 API 端点
文件：`backend/app/api/v1/agents.py`

- `GET /profiles/{id}/work-records` -- 查询某员工的工作记录（支持 limit/offset）
- `GET /profiles/{id}/performance` -- 查询某员工的绩效统计（总消息数、总 token、positive/negative 反馈数、平均耗时、近 7 天每日统计）

### 6. runner.py 写 WorkRecord
在 `_finalize()` 之后，新增 `_record_work()` 方法：
- 从 `acc["usage"]` 提取 tokens
- 从 `message_id` 关联的 conversation 提取信息
- 从消息 reactions 提取反馈（👍=positive, 👎=negative）
- 写入 EmployeeWorkRecord

## 第二部分：前端全面改造

### 7. 语义替换（全前端）
将所有"助手"文案替换为"数字员工"：
- `frontend/src/i18n/locales/zh-CN.ts` 和 `en.ts`：nav.assistants -> "数字员工"，nav.newAssistant -> "新建员工"等
- `AdminView.vue`：tab label "助手管理" -> "数字员工"
- `ChatView.vue`：landing 页 "featured-profiles" 标题、"给 X 发消息" placeholder
- `HelpView.vue`：帮助中心 "助手与记忆" tab -> "数字员工与记忆"
- `Sidebar.vue`：无直接"助手"文案，但 profileColor 注释可更新

### 8. AdminView 助手管理 -> 数字员工花名册
文件：`frontend/src/views/AdminView.vue` assistants tab

**卡片式花名册**（替代当前列表式）：
- 每个员工一张卡片：头像（icon+color）、姓名、工号、岗位、部门、状态 pill（在职/停用/休假）
- 卡片底部：消息数、本周 token、好评率（从 work-records 聚合）
- 点击卡片 -> 编辑抽屉/弹窗

**编辑表单新增字段**：
- 工号 employee_no
- 岗位 position
- 部门 department
- 状态 employee_status（select: 在职/休假/离职）
- 入职时间 hired_at（date picker）

### 9. 员工详情/绩效面板
在员工卡片上增加"详情"按钮，展开侧边面板或弹窗显示：
- 工作记录时间线（最近 20 条，含事件类型/摘要/token/反馈）
- 绩效汇总卡片：总消息数、总 token、好评率、平均响应时间
- 近 7 天每日趋势（CSS bar chart，复用用量看板的样式）

### 10. ChatView landing 页改造
- "featured-profiles" 区域标题改为"选择数字员工"
- 卡片显示：姓名 + 岗位 + 部门标签
- 选中后 placeholder 显示"给 [员工名] ([岗位]) 发消息"

### 11. 前端类型 + API 扩展
- `frontend/src/api/agents.ts`：Profile interface 加新字段；新增 `getWorkRecords(id)` / `getPerformance(id)` 方法
- `frontend/src/types/index.ts`：如有 Profile 相关类型也同步

## 文件清单
**后端修改**：
- `backend/app/db/models/agent.py`（Profile 新字段 + EmployeeWorkRecord 模型）
- `backend/alembic/versions/0055_employee_work_records.py`（新建迁移）
- `backend/app/schemas/agent.py`（ProfileOut/Create/Update 扩展）
- `backend/app/api/v1/agents.py`（新增 work-records/performance 端点）
- `backend/agent_runner/runner.py`（_finalize 后写 WorkRecord）

**前端修改**：
- `frontend/src/i18n/locales/zh-CN.ts` + `en.ts`（语义替换）
- `frontend/src/views/AdminView.vue`（花名册 + 编辑表单 + 绩效面板）
- `frontend/src/views/ChatView.vue`（landing 改造）
- `frontend/src/views/HelpView.vue`（tab 名称）
- `frontend/src/api/agents.ts`（类型 + API 方法）

## 验证
- `ruff check .` 通过
- `npm run type-check` + `npm run build` 通过
- `alembic upgrade head` 成功
- 手动验证：员工花名册卡片展示、编辑表单含新字段、绩效面板有数据