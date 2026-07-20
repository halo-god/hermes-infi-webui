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

## 已知陷阱

- `backend/.env` 中的 `DATABASE_URL` 指向 `localhost:5432`（裸机），Docker 用 `postgres:5439`
- Redis 在 Docker 中使用密码 + 端口 1979（非默认 6379）
- 群聊 `get_conversation` 必须校验 GroupMember 成员资格，不能用宽泛 OR 条件
- Office HTML 给前端预览，注入 AI prompt 前必须用 `_html_to_plain_text()` 转纯文本
