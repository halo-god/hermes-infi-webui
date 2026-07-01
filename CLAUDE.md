# CLAUDE.md

本文件为 Claude Code（claude.ai/code）在本仓库中工作时提供指导。

## 命令

### 全栈（Docker，推荐）
```bash
make up        # 构建 + 启动所有服务（Postgres · Redis · MinIO · API · Web）
make down      # 停止
make fresh     # 停止 + 清空数据卷
make logs      # 查看 API 日志
make migrate   # 在运行中的 api 容器内执行 alembic upgrade head
make seed      # 在运行中的 api 容器内重新初始化超级管理员
```
默认登录：`admin@hermes.io` / `Hermes@2026` — Web: http://localhost:8080 · API 文档: http://localhost:8000/api/docs

### 后端（裸机）
```bash
cd backend
pip install -e ".[dev]"
alembic upgrade head
python -m app.seed
uvicorn app.main:app --reload          # API 端口 :8000
python -m agent_runner.runner          # agent runner（另开终端）
```

### 前端
```bash
cd frontend
npm install
npm run dev          # 端口 :5173，/api 代理到 :8000（含 WebSocket）
npm run type-check   # 仅 vue-tsc --noEmit
npm run build        # type-check + vite build（CI 门禁）
```

### 代码检查 / 测试
```bash
cd backend
ruff check .                            # lint（line-length=100）
pytest tests/test_foo.py -k test_name  # 单个测试
pytest                                  # 全部测试（asyncio_mode=auto）
```

---

## 架构

### 后端 — 4 层架构，严格单向调用

```
HTTP/WS  →  app/api/v1/*.py        路由层：解析输入、鉴权依赖、调用服务、序列化（薄）
            app/services/*.py      业务逻辑：编排、事务、领域规则（厚）
            app/db/models/*.py     SQLAlchemy 2.0 异步 ORM
            PostgreSQL / Redis
```

横切工具位于 `app/core/`：`security`（argon2id 密码、JWT）、`rbac`（平台角色）、`governance`（团队内容权限矩阵）、`redis`（连接 + Stream/PubSub/限流键）、`metrics`、`object_storage`。

所有 ORM 模型继承 `UUIDPrimaryKey` 和 `Timestamps`（来自 `app/db/models/mixins.py`）。迁移**手写**在 `backend/alembic/versions/000N_*.py`；用 `alembic revision -m "..."` 生成空白文件后填写 `upgrade`/`downgrade`。

配置全部在 `app/config.py`（`pydantic-settings`）；加一个带默认值的字段，通过 `from app.config import settings` 使用。

鉴权：`Depends(get_current_user)` 在 `app/deps.py`。Admin 路由用 `_require_admin(user)`。团队权限网关调用 `team_service.require_permission(db, team_id, user_id, "perm.key")`。

### Agent Runner — 独立进程

`agent_runner/runner.py` 消费 Redis Stream `acp:prompt`，通过 `acp_client.py` 驱动 ACP（JSON-RPC over stdio）会话，将结果写入 DB，并把流式事件追加到按会话限流的 Redis Stream `evt:conv:{id}`。API 层通过 XREAD 转发给客户端（单 agent 用 SSE，支持 `Last-Event-ID`/`since` 重连续传；圆桌用 WebSocket）。无 agent CLI 时回退到 `mock_agent.py`。

### Redis 键约定
| 键 | 用途 |
|-----|---------|
| `acp:prompt` | Stream：API → runner（prompt 任务） |
| `evt:conv:{id}` | Stream：runner/API → 客户端（流式 + 群聊事件；限流、可重传）。群聊新增 `message`（人类消息）、`message_update`（编辑/撤回/表情）、`typing`（临时）、`members_changed` |
| `evt:user:{id}` | Stream：API → 每用户 `/me/stream`（跨会话 `notify` 用于未读/@提及徽章；限流、可重传） |
| `presence:{user}` | 用户在线状态（SET ex=60；约 30s 心跳） |
| `hermes:clarify:req:{sid}` | List：agent → runner 澄清请求（RPUSH / LPOP） |
| `hermes:clarify:resp:{sid}:{cid}` | List：runner → agent 澄清回复（RPUSH / BLPOP） |
| `rl:msg:{user}` | 限流计数器 |
| `acp:cancel:{conv}` | 取消信号 |
| `jwt:blacklist:{jti}` | 登出 token 失效 |
| `mem:consolidate:status:{user}` | 记忆整理状态 + 运行锁（SET NX） |
| `mem:consolidate:cooldown:{user}` | 非管理员整理冷却（TTL） |

