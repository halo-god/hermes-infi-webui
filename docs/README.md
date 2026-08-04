# Hermes — 信使 · 文档中心

生产级 AI 协作平台（FastAPI · PostgreSQL · Redis · ACP · Vue 3 + TS）的完整文档。

## 目录

| 文档 | 内容 | 读者 |
|---|---|---|
| [方案设计.md](方案设计.md) | 总体架构、技术选型、ACP 接入、分阶段路线图 | 架构师 / 决策者 |
| [开发文档.md](开发文档.md) | 环境搭建、项目结构、分层约定、加接口/模型/迁移、前端开发、Cookbook | 开发 |
| [运维文档.md](运维文档.md) | 部署、配置参考、备份恢复、扩缩容、可观测、升级、故障排查、灾备 | 运维 / SRE |
| [API文档.md](API文档.md) | 全部 REST 接口、SSE/WebSocket 事件帧、鉴权、错误码 | 前后端 / 集成方 |
| [数据库设计.md](数据库设计.md) | ER 模型、各表字段、迁移、JSONB 结构、索引 | 开发 / DBA |
| [测试文档.md](测试文档.md) | 测试栈、如何运行、用例覆盖、验收 | 开发 / QA |
| [安全文档.md](安全文档.md) | 认证授权、沙箱、多租户隔离、凭证、审计、生产安全清单 | 安全 / 运维 |
| [真机联调.md](真机联调.md) | 接入真实 NousResearch `hermes acp` CLI | 运维 / 开发 |
| [确认弹窗设计.md](确认弹窗设计.md) | 确认/授权弹窗交互设计 | 前端 |
| [文件处理流程.md](文件处理流程.md) | 上传/解析/预览流水线 | 开发 |
| [企业微信SSO集成.md](企业微信SSO集成.md) | 企微扫码登录与组织映射 | 开发 / 运维 |
| [Hermes助手变更记录.md](Hermes助手变更记录.md) | hermes CLI 侧改动跟踪 | 开发 |
| [design/群聊设计.md](design/群聊设计.md) | 群聊/圆桌/频道模式设计 | 开发 |
| [沙箱部署指南.md](沙箱部署指南.md) | Agent 子进程隔离：bubblewrap/firejail 生产配置 | 运维 / 安全 |

## 5 分钟跑起来

```bash
cp .env.example .env
make up        # postgres + redis + minio + api + agent-runner + web
# Web  http://localhost:8080   ·   API 文档  http://localhost:8000/api/docs
# 登录 admin@hermes.io / Hermes@2026
```

> 无 Docker？见 [开发文档.md](开发文档.md) §3「本地裸机启动」。

## 项目状态

P0–P5 六阶段 + 真机联调全部完成，10 个集成测试基于真实 PostgreSQL + Redis 通过。详见各阶段说明（[根 README](../README.md#实现进度)）。

## 仓库结构

```
backend/     FastAPI 应用 + agent_runner（ACP 网关）+ alembic 迁移 + tests
frontend/    Vite + Vue 3 + TS（登录/聊天/圆桌/工作区/历史/后台）
docker/      compose.yaml + 三个 Dockerfile（api / web / agent-runner）
docs/        本文档中心
project/ · Hermes.html   设计原型（视觉参照）
```
