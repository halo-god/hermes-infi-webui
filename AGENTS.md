# AGENTS.md

## 项目概述

Hermes 信使 — 全栈 AI Agent 协作平台（FastAPI + Vue 3 + ACP Agent Runner）。
用户通过 Web 界面与 AI 助手对话、管理团队/项目、定时任务、知识库等。

## 目录结构

```
backend/          FastAPI 后端（Python 3.11+）
  app/
    api/v1/        路由层（薄）：解析输入、鉴权、调服务、序列化
    services/      业务逻辑层（厚）：编排、事务、领域规则
    db/models/     SQLAlchemy 2.0 异步 ORM
    core/          横切：security, rbac, guards, governance, redis, files, metrics
    schemas/       Pydantic DTO
    config.py      pydantic-settings 配置
  agent_runner/    独立进程：消费 Redis Stream → 驱动 ACP 子进程
  alembic/versions/ 手写迁移（52+ 个，命名 00NN_*.py）
frontend/         Vue 3 + TypeScript + Pinia + Naive UI
  src/
    api/           axios 客户端（client.ts + 各领域 .ts）
    stores/        Pinia（auth, chat, branding, notifications, chatStream）
    views/         页面组件
    components/    可复用组件
    types/         index.ts — 所有 TS 接口唯一来源
docker/            compose.yaml + redis.conf + prometheus
```

## 常用命令

```bash
# 后端
cd backend && .venv/bin/ruff check .                # lint (line-length=100)
cd backend && .venv/bin/pytest tests/test_foo.py -k name  # 单测
cd backend && DATABASE_URL=... .venv/bin/alembic upgrade head  # 迁移

# 前端
cd frontend && npm run type-check    # vue-tsc --noEmit（strict, noUnusedLocals）
cd frontend && npm run build         # type-check + vite build（CI 门禁）
cd frontend && npm run dev           # :5173, /api 代理到 :8001

# 全栈（Docker）
make up && make migrate && make seed  # 启动全栈
```

## 架构规则

1. **4 层单向**：`api/v1 → services → db/models → PostgreSQL/Redis`。路由层不做业务逻辑。
2. **新端点**：schema → service → route → 注册 `api/v1/__init__.py`。
3. **新表**：ORM 模型继承 `UUIDPrimaryKey + Timestamps`，在 `db/models/__init__.py` 导入，手写 Alembic 迁移。
4. **SQLAlchemy 异步**：响应序列化期间绝不触发懒加载关系（`MissingGreenlet`）——显式查询 + 手工组装 DTO。
5. **Agent Runner** 是独立进程，通过 Redis Stream `acp:prompt` 通信，不嵌入 FastAPI。

## 编码规范

- 后端：`ruff check`（line-length=100），`from __future__ import annotations`
- 前端：Vue 3 `<script setup lang="ts">`，严格 TS（`noUnusedLocals`），构建前必须清理未使用导入
- 迁移中初始化 JSONB：用 `CAST(:d AS jsonb)` + `json.dumps()`，不要传已序列化字符串
- 文件上传：用 `read_upload_capped()` 限制大小，office 文件用 `process_upload()` 统一处理

## UI/UX 布局规范

- 页面根：`<div class="stage">`（滚动容器）
- 管理页：`.admin-hero` + `.admin-body`（`max-width: 1400px; margin: 0 auto`）
- 卡片：`.section-card` + `.section-head` + `.cfg-input`
- 侧栏列表：`max-height` + `overflow-y: auto` + `flex-shrink: 0`
- 下拉面板有溢出风险时，优先用 `router.push('/path')` 跳转

## 安全要点

- 鉴权：`Depends(get_current_user)`，admin 路由 `Depends(require_admin())`
- 团队权限：`team_service.require_permission(db, team_id, user_id, "perm.key")`
- 平台权限矩阵：`guards.require_permission(perm_id)` 查 `system_settings.permission_overrides`
- Token 撤销：Redis 不可用时 fail-closed（拒绝已撤销 token）
- 文件归属：`_resolve_attached_files` 校验 `conversation_id` 或用户 `__file_storage__` 会话