### 前端 — Vue 3 + Pinia

```
src/
├── api/         client.ts（axios + Bearer 注入 + 401 自动刷新）
│                auth / agents / conversations / teams / admin / projects .ts
├── stores/      auth.ts（会话、路由守卫）  ·  chat.ts（会话、SSE、圆桌 WS）
├── router/      index.ts — meta.requiresAdmin 用于仅管理员路由
├── views/       ChatView · AdminView · TeamDetailView · ProjectView …
├── components/  WorkspacePanel.vue（多标签文件预览/编辑，适配器模式）
├── types/       index.ts — 所有 TS 接口的唯一来源
└── utils/       markdown.ts（零依赖渲染器）
```

**鉴权流程**：`client.ts` 在每个请求注入 `Authorization: Bearer`；401 触发单飞刷新；刷新失败派发 `hermes:logout` → 路由跳转登录页。

**流式传输**：单 agent 用 SSE（`EventSource`，token 在 query 参数中）；圆桌用 WebSocket。均在 `stores/chat.ts` 处理。

**WorkspacePanel** 使用适配器模式 — 调用方传入 `files: FileItem[]` + `adapter: WsAdapter`，使同一面板同时适用于会话工作区文件和团队知识库文件。

**Profiles（"助手"）**：存储在 `profiles` 表。`GET /profiles` 返回所有活跃 profile。`POST /profiles/scan` 为未注册的 agent 自动创建 profile。管理员在 AdminView → "助手管理" tab 管理。

### SQLAlchemy 异步关键陷阱
- **响应序列化期间绝不触发懒加载关系** — 会导致 `MissingGreenlet`。需显式查询并手工组装 DTO（参见 `conversations.py` 的模式）。
- 测试必须在每个用例重置 engine/redis，避免 `attached to a different loop` 错误 — 参见 `tests/conftest.py`。
- 迁移中初始化 JSONB：用 `CAST(:d AS jsonb)` + `json.dumps(...)`，切勿传入已序列化的字符串并加 `type_=JSONB`。

### 常见新增操作

**新增 REST 端点**：schema 在 `app/schemas/<domain>.py` → 逻辑在 `app/services/<domain>_service.py` → 路由在 `app/api/v1/<domain>.py` → 在 `app/api/v1/__init__.py` 注册。

**新增数据库表**：添加继承 `UUIDPrimaryKey + Timestamps` 的 ORM 模型，在 `app/db/models/__init__.py` 导入，在 `alembic/versions/` 手写迁移。

**新增团队权限**：添加到 `app/core/governance.py` 的 `PERMISSIONS` + `_DEFAULTS`，用 `team_service.require_permission(...)` 守卫路由。

**新增前端页面**：API 方法在 `src/api/<domain>.ts`，类型在 `src/types/index.ts`，视图在 `src/views/XxxView.vue`，路由在 `src/router/index.ts`。

**TS 构建是严格的**（`noUnusedLocals`）：`npm run build` 前清理所有未使用的导入/变量。

### 前端 UI/UX 布局规范

所有页面必须遵循以下布局规范以保持视觉一致性：

