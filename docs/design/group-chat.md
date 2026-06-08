# 群聊系统设计方案

> **状态**: 设计中  
> **日期**: 2026-06-08  
> **目标**: 实现多人群聊，支持人→机、人→人、机→人、圆桌四种协作模式

---

## 1. 概述

### 1.1 现状问题

| 问题 | 说明 |
|------|------|
| 无群聊概念 | 只有"个人对话"和"圆桌"（所有Agent并行回答） |
| @mention 未实现 | `@助手名` 只是纯文本，不触发路由 |
| Agent 选择粒度粗 | 侧边栏 toggle 切换 `active_agent_ids`，要么单Agent要么全部 |
| `is_channel` 未利用 | 数据模型有这个字段但前端未使用 |

### 1.2 四种协作模式

| 模式 | 触发方式 | 路由逻辑 | 场景 |
|------|---------|---------|------|
| **人→机** | `@助手A` | 只路由到助手A | 问特定助手问题 |
| **人→人** | 不@任何人 | 不触发Agent，纯人聊 | 团队讨论 |
| **机→人** | Agent主动推送/定时任务 | Agent发消息到群聊 | 定时报告、主动提醒 |
| **圆桌** | `@all` 或 `@助手A @助手B` | 被@的Agent并行回答→合并 | 多角度分析、头脑风暴 |

---

## 2. 数据模型

### 2.1 Conversation 表改造

```python
class Conversation(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "conversations"

    title: Mapped[str]
    icon: Mapped[str | None]
    owner_id: Mapped[uuid.UUID]          # 创建者
    team_id: Mapped[uuid.UUID | None]    # 所属团队
    project_id: Mapped[uuid.UUID | None]

    # === 新增/改造字段 ===
    type: Mapped[str] = mapped_column(String(16), default="personal")
    # "personal" = 个人1:1对话
    # "group"    = 群聊（多人+多Agent）

    # === 保留字段 ===
    primary_agent_id: Mapped[str]        # 默认Agent（个人对话用）
    active_agent_ids: Mapped[list]       # 群聊中的Agent列表
    profile_id: Mapped[str | None]
    acp_session_id: Mapped[str | None]
    session_mode: Mapped[str | None]
    pinned: Mapped[bool]
    visibility: Mapped[str]              # "private" | "team" | "public"

    # === 废弃字段 ===
    # is_channel → 用 type="group" 替代，迁移后删除
```

### 2.2 群聊成员表（新增）

```python
class GroupMember(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "group_members"

    conversation_id: Mapped[uuid.UUID]   # 群聊ID
    user_id: Mapped[uuid.UUID | None]    # 人类成员（可空=Agent成员）
    agent_id: Mapped[str | None]         # Agent成员（可空=人类成员）
    role: Mapped[str] = mapped_column(String(16), default="member")
    # "admin"  = 群主/管理员
    # "member" = 普通成员
    joined_at: Mapped[datetime]
    last_read_at: Mapped[datetime | None]

    # 约束: user_id 和 agent_id 至少有一个非空
    # 约束: (conversation_id, user_id) 唯一 或 (conversation_id, agent_id) 唯一
```

### 2.3 Message 表改造

```python
class Message(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "messages"

    conversation_id: Mapped[uuid.UUID]
    owner_id: Mapped[uuid.UUID | None]   # 发送者（人类）
    agent_id: Mapped[str | None]         # 发送者（Agent）
    role: Mapped[str]                    # "user" | "agent" | "roundtable" | "system"
    content: Mapped[dict]                # {text, mentions?, replies?, merged?}
    status: Mapped[str]
    tokens_in: Mapped[int]
    tokens_out: Mapped[int]

    # === 新增字段 ===
    mentions: Mapped[list | None]        # 提及的Agent列表 ["hermes", "coder"]
    # 用于追踪这条消息@了谁，方便前端高亮和后端路由
```

### 2.4 数据模型关系图

```
Conversation (type="group")
    ├── GroupMember (user_id=用户A, role="admin")
    ├── GroupMember (user_id=用户B, role="member")
    ├── GroupMember (agent_id="hermes", role="member")
    ├── GroupMember (agent_id="coder", role="member")
    └── Messages
        ├── {role:"user", owner_id:用户A, mentions:["hermes"]}
        ├── {role:"agent", agent_id:"hermes", content:{text:"..."}}
        ├── {role:"user", owner_id:用户B, mentions:["hermes","coder"]}
        ├── {role:"roundtable", content:{replies:[...], merged:{text:"..."}}}
        └── {role:"system", content:{text:"用户C 加入了群聊"}}
```

