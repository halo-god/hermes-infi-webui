# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

### Full stack (Docker, recommended)
```bash
make up        # build + start all services (Postgres · Redis · MinIO · API · Web)
make down      # stop
make fresh     # stop + wipe volumes
make logs      # tail API logs
make migrate   # alembic upgrade head inside running api container
make seed      # re-seed super-admin inside running api container
```
Default login: `admin@hermes.io` / `Hermes@2026` — Web: http://localhost:8080 · API docs: http://localhost:8000/api/docs

### Backend (bare-metal)
```bash
cd backend
pip install -e ".[dev]"
alembic upgrade head
python -m app.seed
uvicorn app.main:app --reload          # API on :8000
python -m agent_runner.runner          # agent runner (separate terminal)
```

### Frontend
```bash
cd frontend
npm install
npm run dev          # :5173, /api proxied to :8000 (including WebSocket)
npm run type-check   # vue-tsc --noEmit only
npm run build        # type-check + vite build (CI gate)
```

### Linting / tests
```bash
cd backend
ruff check .                            # lint (line-length=100)
pytest tests/test_foo.py -k test_name  # single test
pytest                                  # all tests (asyncio_mode=auto)
```

---

## Architecture

### Backend — 4-layer, strictly one-way

```
HTTP/WS  →  app/api/v1/*.py        Routes: parse input, auth deps, call service, serialize (thin)
            app/services/*.py      Business logic: orchestration, transactions, domain rules (thick)
            app/db/models/*.py     SQLAlchemy 2.0 async ORM
            PostgreSQL / Redis
```

Cross-cutting utilities live in `app/core/`: `security` (argon2id passwords, JWT), `rbac` (platform roles), `governance` (team content-permission matrix), `redis` (connection + Stream/PubSub/rate-limit keys), `metrics`, `object_storage`.

All ORM models inherit `UUIDPrimaryKey` and `Timestamps` from `app/db/models/mixins.py`. Migrations are **hand-written** in `backend/alembic/versions/000N_*.py`; generate a blank with `alembic revision -m "..."` then fill in `upgrade`/`downgrade`.

Configuration is entirely in `app/config.py` (`pydantic-settings`); add a field with a default, consume via `from app.config import settings`.

Auth: `Depends(get_current_user)` in `app/deps.py`. Admin routes use `_require_admin(user)`. Team permission gates call `team_service.require_permission(db, team_id, user_id, "perm.key")`.

### Agent Runner — separate process

`agent_runner/runner.py` consumes Redis Stream `acp:prompt`, drives ACP (JSON-RPC over stdio) sessions via `acp_client.py`, writes results to DB, and appends streaming events to the capped per-conversation Redis Stream `evt:conv:{id}`. The API layer XREADs and forwards to clients via SSE (single-agent, `Last-Event-ID`/`since` replay on reconnect) or WebSocket (roundtable). Falls back to `mock_agent.py` if no agent CLI is on PATH.

### Redis key conventions
| Key | Purpose |
|-----|---------|
| `acp:prompt` | Stream: API → runner (prompt tasks) |
| `evt:conv:{id}` | Stream: runner/API → clients (streaming + group events; capped, replayable). Group adds `message` (human peer msg), `message_update` (edit/recall/reaction), `typing` (ephemeral), `members_changed` |
| `evt:user:{id}` | Stream: API → one per-user `/me/stream` (cross-conversation `notify` for unread/@-mention badges; capped, replayable) |
| `presence:{user}` | User online presence (SET ex=60; heartbeat every ~30s) |
| `hermes:clarify:req:{sid}` | List: agent → runner clarify requests (RPUSH / LPOP) |
| `hermes:clarify:resp:{sid}:{cid}` | List: runner → agent clarify answer (RPUSH / BLPOP) |
| `rl:msg:{user}` | Rate-limit counter |
| `acp:cancel:{conv}` | Cancellation signal |
| `jwt:blacklist:{jti}` | Logout token invalidation |
| `mem:consolidate:status:{user}` | Memory-consolidation status + run lock (SET NX) |
| `mem:consolidate:cooldown:{user}` | Non-admin consolidation cooldown (TTL) |

### Frontend — Vue 3 + Pinia

```
src/
├── api/         client.ts (axios + Bearer inject + auto-refresh on 401)
│                auth / agents / conversations / teams / admin / projects .ts
├── stores/      auth.ts (session, route guard)  ·  chat.ts (conversations, SSE, roundtable WS)
├── router/      index.ts — meta.requiresAdmin for admin-only routes
├── views/       ChatView · AdminView · TeamDetailView · ProjectView …
├── components/  WorkspacePanel.vue (multi-tab file preview/edit, adapter-pattern)
├── types/       index.ts — single source of all TS interfaces
└── utils/       markdown.ts (zero-dep renderer)
```

