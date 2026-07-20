# Hermes Infi WebUI

基于 [Hermes Agent](https://github.com/NousResearch/hermes-agent) 的自托管 AI Agent 平台 Web 界面。多用户、团队协作、Web 工作区，通过 ACP（Agent Client Protocol）驱动后台 Agent 会话。

**[English](README_EN.md)** | 中文

## 这是什么

Hermes Agent 是一个 CLI 优先的 AI 编程 Agent。Hermes Infi WebUI 在此基础上加了一层 **Web 应用**：

- **多用户认证** — JWT 登录，角色权限控制（管理员 / 普通用户）
- **团队协作** — 创建团队、邀请成员、共享对话和知识库
- **项目管理** — 将对话组织到项目中，带任务跟踪和文件管理
- **ACP Agent 会话** — 每个对话通过 ACP（JSON-RPC over stdio）运行独立的 Hermes Agent 进程
- **实时流式传输** — SSE 单 Agent 响应，WebSocket 圆桌多 Agent 会话
- **工作区文件** — AI 生成的文件可版本管理、在线编辑、浏览器预览
- **助手配置** — 多种 Hermes Agent 配置（不同模型、工具、人格）

## 技术栈

| 层级 | 技术 |
|------|------|
| 后端 | Python 3.11 + FastAPI + SQLAlchemy 2.0 (async) |
| 数据库 | PostgreSQL 16 |
| 缓存/消息 | Redis 7 |
| 对象存储 | MinIO（或内联 DB 存储） |
| Agent 运行时 | Hermes Agent via ACP (JSON-RPC over stdio) |
| 前端 | Vue 3 + TypeScript + Pinia + Naive UI |
| 构建 | Vite 5、Docker Compose、Alembic 迁移 |

## 快速开始

### Docker（推荐）

```bash
make up
```

首次运行会构建镜像，启动 Postgres、Redis、MinIO、API 和 Web 前端。

- **Web**: http://localhost:8080
- **API 文档**: http://localhost:8000/api/docs
- **默认账号**: `admin@hermes.io` / 密码见 `.env` 中的 `FIRST_ADMIN_PASSWORD`

其他命令：

```bash
make down      # 停止所有服务
make fresh     # 停止并清除数据卷（重置）
make logs      # 查看 API 日志
make migrate   # 运行数据库迁移
make seed      # 重新创建管理员账号
```

### 裸机部署

详见 [DEPLOYMENT.md](DEPLOYMENT.md)，包含 systemd 服务示例。

```bash
# 后端
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
alembic upgrade head
python -m app.seed
uvicorn app.main:app --reload --port 8000

# Agent Runner（另一个终端）
cd backend
python -m agent_runner.runner

# 前端（另一个终端）
cd frontend
npm install
npm run dev    # http://localhost:5173
```

或使用提供的启动脚本：

```bash
./start-api.sh      # FastAPI :8001
./start-agent.sh    # Agent Runner
./start-web.sh      # Vite 预览 :5173
```

## 架构

```
┌──────────────┐     SSE/WS      ┌──────────────┐
│   Vue 3 SPA  │ ◄──────────────► │   FastAPI    │
│   (前端)     │    /api/*        │   (后端)     │
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

### 后端 — 四层架构，严格单向

```
HTTP/WS  →  app/api/v1/*.py        路由层（薄）
            app/services/*.py      业务逻辑（厚）
            app/db/models/*.py     SQLAlchemy ORM
            PostgreSQL / Redis
```

| 目录 | 用途 |
|------|------|
| `app/api/v1/` | REST 路由 — 认证、对话、团队、管理、Agent、分析 |
| `app/services/` | 业务逻辑 — 对话编排、团队权限、认证 |
| `app/db/models/` | ORM 模型 — 用户、对话、Agent、团队、工作区、审计 |
| `app/core/` | 通用模块 — 安全(argon2id+JWT)、RBAC、Redis、治理 |
| `agent_runner/` | ACP 会话消费者 — 从 Redis Stream 读取，驱动 Hermes Agent |
| `alembic/` | 手写迁移 (`000N_*.py`) |

### Agent Runner

独立进程，职责：

1. 从 Redis Stream `acp:prompt` 消费提示任务
2. 通过 ACP (JSON-RPC over stdio) 启动 Hermes Agent 会话
3. 通过 Redis PubSub (`chan:conv:{id}`) 流式回传结果
4. 将对话历史和工作区文件写入数据库

API 层订阅 PubSub，通过 SSE（单 Agent）或 WebSocket（圆桌）转发给客户端。

### 前端

```
src/
├── api/           Axios 客户端，自动刷新 token
├── stores/        Pinia 状态管理（auth, chat, notifications）
├── router/        Vue Router，含管理员路由守卫
├── views/         页面 — 对话、管理、团队、项目、分析、终端
├── components/    可复用组件 — 编辑器、工作区面板、侧边栏、弹窗
├── types/         TypeScript 接口定义（唯一事实来源）
├── utils/         工具函数（Markdown 渲染，零依赖）
├── composables/   组合式函数（useTheme, useStream, usePresence）
└── i18n/          中英文本地化
```

## 核心功能

### 对话

- 选择任意助手配置创建对话
- 实时流式传输，带打字指示器和执行步骤跟踪
- 消息搜索、分叉（从任意节点分支）、导出（Markdown / JSON）
- 根据回复内容生成智能追问建议
- 知识库注入 — 将团队知识库内容附加到提示词

### 团队与协作

- 创建团队、邀请成员、基于角色的权限控制
- 团队成员共享对话
- 团队知识库 — 上传和管理参考文档
- 权限矩阵：创建、读取、管理对话和知识库

### 项目

- 将对话组织到项目中，带任务跟踪
- 文件上传和项目级工作区管理
- 任务状态跟踪（待办 / 进行中 / 已完成）

### 管理面板

- 用户管理 — 创建、激活、停用账号
- Agent 管理 — 注册和配置助手配置文件
- 系统设置 — 品牌、模型配置
- 审计日志 — 跟踪所有重要操作
- Agent 扫描 — 自动发现 Hermes Agent 安装

### 工作区

- AI 生成的文件可版本管理、在线编辑
- 多标签文件编辑器，带语法高亮
- 文件差异查看器
- MinIO 或数据库存储（可配置）

## 配置

所有后端配置在 `backend/app/config.py`，使用 `pydantic-settings`。环境变量覆盖默认值。

| 变量 | 默认值 | 用途 |
|------|--------|------|
| `DATABASE_URL` | `postgresql+asyncpg://hermes:hermes@localhost:5432/hermes` | 数据库连接 |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis 连接 |
| `SECRET_KEY` | `change-me-in-production` | JWT 签名密钥 |
| `STORAGE_BACKEND` | `db` | `db`（内联）或 `minio` |
| `HERMES_BIN` | `hermes` | Hermes Agent 二进制路径 |
| `HERMES_HOME` | `~/.hermes` | Hermes Agent 主目录 |
| `VITE_API_PROXY_TARGET` | `http://localhost:8000` | 开发代理目标（前端 `.env.local`） |

## 开发

### 后端

```bash
cd backend
ruff check .              # 代码检查
pytest                    # 全部测试
pytest tests/test_foo.py -k test_name  # 单个测试
```

### 前端

```bash
cd frontend
npm run type-check   # vue-tsc --noEmit
npm run build        # 类型检查 + vite 构建
npm run dev          # 开发热重载
```

TypeScript 构建是严格的（`noUnusedLocals`），构建前清理未使用的导入。

### 添加新 API 端点

1. Schema: `app/schemas/<domain>.py`
2. 业务逻辑: `app/services/<domain>_service.py`
3. 路由: `app/api/v1/<domain>.py`
4. 注册: `app/api/v1/__init__.py`

### 添加新数据库表

1. ORM 模型继承 `UUIDPrimaryKey + Timestamps`，放在 `app/db/models/`
2. 在 `app/db/models/__init__.py` 中导入
3. 手写迁移: `alembic/versions/000N_*.py`

### 添加新前端页面

1. API 客户端: `src/api/<domain>.ts`
2. 类型: `src/types/index.ts`
3. 页面: `src/views/XxxView.vue`
4. 路由: `src/router/index.ts`

## 项目结构

```
hermes-infi-webui/
├── backend/
│   ├── app/
│   │   ├── api/v1/          # REST 路由
│   │   ├── services/        # 业务逻辑
│   │   ├── db/models/       # SQLAlchemy 模型
│   │   ├── schemas/         # Pydantic 响应模型
│   │   ├── core/            # 安全、RBAC、Redis、治理
│   │   └── config.py        # 所有配置
│   ├── agent_runner/        # Agent Runner（独立进程）
│   ├── alembic/             # 数据库迁移
│   └── tests/               # Pytest 测试
├── frontend/
│   └── src/
│       ├── api/             # Axios API 客户端
│       ├── stores/          # Pinia 状态管理
│       ├── views/           # 页面组件
│       ├── components/      # 可复用组件
│       ├── types/           # TypeScript 接口
│       └── utils/           # 工具函数
├── docker/
│   └── compose.yaml         # Docker Compose
├── start-api.sh             # 裸机：FastAPI
├── start-agent.sh           # 裸机：Agent Runner
├── start-web.sh             # 裸机：Vite 预览
├── DEPLOYMENT.md            # 裸机部署指南
├── Makefile                 # Docker 快捷命令
└── README.md                # 本文件
```

## 许可

内部项目，私有使用。