---

## 3. @mention 路由逻辑

### 3.1 消息解析

用户发送消息时，前端提取 `@mention` 标记，后端做最终验证。

**前端解析规则：**
```
"@hermes 帮我写个函数"           → mentions: ["hermes"]
"@hermes @coder 这段代码怎么优化" → mentions: ["hermes", "coder"] → 圆桌
"@all 大家看看这个方案"           → mentions: ["__all__"] → 圆桌(全员)
"今天天气不错"                    → mentions: [] → 人→人模式，不触发Agent
```

**mention 格式：**
- `@agent_id` — 使用 Agent ID（如 `hermes`, `coder`）
- `@助手名` — 使用助手显示名（如 `@Hermes`, `@代码助手`），后端映射到 agent_id
- `@all` — 特殊标记，触发圆桌（群聊中所有Agent）

### 3.2 后端路由决策树

```
用户消息进入
    │
    ▼
解析 mentions 字段
    │
    ├── mentions 为空
    │   └── 人→人模式：只存消息，不触发Agent
    │
    ├── mentions = ["__all__"]
    │   └── 圆桌模式：群聊中所有Agent并行回答 → 合并
    │
    ├── len(mentions) == 1
    │   └── 人→机模式：只路由到该Agent，单Agent回答
    │
    └── len(mentions) > 1
        └── 圆桌模式：被@的Agent并行回答 → 合并
```

### 3.3 Agent 名称解析

```python
async def resolve_mention(mention: str, group_agents: list[str]) -> str | None:
    """将 @mention 文本解析为 agent_id。"""
    # 1. 直接匹配 agent_id
    if mention in group_agents:
        return mention

    # 2. 匹配助手显示名
    profile = await db.execute(
        select(Profile).where(Profile.name == mention, Profile.is_active == True)
    )
    profile = profile.scalar_one_or_none()
    if profile and profile.default_agent_id in group_agents:
        return profile.default_agent_id

    # 3. 模糊匹配（前缀/包含）
    for agent_id in group_agents:
        p = await get_profile_by_agent_id(agent_id)
        if p and (mention in p.name or p.name.startswith(mention)):
            return agent_id

    return None  # 无法识别的@mention忽略
```

---

## 4. API 设计

### 4.1 群聊管理

```
POST   /api/v1/conversations/group          创建群聊
GET    /api/v1/conversations?type=group     获取群聊列表
PATCH  /api/v1/conversations/{id}           更新群聊信息
DELETE /api/v1/conversations/{id}           删除群聊
```

**创建群聊请求体：**
```json
{
  "title": "项目讨论组",
  "member_user_ids": ["user-uuid-1", "user-uuid-2"],
  "member_agent_ids": ["hermes", "coder"],
  "team_id": "team-uuid"  // 可选，关联团队
}
```

**创建群聊响应：**
```json
{
  "id": "conv-uuid",
  "type": "group",
  "title": "项目讨论组",
  "members": [
    {"user_id": "user-uuid-1", "role": "admin", "name": "庭辉"},
    {"user_id": "user-uuid-2", "role": "member", "name": "张三"},
    {"agent_id": "hermes", "role": "member", "name": "Hermes"},
    {"agent_id": "coder", "role": "member", "name": "代码助手"}
  ],
  "active_agent_ids": ["hermes", "coder"],
  "created_at": "2026-06-08T10:00:00Z"
}
```

### 4.2 成员管理

```
POST   /api/v1/conversations/{id}/members          添加成员
DELETE /api/v1/conversations/{id}/members/{member_id} 移除成员
GET    /api/v1/conversations/{id}/members            获取成员列表
```

**添加成员：**
```json
{
  "user_id": "user-uuid-3"   // 添加人类
  // 或
  "agent_id": "translator"   // 添加Agent
}
```

### 4.3 消息发送（改造）

```
POST /api/v1/conversations/{id}/messages
```

**请求体：**
```json
{
  "text": "@hermes 帮我分析一下这段代码",
  "mentions": ["hermes"],
  "attached_file_ids": []
}
```

**后端处理流程：**
1. 存储用户消息（包含 mentions 字段）
2. 检查 `mentions` 决定路由模式
3. 人→人模式：只返回用户消息，不触发Agent
4. 人→机模式：调用单Agent，SSE流式返回
5. 圆桌模式：调用多个Agent，WebSocket流式返回+合并

### 4.4 群聊消息获取

```
GET /api/v1/conversations/{id}/messages?limit=50&before=cursor
```

