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
| `evt:conv:{id}` | Stream: runner → API (streaming events; capped, replayable) |
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