1. **页面结构**：每个视图使用 `<div class="stage">` 作为根容器（滚动容器，`flex: 1; overflow-y: auto`）。
2. **Hero 头部**：管理/工具页使用 `<div class="admin-hero">` + `<div class="admin-hero-row">`（徽章 + 元信息）+ `<h1 class="admin-title">`（用 `<em>` 强调）+ `<div class="admin-sub">`（副标题）。
3. **居中内容区**：内容放在 `<div class="admin-body">` — `max-width: 1400px; margin: 0 auto; padding: 24px 40px 60px`。
4. **卡片**：使用 `<div class="section-card">` + `<div class="section-head"><div class="section-title">…</div></div>` + padding 内容体。
5. **统计卡片**：`<div class="stat-grid">` + `<div class="stat"><div class="stat-label">…</div><div class="stat-value">…</div></div>`。
6. **双栏布局**：`<div class="col-grid">`（CSS grid，`gap: 16px`）。
7. **表单**：输入框/下拉框/文本域用 `class="cfg-input"`。按钮用 `class="btn"` 或 `class="btn primary"`。
8. **过滤工具栏**：使用 `<div class="users-toolbar">` + `<div class="filter-input">`（搜索）+ `<button class="filter-select">`（下拉）。
9. **表格**：CSS grid 行 — `<div class="audit-table">` + `<div class="au-row head">`（表头）+ `<div class="au-row">`（数据行）。
10. **顶栏按钮**：使用 `class="icon-btn"` + `<Icon name="…" />`。下拉面板用 `position: relative` 包裹 + `position: absolute` 面板 + `z-index: 800`。**如下拉有溢出风险，优先用直接路由跳转**（`router.push('/path')`）。
11. **侧栏滚动区域**：可能增长的列表（团队、群聊）必须有 `max-height` + `overflow-y: auto` + `flex-shrink: 0`，防止挤压其他区域。会话列表（`.convo-list`）用 `flex: 1; min-height: 0` 填充剩余空间。
12. **状态标签/pill**：`<span class="fb-cat-pill">` / `<span class="fb-st-pill">` — 彩色边框 + 文字，`font-size: 10px; padding: 1px 6px; border-radius: 4px`。
13. **过渡动画**：下拉面板使用 `<Transition name="panel-drop">`（透明度 + translateY 8px，150ms）。

---

## AI 提示词工程实践（Prompt Engineering）

在解决复杂问题、修 BUG、设计架构时，以下两种提示词技巧经实战验证可显著提升 AI 输出质量。

### 一、第一性原理（"从第一性原理出发"）

**本质**：在提示词末尾加一句"从第一性原理出发"，强制 AI 放弃"类比推理 / 参考现有方案"，回到问题最底层的事实重新推导。

**为什么有效**：
- AI 默认倾向用类比推理（"这个问题像 XXX，所以照搬那个方案"），容易治标不治本
- "从第一性原理出发"迫使 AI 拆解到不可再分的基本事实，从零开始推导解决方案
- 类比：马斯克用第一性原理把火箭发射成本砍了 90%（不从"现有火箭多少钱"出发，而从"原材料成本是多少"重新算起）

**实战案例**：
- AIHOT 飞书推送出现 OpenAI 信源抓取失败
  - 不加提示词：AI 判断"某个国产模型改坏了表层配置" → 修表层 → 治标
  - 加"从第一性原理出发"：AI 发现是底层流量路由机制存在根本性缺陷，追溯到 4 个月前的代码，重构后治本

**适用场景**：解决问题、修 BUG、设计架构时，在提示词末尾追加这一句即可，无需安装额外工具。

### 二、对抗式审查

**本质**：让 AI 以"如果我是恶意用户，我要怎么搞崩这个系统"的角色，对代码/方案进行攻击性审查。

**为什么有效**：
- 常规 code review 容易陷入"功能是否正常"的正面思维，忽略边界条件和恶意输入
- 对抗式审查强制 AI 站在攻击者视角，主动寻找可被利用的漏洞
- 可并发开启多个 Agent 同时审查不同模块，提升覆盖面

**实战案例**（AIHOT 项目全局审查，开启约 40 个 Agent 并发）：
- **OOM 死循环**：大任务导致内存溢出 → 进程被杀 → 自动重试 → 又溢出……无限循环（根因：50MB~100MB 的 HTML 信源未做大小校验就全量加载）
- **未来时间污染**：信源发布时间因时区错误变成"明天"，导致文章排到信息流最前面，被误推送给用户
- **性能炸弹**：HTML 清洗模块、翻译模块的隐藏性能隐患（正则回溯爆炸、同步阻塞等）
- **缓存穿透假阳性**：部署探活机制误判缓存状态，导致穿透防护失效

**适用场景**：项目上线前安全审查、大规模重构后验证、怀疑存在隐藏 bug 时。提示词示例：

```
你是一个恶意攻击者，目标是搞崩这个系统。请审查以下代码，找出所有可被利用的漏洞：
- 能否通过构造输入导致 OOM、死循环、CPU 爆炸？
- 能否绕过权限校验访问他人数据？
- 能否通过时间/时区 manipulation 污染数据？
- 有无缓存穿透/雪崩风险？
```