**响应增加字段：**
```json
{
  "messages": [
    {
      "id": "msg-uuid",
      "role": "user",
      "owner_id": "user-uuid",
      "content": {"text": "@hermes 这个怎么解？"},
      "mentions": ["hermes"],
      "sender_name": "庭辉",
      "sender_avatar": null
    },
    {
      "id": "msg-uuid-2",
      "role": "agent",
      "agent_id": "hermes",
      "content": {"text": "这个问题可以这样理解..."},
      "sender_name": "Hermes",
      "sender_color": "#b8852a"
    }
  ]
}
```

---

## 5. 前端交互设计

### 5.1 侧边栏布局

```
┌─────────────────────┐
│  🔍 搜索             │
├─────────────────────┤
│  📢 群聊             │  ← 新增分区，在个人会话上方
│  ├ 项目讨论组 (3人2机)│
│  ├ 技术交流群         │
│  └ + 创建群聊         │
├─────────────────────┤
│  💬 个人会话          │  ← 原有分区
│  ├ 和 Hermes 的对话   │
│  ├ 和代码助手的对话    │
│  └ + 新建对话         │
├─────────────────────┤
│  📁 项目 / 👥 团队    │
└─────────────────────┘
```

### 5.2 @mention 输入交互

**Composer 组件改造：**

```
用户输入 "@" 
    │
    ▼
弹出 @mention 选择器（浮层）
    │
    ├── 显示群聊中的Agent列表
    │   ├  🤖 Hermes — 通用助手
    │   ├  🤖 代码助手 — 编程专家
    │   ├  🤖 翻译助手 — 多语言翻译
    │   └  📢 @all — 所有助手（圆桌）
    │
    ├── 键盘上下选择 + Enter 确认
    └── 输入文字过滤（如 @Herm → 只显示 Hermes）

选中后插入：
    "@Hermes 帮我写个函数" 
    显示为带样式的 @mention 标签（蓝色背景圆角）
```

**技术实现：**
- 监听 `@` 字符触发选择器
- 用 `contenteditable` 或隐藏 input + overlay 实现 mention 标签
- 发送时提取所有 mention 标签的 agent_id 放入 `mentions` 字段

### 5.3 群聊消息显示

```
┌─────────────────────────────────────┐
│ 庭辉  10:30                         │
│ @Hermes 帮我分析一下这段代码          │  ← @mention 高亮显示
│                                     │
│ ┌─ Hermes ────────────────────────┐ │
│ │ 这段代码的问题在于...             │ │  ← Agent 回复带 Agent 标识
│ └─────────────────────────────────┘ │
│                                     │
│ 张三  10:32                         │
│ 我觉得可以这样改...                  │  ← 纯人聊，无Agent回复
│                                     │
│ 庭辉  10:33                         │
│ @all 大家看看这个方案                │  ← 圆桌触发
│                                     │
│ ┌─ 圆桌 · 2位助手并行作答 ──────────┐ │
│ │ ┌ Hermes ──────────────────────┐ │ │
│ │ │ 方案从架构角度看...            │ │ │
│ │ └──────────────────────────────┘ │ │
│ │ ┌ 代码助手 ────────────────────┐ │ │
│ │ │ 实现层面建议...               │ │ │
│ │ └──────────────────────────────┘ │ │
│ │ ┌ Hermes 综合观点 ─────────────┐ │ │
│ │ │ 综合两位助手的建议...         │ │ │
│ │ └──────────────────────────────┘ │ │
│ └─────────────────────────────────┘ │
└─────────────────────────────────────┘
```

### 5.4 创建群聊流程

```
点击 "+ 创建群聊"
    │
    ▼
弹出创建群聊弹窗
    │
    ├── 1. 群聊名称 [输入框]
    │
    ├── 2. 添加人类成员
    │   └ 从团队成员中选择（多选）
    │
    ├── 3. 添加AI助手
    │   └ 从已注册的助手配置中选择（多选）
    │   ├  ☑ Hermes — 通用助手
    │   ├  ☐ 代码助手 — 编程专家
    │   └  ☐ 翻译助手 — 多语言翻译
    │
    └── 4. [创建] 按钮
```

---

## 6. 实现计划

### Phase 1: 数据模型 + 后端路由（优先级最高）