## 关键文件

- `CLAUDE.md` — 完整架构文档 + AI 提示词工程实践
- `backend/app/core/files.py` — `process_upload()`、`OFFICE_EXTRACTORS`、`extract_docx_html` 等
- `backend/app/services/conversation_service.py` — 消息分发核心（dispatch / dispatch_group / send_roundtable）
- `backend/app/core/governance.py` — 团队级权限矩阵
- `frontend/src/types/index.ts` — 所有 TS 接口定义

## hermes-agent 集成（HERMES_HOME 打通）

平台通过 HERMES_HOME 目录驱动外部 hermes-agent（`~/.hermes/hermes-agent`，ACP 协议）。集成代码与约定：

- **目录布局**：全局 `~/.hermes/`（hermes-default）+ 每助手 `~/.hermes/profiles/<handle>/`（config.yaml / SOUL.md / skills/ / memories/ / state.db / memory_store.db）。DB `Profile.path` = `<home>/config.yaml`，DB handle 带 `hermes-` 前缀（如 `hermes-emotion-master` ↔ 目录 `emotion-master`）
- **技能双向同步**（`services/skill_sync_service.py`）：
  - Direction A（DB→FS）：SKILL.md frontmatter 保留**原始 name** + `metadata.platform_skill_id`（DB 技能 UUID）；slug 冲突（中文名退化为 `unnamed-skill`、"API 测试"/"API 开发"→`api`）自动加 `-{id8}` 后缀防覆盖；改名/删除按 id 标记反查清理（含 hash 目录）
  - Direction B（FS→DB）：runner 启动 + 每日自动 ingest（`runner.py:_maybe_ingest_hermes_skills`，Redis 24h 冷却）；agent 来源技能（`AgentSkill.origin='agent'`）FS 消失自动 tombstone；平台技能（`origin='platform'`）永不受扫描影响
- **配置同步**（`services/hermes_config_sync.py`）：平台设置 deep-merge 进全局 + 各 profile 的 config.yaml；`reasoning_effort` 必须写 `agent.reasoning_effort`（顶层键 hermes 不读）；只同步 DB 中存在的 profile 目录
- **人设投影**：`Profile.system_prompt` 变更（含提示词演化自动应用）→ 写 `{profile_home}/SOUL.md`
- **记忆**：平台 Postgres（AgentMemory user+profile 两级 + MemoryEpisode）与 hermes 侧 `memories/MEMORY.md` 完全隔离；记忆合并子进程用隔离 HERMES_HOME（scratch），consolidation 按 profile 分组落库（`runner_memory.py`）
- **ACP 子进程 env**（`agent_runner/acp_client.py:profile_env`）：只注入 `HERMES_HOME` + `REDIS_URL`（HERMES_* 高级配置走 config.yaml，不注入 env）

## 已知陷阱

- `backend/.env` 中的 `DATABASE_URL` 指向 `localhost:5432`（裸机），Docker 用 `postgres:5439`
- Redis 在 Docker 中使用密码 + 端口 1979（非默认 6379）
- 群聊 `get_conversation` 必须校验 GroupMember 成员资格，不能用宽泛 OR 条件
- Office HTML 给前端预览，注入 AI prompt 前必须用 `_html_to_plain_text()` 转纯文本
- **清理 `~/.hermes` 下任何目录前，必须先核对运行中进程（`ps aux | grep hermes`）**：hermes 侧 profile 与平台 DB 完全解耦——`gateway run --profile X` 直接使用 `profiles/X/`，DB 无 Profile 行 ≠ 未使用（曾因只看 DB 误删运行中的 beta gateway home）
- 中文/近似名技能 slug 退化后，`platform_skill_id` 是改名/删除反查目录的唯一可靠键（不要按 slug 匹配删除）
- profile 目录改名（handle 变更）在 `agents.py:update_profile` 中自动迁移 FS home；不要手动移动 `profiles/<handle>/`