**Auth flow**: `client.ts` injects `Authorization: Bearer` on every request; a 401 triggers a single-flight refresh; refresh failure dispatches `hermes:logout` → router redirects to login.

**Streaming**: single-agent uses SSE (`EventSource`, token in query param); roundtable uses WebSocket. Both handled in `stores/chat.ts`.

**WorkspacePanel** uses an adapter pattern — callers pass `files: FileItem[]` + `adapter: WsAdapter` so the same panel works for both conversation workspace files and team knowledge files.

**Profiles ("assistants")**: stored in the `profiles` table. `GET /profiles` returns all active profiles. `POST /profiles/scan` auto-creates profiles for any registered agent that doesn't have one. Admins manage profiles in AdminView → "助手管理" tab.

### Key SQLAlchemy async pitfalls
- **Never trigger lazy-loaded relationships during response serialization** — causes `MissingGreenlet`. Explicitly query and hand-assemble DTOs (see `conversations.py` for the pattern).
- Tests must reset the engine/redis per-case to avoid `attached to a different loop` errors — see `tests/conftest.py`.
- Seeding JSONB in migrations: use `CAST(:d AS jsonb)` + `json.dumps(...)`, never pass an already-serialised string with `type_=JSONB`.

### Adding common things

**New REST endpoint**: schema in `app/schemas/<domain>.py` → logic in `app/services/<domain>_service.py` → route in `app/api/v1/<domain>.py` → register in `app/api/v1/__init__.py`.

**New DB table**: add ORM model inheriting `UUIDPrimaryKey + Timestamps`, import it in `app/db/models/__init__.py`, hand-write migration in `alembic/versions/`.

**New team permission**: add to `app/core/governance.py` `PERMISSIONS` + `_DEFAULTS`, guard routes with `team_service.require_permission(...)`.

**New frontend page**: API method in `src/api/<domain>.ts`, types in `src/types/index.ts`, view in `src/views/XxxView.vue`, route in `src/router/index.ts`.

**TS build is strict** (`noUnusedLocals`): clean up all unused imports/variables before `npm run build`.

### Frontend UI/UX layout conventions

All pages MUST follow these layout conventions for visual consistency:

1. **Page structure**: Every view uses `<div class="stage">` as the root (scroll container, `flex: 1; overflow-y: auto`).
2. **Hero header**: Admin/tool pages use `<div class="admin-hero">` with `<div class="admin-hero-row">` (badge + meta), `<h1 class="admin-title">` (with `<em>` for emphasis), and `<div class="admin-sub">` (subtitle).
3. **Centered body**: Content goes in `<div class="admin-body">` — `max-width: 1400px; margin: 0 auto; padding: 24px 40px 60px`.
4. **Cards**: Use `<div class="section-card">` with `<div class="section-head"><div class="section-title">…</div></div>` + padding body.
5. **Stat cards**: `<div class="stat-grid">` with `<div class="stat"><div class="stat-label">…</div><div class="stat-value">…</div></div>`.
6. **Two-column layout**: `<div class="col-grid">` (CSS grid, `gap: 16px`).
7. **Forms**: Use `class="cfg-input"` for inputs/selects/textareas. Buttons use `class="btn"` or `class="btn primary"`.
8. **Filter toolbars**: Use `<div class="users-toolbar">` with `<div class="filter-input">` (search) and `<button class="filter-select">` (dropdowns).
9. **Tables**: CSS grid rows — `<div class="audit-table">` with `<div class="au-row head">` (header) and `<div class="au-row">` (body rows).
10. **Topbar buttons**: Use `class="icon-btn"` with `<Icon name="…" />`. For dropdown panels, anchor with `position: relative` wrapper + `position: absolute` panel with `z-index: 800`. **If a dropdown risks overflow, prefer a direct route link instead** (`router.push('/path')`).
11. **Sidebar scroll areas**: Lists that can grow (teams, group chats) MUST have `max-height` + `overflow-y: auto` + `flex-shrink: 0` to prevent pushing other sections off-screen. The conversation list (`.convo-list`) uses `flex: 1; min-height: 0` to fill remaining space.
12. **Status tags/pills**: `<span class="fb-cat-pill">` / `<span class="fb-st-pill">` — colored border + text, `font-size: 10px; padding: 1px 6px; border-radius: 4px`.
13. **Transitions**: Dropdown panels use `<Transition name="panel-drop">` (opacity + translateY 8px over 150ms).

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
