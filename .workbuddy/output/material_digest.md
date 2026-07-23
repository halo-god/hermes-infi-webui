# Hermes Infi WebUI · 资料摘要

> 本文档做一件事：**精读主理人转交的全部原始资料（D1–D4），逐份、逐章节做出摘要**——后面任何人拿到这份摘要，都能通过 `D编号，§章节` 快速定位回原始文件的对应位置。
>
> 上游输入：主理人转交的本地项目文档（README.md / CLAUDE.md / AGENTS.md，均为 md）与一份上游仓库调研（NousResearch/hermes-agent，md / WebFetch 抓取）。
> 产出者：`knowledge-ingest-engineer`（知识摄入工程师 - 闻资料），经 G1 校验与人工审核通过后交付。

---

## 0. 元信息

```yaml
标题: Hermes Infi WebUI - 资料摘要 v0.1
版本: v0.1
状态: Draft
创建日期: 2026-07-21
整理人: knowledge-ingest-engineer（闻资料）
审核人:
  - 主理人（team-lead）

原始资料清单:
  - /Users/caotinghui/Downloads/hermes-python/README.md: D1 Hermes Infi WebUI 项目说明
  - /Users/caotinghui/Downloads/hermes-python/CLAUDE.md: D2 开发 / 架构指引
  - /Users/caotinghui/Downloads/hermes-python/AGENTS.md: D3 项目概览与架构规则
  - NousResearch/hermes-agent 上游仓库调研（主理人 2026-07-21 WebFetch 抓取全文）: D4 上游 Agent 能力基线
```

| 版本 | 日期 | 作者 | 变更内容 |
| --- | --- | --- | --- |
| v0.1 | 2026-07-21 | knowledge-ingest-engineer | 初稿（基于 D1–D4 逐章摘要，记录 X1–X3 冲突） |
| v0.2 | 2026-07-21 | knowledge-ingest-engineer | 返工：消除尖括号占位符；§2 与 §3 出处统一改为 `D编号，§数字` 形式 |

---

## 1. 资料清单

> 列出全部原始资料，每份标注解析状态。解析失败或跳过的必须注明原因。

| 编号 | 文件名 | 类型 | 来源 | 解析状态 | 说明 |
| --- | --- | --- | --- | --- | --- |
| D1 | README.md | md | 项目仓库根目录 | 已解析 | 经 Read 直读，含项目说明 / 技术栈 / 架构 / 核心功能 |
| D2 | CLAUDE.md | md | 项目仓库根目录 | 已解析 | 经 Read 直读，含命令 / 架构 / Redis 键约定 / UI 规范 / 提示词实践 |
| D3 | AGENTS.md | md | 项目仓库根目录 | 已解析 | 经 Read 直读，含架构规则 / 编码规范 / 安全要点 / 已知陷阱 |
| D4 | NousResearch/hermes-agent 上游调研 | md（WebFetch） | 主理人 2026-07-21 抓取全文 | 已解析 | 上游 Agent 能力基线，覆盖 ACP/MCP/子代理网关/会话恢复/检查点/定时任务/推理分级等 |

**类型枚举说明**：本批实际输入均为 `md`（模板枚举 docx / pdf / pptx / xlsx 为通用解析能力矩阵，见附录 B）。无失败 / 跳过资料，故无原因说明项。

---

## 2. 资料内容摘要

> 逐份文档按自身章节结构做摘要。每条摘要标注章节号（`D编号，§章节`），后面任何人想核实某个点，直接定位回原文对应位置即可。各文档内部已建立小节顺序编号（§1/§2/§3…），保持可追溯。

> **方向标注约定**（行末方括号，关联用户 5 大诉求方向，仅用于提示抽取重点，不做业务结论）：
> 【方向1】差异化定位 ｜ 【方向2】协作模型 ｜ 【方向3】系统架构方案 ｜ 【方向4】关键能力清单 ｜ 【方向5】创新点建议

### D1：README.md（Hermes Infi WebUI 项目说明）

> 自托管 AI Agent 平台 Web 界面说明，覆盖定位 / 技术栈 / 架构 / 核心功能 — 来源：项目仓库根目录

