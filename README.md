# Hermes Python

A self-hosted AI agent platform built on [Hermes Agent](https://github.com/NousResearch/hermes-agent). Multi-user, team-aware, with a web workspace for managing conversations, files, and projects — all powered by ACP (Agent Client Protocol) sessions running in the background.

## What This Is

Hermes Agent is a CLI-first AI coding agent. Hermes Python wraps it with a **web application layer**:

- **Multi-user auth** — JWT-based login, role-based access control (admin / member)
- **Team collaboration** — create teams, invite members, manage shared conversations and knowledge bases
- **Project management** — organize conversations into projects with tasks and file tracking
- **ACP agent sessions** — each conversation runs through an isolated Hermes Agent process via ACP (JSON-RPC over stdio)
- **Real-time streaming** — SSE for single-agent responses, WebSocket for roundtable multi-agent sessions
- **Workspace files** — AI-generated files are versioned, editable, and previewable in-browser
- **Assistant profiles** — multiple Hermes Agent configurations (different models, tools, personalities)

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.11 + FastAPI + SQLAlchemy 2.0 (async) |
| Database | PostgreSQL 16 |
| Cache / PubSub | Redis 7 |
| Object Storage | MinIO (or inline DB storage) |
| Agent Runtime | Hermes Agent via ACP (JSON-RPC over stdio) |
| Frontend | Vue 3 + TypeScript + Pinia + Naive UI |
| Build | Vite 5, Docker Compose, Alembic migrations |

## Quick Start

### Docker (recommended)

```bash
make up
```

This starts Postgres, Redis, MinIO, the API server, and the web frontend. First run takes a few minutes to build.

- **Web**: http://localhost:8080
- **API docs**: http://localhost:8000/api/docs
- **Default login**: `admin@hermes.io` / `Hermes@2026`

Other commands:

```bash
make down      # stop all services
make fresh     # stop + wipe volumes (clean slate)
make logs      # tail API logs
make migrate   # run alembic migrations
make seed      # re-seed the super-admin account
```

### Bare-metal

See [DEPLOYMENT.md](DEPLOYMENT.md) for running without Docker, including systemd service examples.

```bash
# Backend
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
alembic upgrade head
python -m app.seed
uvicorn app.main:app --reload --port 8000

# Agent runner (separate terminal)
cd backend
python -m agent_runner.runner

# Frontend (separate terminal)
cd frontend
npm install
npm run dev    # http://localhost:5173
```

Or use the provided scripts:

```bash
./start-api.sh      # FastAPI on :8001
./start-agent.sh    # ACP agent runner
./start-web.sh      # Vite preview on :5173
```

## Architecture

```
┌──────────────┐     SSE/WS      ┌──────────────┐
│   Vue 3 SPA  │ ◄──────────────► │   FastAPI    │
│  (frontend)  │    /api/*        │   (backend)  │
└──────────────┘                  └──────┬───────┘
                                         │
                              ┌──────────┼──────────┐
                              │          │          │
                         ┌────▼───┐ ┌────▼───┐ ┌───▼────┐
                         │Postgres│ │ Redis  │ │ MinIO  │
                         └────────┘ └───┬────┘ └────────┘
                                        │
                                   ┌────▼────┐
                                   │  Agent  │
                                   │ Runner  │
                                   └────┬────┘
                                        │ ACP (stdio)
                                   ┌────▼────┐
                                   │ Hermes  │
                                   │  Agent  │
                                   └─────────┘
```

### Backend

Four-layer architecture, strictly one-way:

```
HTTP/WS  →  app/api/v1/*.py        Routes (thin)
            app/services/*.py      Business logic (thick)
            app/db/models/*.py     SQLAlchemy ORM
            PostgreSQL / Redis
```

Key modules:

| Directory | Purpose |
|-----------|---------|
| `app/api/v1/` | REST routes — auth, conversations, teams, admin, agents, analytics |
| `app/services/` | Business logic — conversation orchestration, team permissions, auth |
| `app/db/models/` | ORM models — user, conversation, agent, team, workspace, audit |
| `app/core/` | Cross-cutting — security (argon2id + JWT), RBAC, Redis, governance |
| `agent_runner/` | ACP session consumer — reads from Redis Stream, drives Hermes Agent |
| `alembic/` | Hand-written migrations (`000N_*.py`) |

### Agent Runner

A separate process that:

1. Consumes prompt tasks from Redis Stream `acp:prompt`
2. Spawns Hermes Agent sessions via ACP (JSON-RPC over stdio)
3. Streams results back via Redis PubSub (`chan:conv:{id}`)
4. Writes conversation history and workspace files to the database

The API layer subscribes to PubSub and forwards events to clients via SSE (single-agent) or WebSocket (roundtable).

### Frontend

```
src/
├── api/           Axios clients with auto-refresh (auth, conversations, teams, admin)
├── stores/        Pinia stores (auth, chat, notifications)
├── router/        Vue Router with admin guards
├── views/         Pages — Chat, Admin, Teams, Projects, Analytics, Terminal
├── components/    Reusable — Composer, WorkspacePanel, Sidebar, Modals
├── types/         TypeScript interfaces (single source of truth)
├── utils/         Markdown renderer (zero-dependency)
├── composables/   useTheme, useStream, usePresence
└── i18n/          Chinese + English locale strings
```

## Key Features

### Conversations

- Create conversations with any registered assistant profile
- Real-time streaming with typing indicators and execution step tracking
- Message search, fork (branch from any point), export (Markdown / JSON)
- Smart follow-up suggestion chips based on response content
- Knowledge base injection — attach team knowledge items to prompts

### Teams & Collaboration

- Create teams, invite members with role-based permissions
- Shared conversations visible to all team members
- Team knowledge bases — upload and manage reference documents
- Permission matrix: create, read, manage conversations and knowledge

### Projects

- Organize conversations into projects with task tracking
- File upload and workspace management per project
- Task status tracking (todo / in-progress / done)

### Admin Panel

- User management — create, activate, deactivate accounts
- Agent management — register and configure assistant profiles
- System settings — branding, model configuration
- Audit log — track all significant actions
- Agent scanner — auto-discover Hermes Agent installations

### Workspace

- AI-generated files are versioned and editable in-browser
- Multi-tab file editor with syntax highlighting
- File diff viewer for tracking changes
- MinIO or database-backed storage (configurable)

## Configuration

All backend configuration is in `backend/app/config.py` via `pydantic-settings`. Environment variables override defaults.

Key settings:

| Variable | Default | Purpose |
|----------|---------|---------|
| `DATABASE_URL` | `postgresql+asyncpg://hermes:hermes@localhost:5432/hermes` | Database connection |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection |
| `SECRET_KEY` | `change-me-in-production` | JWT signing key |
| `STORAGE_BACKEND` | `db` | `db` (inline) or `minio` |
| `HERMES_BIN` | `hermes` | Path to Hermes Agent binary |
| `HERMES_HOME` | `~/.hermes` | Hermes Agent home directory |
| `VITE_API_PROXY_TARGET` | `http://localhost:8000` | Dev proxy target (frontend `.env.local`) |

## Development

### Backend

```bash
cd backend
ruff check .              # lint
pytest                    # all tests
pytest tests/test_foo.py -k test_name  # single test
```

### Frontend

```bash
cd frontend
npm run type-check   # vue-tsc --noEmit
npm run build        # type-check + vite build
npm run dev          # dev server with hot reload
```

TypeScript build is strict (`noUnusedLocals`). Clean up unused imports before building.

### Adding a New API Endpoint

1. Schema in `app/schemas/<domain>.py`
2. Service logic in `app/schemas/<domain>_service.py`
3. Route in `app/api/v1/<domain>.py`
4. Register in `app/api/v1/__init__.py`

### Adding a New Database Table

1. ORM model in `app/db/models/` inheriting `UUIDPrimaryKey + Timestamps`
2. Import in `app/db/models/__init__.py`
3. Hand-write migration in `alembic/versions/000N_*.py`

### Adding a Frontend Page

1. API client in `src/api/<domain>.ts`
2. Types in `src/types/index.ts`
3. View in `src/views/XxxView.vue`
4. Route in `src/router/index.ts`

## Project Structure

```
hermes-python/
├── backend/
│   ├── app/
│   │   ├── api/v1/          # REST routes
│   │   ├── services/        # Business logic
│   │   ├── db/models/       # SQLAlchemy models
│   │   ├── schemas/         # Pydantic response models
│   │   ├── core/            # Security, RBAC, Redis, governance
│   │   └── config.py        # All configuration
│   ├── agent_runner/        # ACP agent runner (separate process)
│   ├── alembic/             # Database migrations
│   └── tests/               # Pytest suite
├── frontend/
│   └── src/
│       ├── api/             # Axios API clients
│       ├── stores/          # Pinia state management
│       ├── views/           # Page components
│       ├── components/      # Reusable components
│       ├── types/           # TypeScript interfaces
│       └── utils/           # Utilities (markdown, etc.)
├── docker/
│   └── compose.yaml         # Docker Compose stack
├── start-api.sh             # Bare-metal: FastAPI
├── start-agent.sh           # Bare-metal: Agent runner
├── start-web.sh             # Bare-metal: Vite preview
├── DEPLOYMENT.md            # Bare-metal deployment guide
├── Makefile                 # Docker shortcuts
└── README.md                # This file
```

## License

Private — internal project.