| 任务 | 说明 | 改动文件 |
|------|------|---------|
| 新增 `type` 字段 | Conversation 加 `type: "personal" \| "group"` | `models/conversation.py` |
| 新增 `GroupMember` 表 | 群聊成员关系 | `models/group.py` + 迁移 |
| Message 加 `mentions` | 追踪 @mention | `models/conversation.py` |
| 群聊 CRUD API | 创建/获取/更新/删除群聊 | `api/v1/conversations.py` |
| 成员管理 API | 添加/移除成员 | `api/v1/conversations.py` |
| @mention 路由 | `dispatch()` 改造，按 mentions 决定路由 | `services/conversation_service.py` |
| Agent 名称解析 | `@助手名` → `agent_id` 映射 | `services/conversation_service.py` |

### Phase 2: 前端 @mention 交互

| 任务 | 说明 | 改动文件 |
|------|------|---------|
| Composer @mention | 输入 `@` 弹出选择器 | `components/Composer.vue` |
| Mention 标签 | 蓝色圆角标签，可删除 | `components/Composer.vue` |
| 发送时提取 mentions | 从消息文本中提取 mention 列表 | `stores/chat.ts` |
| 消息显示 @mention 高亮 | 蓝色高亮显示 @mention | `views/ChatView.vue` |
| Agent 回复带标识 | 显示 Agent 头像/名称/颜色 | `views/ChatView.vue` |

### Phase 3: 侧边栏群聊分区

| 任务 | 说明 | 改动文件 |
|------|------|---------|
| 侧边栏群聊分区 | 在个人会话上方显示群聊列表 | `components/Sidebar.vue` |
| 创建群聊弹窗 | 选成员+Agent，输入名称 | `components/NewGroupModal.vue` |
| 群聊详情面板 | 成员列表、设置、退出 | `views/GroupDetailView.vue` |

### Phase 4: 机→人（Agent 主动推送）

| 任务 | 说明 | 改动文件 |
|------|------|---------|
| Agent 发消息到群聊 | 后端支持 Agent 角色发消息 | `services/conversation_service.py` |
| 定时任务集成 | Cron 任务结果推送到群聊 | `cron/` |
| 系统消息 | 成员加入/退出、Agent 推送等 | `services/conversation_service.py` |

---

## 7. 迁移策略

### 7.1 现有数据兼容

```sql
-- 0018_group_chat.sql

-- 1. Conversation 加 type 字段
ALTER TABLE conversations ADD COLUMN type VARCHAR(16) NOT NULL DEFAULT 'personal';

-- 2. is_channel → type 映射
UPDATE conversations SET type = 'group' WHERE is_channel = true;

-- 3. 创建群聊成员表
CREATE TABLE group_members (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    agent_id VARCHAR(64),
    role VARCHAR(16) NOT NULL DEFAULT 'member',
    joined_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_read_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT group_members_user_or_agent CHECK (user_id IS NOT NULL OR agent_id IS NOT NULL),
    CONSTRAINT group_members_unique_user UNIQUE (conversation_id, user_id),
    CONSTRAINT group_members_unique_agent UNIQUE (conversation_id, agent_id)
);

-- 4. Message 加 mentions 字段
ALTER TABLE messages ADD COLUMN mentions JSONB;

-- 5. 为现有群聊创建成员记录
INSERT INTO group_members (conversation_id, user_id, role)
SELECT id, owner_id, 'admin' FROM conversations WHERE type = 'group';

INSERT INTO group_members (conversation_id, agent_id, role)
SELECT c.id, unnest(c.active_agent_ids), 'member'
FROM conversations c WHERE c.type = 'group';

-- 6. 保留 is_column 作为兼容字段，后续版本删除
-- ALTER TABLE conversations DROP COLUMN is_channel;  -- v2.0 再删
```

### 7.2 向后兼容

- 现有 `is_channel=true` 的对话自动迁移为 `type="group"`
- 现有 `active_agent_ids` 逻辑不变，群聊成员从 `group_members` 表读取
- 现有圆桌功能不变，`@all` 触发等同于现有 roundtable
- 个人对话的 `dispatch()` 逻辑不受影响

---

## 8. 边界情况处理

| 场景 | 处理方式 |
|------|---------|
| 用户 @了一个不在群聊中的 Agent | 忽略该 mention，按剩余 mention 路由；全部无效则走人→人模式 |
| 用户 @all 但群聊中没有 Agent | 提示"群聊中暂无AI助手" |
| Agent 回复超时/失败 | 显示错误消息，不影响其他 Agent 的回复 |
| 同时多人 @同一 Agent | 排队处理，先到先答 |
| 用户在个人对话中使用 @mention | 忽略 @mention，按现有逻辑路由到 primary_agent |
| Agent 名称冲突（两个同名助手） | 使用 agent_id 区分，前端显示时加后缀区分 |