| 章节 | 内容摘要 |
| --- | --- |
| D1，§1 这是什么 | Hermes Infi WebUI 是基于 Hermes Agent 的自托管 AI Agent 平台 Web 界面，支持多用户、团队协作、Web 工作区，通过 ACP（Agent Client Protocol）驱动后台 Agent 会话。【方向1】【方向3】 |
| D1，§2 技术栈 | 后端 Python 3.11 + FastAPI + SQLAlchemy 2.0(async)；数据库 PostgreSQL 16；缓存/消息 Redis 7；对象存储 MinIO（或内联 DB）；Agent 运行时 Hermes Agent via ACP(JSON-RPC over stdio)；前端 Vue 3 + TypeScript + Pinia + Naive UI；构建 Vite 5、Docker Compose、Alembic 迁移。【方向3】 |
| D1，§3 快速开始/Docker | `make up` 构建并启动 Postgres/Redis/MinIO/API/Web；Web http://localhost:8080，API 文档 http://localhost:8000/api/docs，默认账号 admin@hermes.io / 密码见 .env 的 FIRST_ADMIN_PASSWORD。另提供 make down/fresh/logs/migrate/seed。 |
| D1，§4 快速开始/裸机部署 | 后端：venv + `pip install -e ".[dev]"` + `alembic upgrade head` + `python -m app.seed` + `uvicorn app.main:app --reload --port 8000`；另启 Agent Runner：`python -m agent_runner.runner`；前端：`npm install` + `npm run dev`（:5173）。另提供 start-api.sh(:8001)/start-agent.sh/start-web.sh 脚本。【方向2】 |
| D1，§5 架构 | 整体拓扑：Vue 3 SPA ↔ FastAPI（SSE/WS，/api/*）↔ [Postgres / Redis / MinIO] ↔ Agent Runner ↔ Hermes Agent(ACP over stdio)。后端四层单向：HTTP/WS → app/api/v1(薄) → app/services(厚) → app/db/models → PostgreSQL/Redis。【方向3】 |
| D1，§6 架构/后端目录 | `app/api/v1/`（REST：认证 / 对话 / 团队 / 管理 / Agent / 分析）；`app/services/`（对话编排 / 团队权限 / 认证）；`app/db/models/`（用户 / 对话 / Agent / 团队 / 工作区 / 审计）；`app/core/`（安全 argon2id+JWT / RBAC / Redis / 治理）；`agent_runner/`（ACP 会话消费者，从 Redis Stream 读取，驱动 Hermes Agent）；`alembic/`（手写迁移）。【方向3】【方向4】 |
| D1，§7 架构/Agent Runner | 独立进程职责：1) 消费 Redis Stream `acp:prompt`；2) 通过 ACP(JSON-RPC over stdio) 启动 Hermes Agent 会话；3) 通过 Redis PubSub `chan:conv:{id}` 流式回传结果；4) 将对话历史与工作区文件写入数据库。API 层订阅 PubSub，经 SSE（单 Agent）或 WebSocket（圆桌）转发客户端。【方向2】【方向3】注：流式回传机制与 D2 不一致，见 §3 X1。 |
| D1，§8 架构/前端 | `src/` 含 api(Axios 自动刷新 token)、stores(Pinia: auth/chat/notifications)、router(Vue Router 含管理员路由守卫)、views(对话/管理/团队/项目/分析/终端)、components(编辑器/工作区面板/侧边栏/弹窗)、types(TS 接口唯一事实来源)、utils(Markdown 渲染零依赖)、composables(useTheme/useStream/usePresence)、i18n(中英文本地化)。【方向3】 |
| D1，§9 核心功能/对话 | 选任意助手配置创建对话；实时流式（打字指示器 + 执行步骤跟踪）；消息搜索 / 分叉（从任意节点分支）/ 导出（Markdown / JSON）；根据回复生成智能追问建议；知识库注入（团队知识库内容附加到提示词）。【方向2】【方向4】 |
| D1，§10 核心功能/团队与协作 | 创建团队、邀请成员、基于角色的权限控制；成员共享对话；团队知识库（上传 / 管理参考文档）；权限矩阵：创建、读取、管理对话和知识库。【方向2】【方向4】 |
| D1，§11 核心功能/项目 | 将对话组织到项目中，带任务跟踪；文件上传与项目级工作区管理；任务状态跟踪（待办 / 进行中 / 已完成）。【方向4】 |
| D1，§12 核心功能/管理面板 | 用户管理（创建 / 激活 / 停用账号）；Agent 管理（注册 / 配置助手配置文件）；系统设置（品牌 / 模型配置）；审计日志（跟踪所有重要操作）；Agent 扫描（自动发现 Hermes Agent 安装）。【方向4】 |
| D1，§13 核心功能/工作区 | AI 生成的文件可版本管理、在线编辑；多标签文件编辑器（语法高亮）；文件差异查看器；MinIO 或数据库存储（可配置）。【方向4】 |
| D1，§14 配置 | 后端配置在 `backend/app/config.py`（pydantic-settings），环境变量覆盖默认值。关键变量：DATABASE_URL（默认 `postgresql+asyncpg://hermes:hermes@localhost:5432/hermes`）、REDIS_URL（默认 `redis://localhost:6379/0`）、SECRET_KEY、STORAGE_BACKEND（db/minio）、HERMES_BIN（hermes）、HERMES_HOME（~/.hermes）、VITE_API_PROXY_TARGET（默认 `http://localhost:8000`）。 |
| D1，§15 开发 | 后端 `ruff check` / `pytest`；前端 `npm run type-check`(vue-tsc) / `build` / `dev`。新增端点：schema→service→route→注册 `api/v1/__init__.py`；新增表：ORM 继承 UUIDPrimaryKey+Timestamps→`db/models/__init__.py` 导入→手写 Alembic 迁移；新增前端页：api/types/views/router。 |
| D1，§16 项目结构 | 完整目录树：backend（app/api,v1/services/db/models/schemas/core/config、agent_runner、alembic、tests）、frontend（src/api,stores,views,components,types,utils）、docker/compose.yaml、start-*.sh、DEPLOYMENT.md、Makefile、README.md。 |
| D1，§17 许可 | 内部项目，私有使用。 |

### D2：CLAUDE.md（开发 / 架构指引）

> 面向 Claude Code 的本仓库工作指导，覆盖命令 / 架构细节 / Redis 键约定 / 前端规范 / 提示词工程 — 来源：项目仓库根目录

| 章节 | 内容摘要 |
| --- | --- |
| D2，§1 命令/全栈 | `make up`（构建+启动 Postgres·Redis·MinIO·API·Web）、make down/migrate/seed/logs/fresh。默认 admin@hermes.io / FIRST_ADMIN_PASSWORD；Web :8080，API 文档 :8000/api/docs。 |
| D2，§2 命令/后端 | `cd backend`；`pip install -e ".[dev]"`；`alembic upgrade head`；`python -m app.seed`；`uvicorn app.main:app --reload`（API 端口 :8000）；`python -m agent_runner.runner`（另开终端）。 |
| D2，§3 命令/前端 | `cd frontend`；`npm install`；`npm run dev`（端口 :5173，/api 代理到 :8000 含 WebSocket）；`npm run type-check`（仅 vue-tsc --noEmit）；`npm run build`（type-check + vite build，CI 门禁）。 |
| D2，§4 命令/代码检查 | `ruff check .`（line-length=100）；`pytest`（asyncio_mode=auto）；单测 `pytest tests/test_foo.py -k test_name`。 |
| D2，§5 架构/后端4层 | HTTP/WS → `app/api/v1/*.py`（薄：解析输入 / 鉴权依赖 / 调服务 / 序列化）→ `app/services/*.py`（厚：编排 / 事务 / 领域规则）→ `app/db/models/*.py`（SQLAlchemy 2.0 异步）→ PostgreSQL/Redis。横切工具在 `app/core/`：security(argon2id+JWT)、rbac(平台角色)、governance(团队内容权限矩阵)、redis(连接+Stream/PubSub/限流键)、metrics、object_storage。ORM 继承 UUIDPrimaryKey+Timestamps；迁移手写 `alembic/versions/000N_*.py`。鉴权 `Depends(get_current_user)`；admin 用 `_require_admin(user)`；团队权限网关 `team_service.require_permission(db, team_id, user_id, "perm.key")`。【方向3】【方向4】 |
| D2，§6 架构/Agent Runner | `agent_runner/runner.py` 消费 Redis Stream `acp:prompt`，通过 `acp_client.py` 驱动 ACP(JSON-RPC over stdio) 会话，结果写入 DB，并把流式事件追加到按会话限流的 Redis Stream `evt:conv:{id}`；API 层通过 XREAD 转发（单 agent 用 SSE，支持 `Last-Event-ID`/`since` 重连续传；圆桌用 WebSocket）。无 agent CLI 时回退 `mock_agent.py`。【方向2】【方向3】注：流式回传机制与 D1 不一致，见 §3 X1。 |
| D2，§7 架构/Redis键约定 | 键：acp:prompt（Stream：API→runner，prompt 任务）；evt:conv:{id}（Stream：runner/API→客户端，流式+群聊事件，限流可重传；群聊新增 message 人类消息 / message_update 编辑·撤回·表情 / typing 临时 / members_changed）；evt:user:{id}（Stream：API→每用户 /me/stream，跨会话 notify 用于未读/@提及徽章）；presence:{user}（用户在线状态 SET ex=60，约 30s 心跳）；hermes:clarify:req:{sid}（List：agent→runner 澄清请求，RPUSH/LPOP）；hermes:clarify:resp:{sid}:{cid}（List：runner→agent 澄清回复，RPUSH/BLPOP）；rl:msg:{user}（限流计数器）；acp:cancel:{conv}（取消信号）；jwt:blacklist:{jti}（登出 token 失效）；mem:consolidate:status:{user}（记忆整理状态+运行锁 SET NX）；mem:consolidate:cooldown:{user}（非管理员整理冷却 TTL）。【方向2】【方向4】 |
| D2，§8 架构/前端 Vue3 | `src/`：api（client.ts axios+Bearer 注入+401 自动刷新；auth/agents/conversations/teams/admin/projects .ts）、stores（auth.ts 会话+路由守卫；chat.ts 会话+SSE+圆桌 WS）、router（index.ts，meta.requiresAdmin 仅管理员路由）、views（ChatView·AdminView·TeamDetailView·ProjectView…）、components（WorkspacePanel.vue 多标签文件预览/编辑，适配器模式）、types（index.ts 所有 TS 接口唯一来源）、utils（markdown.ts 零依赖）。鉴权：每请求注入 Bearer，401 单飞刷新，失败派发 hermes:logout→跳转登录。流式：单 agent SSE（EventSource，token 在 query）；圆桌 WebSocket，均在 stores/chat.ts。Profiles（“助手”）存 profiles 表，GET /profiles 返回活跃 profile，POST /profiles/scan 为未注册 agent 自动创建，AdminView “助手管理” tab 管理。【方向2】【方向3】 |
| D2，§9 SQLAlchemy异步陷阱 | 响应序列化期间绝不触发懒加载关系（防 MissingGreenlet，需显式查询+手工组装 DTO）；测试每用例重置 engine/redis（防 attached to a different loop）；迁移 JSONB 用 `CAST(:d AS jsonb)` + `json.dumps(...)`，勿传已序列化字符串加 type_=JSONB。 |
| D2，§10 常见新增操作 | 新增 REST：schema→service→route→注册 `api/v1/__init__.py`。新增表：ORM 继承 UUIDPrimaryKey+Timestamps，导入 `db/models/__init__.py`，手写 Alembic 迁移。新增团队权限：加 `governance.py` 的 PERMISSIONS+_DEFAULTS，用 `require_permission` 守卫路由。新增前端页：api/types/views/router。TS 严格（noUnusedLocals）。 |
| D2，§11 前端UI/UX布局规范 | 13 条布局规范：stage 根容器(滚动 flex:1;overflow-y:auto)；admin-hero 头部+badge+title（em 标签）+sub；admin-body 居中(max-width:1400px;margin:0 auto;padding:24px 40px 60px)；section-card 卡片+section-head+内容体；stat-grid 统计卡；col-grid 双栏(gap:16px)；cfg-input 表单控件+btn/btn primary；users-toolbar 过滤栏+filter-input+filter-select；audit-table(au-row head+data)；icon-btn 顶栏+Icon；侧栏滚动区 max-height+overflow-y:auto+flex-shrink:0（会话列表 flex:1;min-height:0）；fb-cat-pill/fb-st-pill 状态标签(10px 彩边)；panel-drop 过渡(透明度+translateY 8px,150ms)。 |
| D2，§12 AI提示词工程实践/第一性原理 | 提示词末尾加“从第一性原理出发”，强制 AI 放弃类比推理、回到最底层事实重新推导。实战：AIHOT 飞书推送信源抓取失败，不加该句判为“国产模型改坏表层配置”治标；加后追溯到 4 个月前流量路由根因并重构治本。适用：解决问题/修 BUG/设计架构。 |
| D2，§13 AI提示词工程实践/对抗式审查 | 让 AI 以“恶意用户”角色对代码/方案做攻击性审查，强制站在攻击者视角找漏洞；可并发多 Agent 审不同模块。实战（AIHOT 全局审查，约 40 Agent 并发）：OOM 死循环（大 HTML 信源未做大小校验全量加载）、未来时间污染（时区错误致文章排到信息流最前误推）、性能炸弹（正则回溯/同步阻塞）、缓存穿透假阳性（探活误判缓存状态）。提示词聚焦 OOM/死循环/CPU、权限绕过、时间污染、缓存穿透雪崩。 |

### D3：AGENTS.md（项目概览与架构规则）

> 项目概览与硬性架构/编码/安全规则，含关键文件与已知陷阱 — 来源：项目仓库根目录

| 章节 | 内容摘要 |
| --- | --- |
| D3，§1 项目概述 | Hermes 信使 — 全栈 AI Agent 协作平台（FastAPI + Vue 3 + ACP Agent Runner）。用户通过 Web 界面与 AI 助手对话、管理团队 / 项目、定时任务、知识库等。【方向1】【方向3】 |
| D3，§2 目录结构 | backend（app/api/v1 薄、services 厚、db/models、core 横切 security/rbac/guards/governance/redis/files/metrics、schemas、config.py；agent_runner 独立进程消费 Redis Stream 驱动 ACP 子进程；alembic/versions 手写迁移 52+ 个，命名 00NN_*.py）、frontend（Vue3+TS+Pinia+Naive UI；src/api,stores(auth/chat/branding/notifications/chatStream),views,components,types）、docker（compose.yaml+redis.conf+prometheus）。【方向3】 |
| D3，§3 常用命令 | 后端 ruff/pytest/alembic；前端 `npm run type-check`(vue-tsc strict noUnusedLocals) / `npm run build`(CI 门禁) / `npm run dev`（:5173，/api 代理到 :8001）；全栈 `make up && make migrate && make seed`。 |
| D3，§4 架构规则 | 1) 4 层单向 `api/v1 → services → db/models → PostgreSQL/Redis`，路由层不做业务逻辑；2) 新端点 schema→service→route→注册 `api/v1/__init__.py`；3) 新表 ORM 继承 UUIDPrimaryKey+Timestamps，导入 `db/models/__init__.py`，手写 Alembic 迁移；4) SQLAlchemy 异步序列化期间绝不懒加载（防 MissingGreenlet）；5) Agent Runner 独立进程，经 Redis Stream `acp:prompt` 通信，不嵌入 FastAPI。【方向3】 |
| D3，§5 编码规范 | 后端 ruff(line-length=100) + `from __future__ import annotations`；前端 Vue3 `script setup` 写法（TypeScript），严格 TS(noUnusedLocals) 构建前清未用导入；迁移 JSONB 用 `CAST(:d AS jsonb)` + `json.dumps()`；文件上传 `read_upload_capped()` 限大小，office 用 `process_upload()` 统一处理。 |
| D3，§6 UI/UX布局规范 | stage 根(滚动容器)；管理页 admin-hero+admin-body(max-width:1400px;margin:0 auto)；卡片 section-card+section-head+cfg-input；侧栏列表 max-height+overflow-y:auto+flex-shrink:0；下拉面板溢出风险优先 router.push('/path')。 |
| D3，§7 安全要点 | 鉴权 `Depends(get_current_user)`，admin 路由 `Depends(require_admin())`；团队权限 `team_service.require_permission(db, team_id, user_id, "perm.key")`；平台权限矩阵 `guards.require_permission(perm_id)` 查 `system_settings.permission_overrides`；Token 撤销 Redis 不可用时 fail-closed（拒绝已撤销 token）；文件归属 `_resolve_attached_files` 校验 conversation_id 或用户 `__file_storage__` 会话。【方向2】【方向4】 |
| D3，§8 关键文件 | CLAUDE.md（完整架构+提示词实践）；backend/app/core/files.py（process_upload/OFFICE_EXTRACTORS/extract_docx_html）；backend/app/services/conversation_service.py（消息分发核心 dispatch/dispatch_group/send_roundtable）；backend/app/core/governance.py（团队级权限矩阵）；frontend/src/types/index.ts（所有 TS 接口定义）。【方向2】【方向4】 |
| D3，§9 已知陷阱 | backend/.env 的 DATABASE_URL 指向 localhost:5432（裸机），Docker 用 postgres:5439；Redis 在 Docker 中用密码+端口 1979（非默认 6379）；群聊 get_conversation 必须校验 GroupMember 成员资格（勿用宽泛 OR 条件）；Office HTML 给前端预览，注入 AI prompt 前必须用 `_html_to_plain_text()` 转纯文本。 |

### D4：NousResearch/hermes-agent 上游仓库调研（主理人 2026-07-21 WebFetch 抓取）

> 上游 Agent 运行时能力基线，为 WebUI 设计方案提供底层能力参照 — 来源：主理人抓取上游仓库全文

| 章节 | 内容摘要 |
| --- | --- |
| D4，§1 仓库元数据 | 仓库 NousResearch/hermes-agent，标语 “The agent that grows with you”，MIT 许可，Python 3.11–3.13（不含 3.14），16,594 Commits（截至 2026-07-21）。Nous Chat 已退役（website/ 与 docs 移除引用）。【方向1】 |
| D4，§2 交互形态 | CLI、TUI（终端 UI）、桌面端（Electron）、Web 仪表板（Dashboard）并存：hermes_cli/、ui-tui/、apps/desktop、web/。【方向3】 |
| D4，§3 Agent运行时核心 | agent/ 目录为运行时核心；支持推理努力级别（reasoning effort levels，新增 max 和 ultra）；会话与状态管理 hermes_state.py、tui_gateway/（会话恢复、验证候选消息、去重）；定时任务 cron/；凭证读取守卫失败即关闭（fail closed）。【方向4】 |
| D4，§4 模型提供商扩展 | 内置 DeepInfra、Upstage Solar、Nous 自身等，通过插件系统 plugins/model-providers/ 接入；HERMES_OVERLAYS 与 plugin.yaml 注册。【方向3】 |
| D4，§5 检查点 | 检查点（checkpoints）遵循网关配置与任务 cwd。【方向4】 |
| D4，§6 协议/ACP | ACP（Agent Client Protocol）— 含 acp_adapter/（会话管理 SessionManager._restore）、acp_registry/，用于衔接 Zed 等编辑器中的代理会话。【方向2】【方向3】 |
| D4，§7 协议/MCP | MCP（Model Context Protocol）— optional-mcps/ 目录强制精确版本固定（git 安装需 40-char SHA，uvx/npx 需 pkg==X / pkg@X），tools/ 涉及 MCP 电路熔断（circuit breaker）。【方向3】 |
| D4，§8 Agent定义/运行 | 配置驱动（cli-config.yaml.example）；推理控制 /reasoning、/fast slash 命令；HERMES_YOLO_MODE 可绕过危险命令审批；验证循环 verify-on-stop，临时助理文本持久化至 state.db 并作为 interim 消息发出。【方向4】 |
| D4，§9 多代理/团队支持 | 子代理（subagent）网关（docs/plans/2026-05-07-s6-overlay-dynamic-subagent-gateways.md 提及 s6-overlay 动态子代理网关）；CLI 中断子代理测试；计划文件 .plans/ 合并 PR 追踪。【方向2】【方向4】 |
| D4，§10 WebUI/协作 | web/ 仪表板含 Mixture-of-Agents(MoA) 预设模态框；apps/desktop Electron 含 Playwright E2E 视觉回归；hermes_cli/web_server.py 提供 REST API（如 POST /api/cron/jobs）；tui_gateway/server.py 会话 info 带 install_warning。【方向2】【方向4】 |
| D4，§11 协作相关 | scripts/ 中工作流每 15s 轮询 GitHub Actions API 并 upsert 评论（实时 CI 审查）；i18n 含 locales/（16 种语言）；安装方式仅 curl 安装脚本为官方支持路径（pip/Homebrew 不支持）。【方向2】 |

---

## 3. 冲突记录

> 不同资料对同一事实描述矛盾时，**并列保留两个版本**，不做裁决。

| 编号 | 冲突主题 | 版本 A | 出处 A | 版本 B | 出处 B | 差异说明 |
| --- | --- | --- | --- | --- | --- | --- |
| X1 | Agent 会话流式回传机制与 Redis 键 | Redis PubSub `chan:conv:{id}`，API 层订阅 PubSub 转发（SSE 单 Agent / WebSocket 圆桌） | D1，§7 架构/Agent Runner | Redis Stream `evt:conv:{id}`，API 层经 XREAD 转发（SSE 单 Agent / WebSocket 圆桌） | D2，§6 架构/Agent Runner | 传输机制（PubSub vs Stream）与键名（chan:conv vs evt:conv）均不一致；需主理人 / 下游裁定以哪个为准，并核对实际代码实现 |
| X2 | 前端 /api 代理目标端口 | :8000（VITE_API_PROXY_TARGET 默认 `http://localhost:8000`；/api 代理到 :8000 含 WebSocket） | D1，§14 配置；D2，§3 命令/前端 | :8001（npm run dev /api 代理到 :8001；start-api.sh 标注 FastAPI :8001） | D3，§3 常用命令；D1，§4 快速开始/裸机部署 | 端口 8000 vs 8001，可能对应不同启动方式（uvicorn 直启 :8000 vs start-api.sh :8001）；设计部署时需对齐端口约定 |
| X3 | Redis / PostgreSQL 端口与认证（文档默认 vs Docker 实际） | 默认 REDIS_URL `redis://localhost:6379/0`、DATABASE_URL `localhost:5432` | D1，§14 配置 | Docker 中 Redis 端口 1979 + 密码、DATABASE_URL `postgres:5439` | D3，§9 已知陷阱 | 文档默认端口（6379/5432）与 Docker 实际（1979/5439）不一致；非直接矛盾但属配置-vs-部署差异，下游部署设计需以 docker/compose 实际配置为准 |

---

## 4. 硬指标清单

| 章节 | 硬指标 | 状态 |
| --- | --- | --- |
| §1 | 每份资料有解析状态，失败/跳过注明原因 | ✅（D1–D4 均“已解析”，无失败 / 跳过，故无原因说明项） |
| §2 | 每份文档按章节逐条摘要，每条标注了 `D编号，§章节` | ✅（D1–D4 均按自身章节结构逐条摘要，全部带 `D编号，§数字` 溯源，如 D1，§7 / D2，§6 / D3，§9 / D4，§1） |
| §3 | 冲突信息并列保留，不做裁决 | ✅（X1–X3 均并列双版本 + 出处，未做裁决） |

---

## 附录 A：生成流程

### 流程总览

| 步骤 | 动作 | 落入章节 |
| --- | --- | --- |
| Step0 | 读取模板 + 全部原始资料（D1 README.md / D2 CLAUDE.md / D3 AGENTS.md / D4 上游调研） | — |
| Step1 | 盘点资料清单，标注解析状态 | §1 |
| Step2 | 逐份打开资料，按自身章节结构逐条摘要（D1→D2→D3→D4），每行标注 `D编号，§数字` | §2 |
| Step3 | 交叉比对不同资料，发现并记录矛盾（X1 流式机制 / X2 代理端口 / X3 Redis·DB 端口） | §3 |
| Step4 | 逐项核验硬指标 | §4 |

```mermaid
flowchart LR
    S0[读取模板与 D1-D4] --> S1[盘点资料清单]
    S1 --> S2[逐份精读逐章节摘要]
    S2 --> S3[交叉比对记录冲突]
    S3 --> S4[硬指标自检]
```

### 整理原则

1. **逐份精读，不跨文档归并**：摘要按文档自身章节结构组织，不做跨文档的主题重组（那是下游的事）。
2. **出处即章节号**：每条摘要标注 `D编号，§章节`，直接映射回原文位置。
3. **冲突保留**：矛盾信息并列保留两个版本，不擅自裁决。
4. **事实驱动**：以原始资料中的事实为准，不添加主观推断；方向标注（【方向1】–【方向5】）仅提示抽取重点，不构成业务结论。

---

## 附录 B：解析 Skill

- `md`：Markdown 类项目说明 / 架构指引 / 概览（本批 D1–D4 均为 `.md`，经 Read 工具直接解析；D4 为 WebFetch 抓取的上游全文）。
- `docx`：Word 类产品 / 业务文档（通用能力矩阵，本批未涉及）。
- `pdf`：PDF 类规范、手册、报告（通用能力矩阵，本批未涉及）。
- `pptx`：PPT 类方案 / 汇报（通用能力矩阵，本批未涉及）。
- `xlsx`：Excel 类数据清单、指标表（通用能力矩阵，本批未涉及）。
