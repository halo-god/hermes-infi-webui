# API 文档

REST 基址：`/api/v1`。交互式文档（Swagger）：`/api/docs`；ReDoc：`/redoc`；OpenAPI：`/api/openapi.json`。

---

## 1. 通用约定

- **认证**：除 `/auth/login`、`/auth/refresh`、`/auth/providers`、健康检查、`/metrics` 外，均需请求头 `Authorization: Bearer <access_token>`。
- **令牌**：`access`（默认 15 min）+ `refresh`（默认 7 天，轮换式：用一次即作废）。
- **内容类型**：请求/响应 JSON；SSE 为 `text/event-stream`；WebSocket 为 JSON 文本帧。
- **错误格式**：`{"detail": "原因"}`，HTTP 状态码语义化。
- **时间**：ISO-8601（UTC）。ID：UUID（审计日志为自增 BigInt）。

### 状态码
| 码 | 含义 |
|---|---|
| 200/201/202/204 | 成功 |
| 400/422 | 参数错误 |
| 401 | 未认证 / 令牌无效过期 |
| 403 | 无权限（平台角色或团队权限不足、提供商未启用） |
| 404 | 资源不存在 |
| 409 | 冲突（如邮箱已存在、已是成员） |
| 429 | 触发限流 |
| 501 | 提供商未配置 |
| 503 | 外部依赖不可用（如 LDAP 不可达） |

---

## 2. 认证 `/auth`

### POST `/auth/login`
```jsonc
// 请求
{ "method": "local", "username": "admin@hermes.io", "password": "Hermes@2026", "remember_device": true }
// method: local | ldap | wecom（外部提供商需管理员启用，否则 403/501）
// 响应 200
{ "access_token":"…","refresh_token":"…","token_type":"bearer","expires_in":900,
  "user": { "id":"…","email":"…","name":"…","role":"super_admin","source":"local", … } }
```
LDAP：`method:"ldap"`，`username` 为域账号；首登按部门映射自动建号 + 分配角色 + 加团队。

### POST `/auth/refresh`
`{ "refresh_token": "…" }` → 新 `{access_token, refresh_token, …}`。旧 refresh 立即失效（重用 → 401）。

### POST `/auth/logout`
`{ "refresh_token": "…" }` → 204。该 refresh 加入黑名单。

### GET `/auth/me`
当前用户。

### GET `/auth/providers`
登录页可用方式：`[{id,label,enabled,kind}]`（`local` 恒 enabled，其余反映后台启用状态）。

---

## 3. 用户 `/users`

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/users/me` | 我的资料 |
| PATCH | `/users/me` | 改我的资料（name/handle/title/department/phone/timezone/bio/color） |
| GET | `/users` | 用户列表（**管理员**） |

---

## 4. 助手与 Profile

### Agent 注册表 `/agents`
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/agents` | ACP 注册表（来自 runner 探测）：`[{id,label,kind(acp_cli/builtin_mock),available,official,version,color,icon,description}]` |
| POST | `/agents/scan` | 管理员：重新扫描 PATH 上的 agent CLI 并 upsert |

### Profile（助手）`/profiles`
每个 Profile 是一个独立助手，运行在独立 HERMES_HOME（`~/.hermes/profiles/<handle>/`）。**handle 改名自动迁移 FS home 目录**；**删除自动清理 FS home**；创建/克隆/导入自动投影 SOUL.md（persona）+ 补写 config.yaml overrides。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/profiles` | 列表（含默认 agent/model） |
| POST | `/profiles` | 创建（管理员）：`{name,handle,system_prompt,default_model,…}` → 自动建隔离 home |
| PATCH | `/profiles/{id}` | 更新（管理员）：`system_prompt` 变更投影 SOUL.md；`handle` 变更迁移 home |
| DELETE | `/profiles/{id}` | 删除（管理员）：清理 DB 行 + best-effort 删除 FS home |
| POST | `/profiles/{id}/clone` | 克隆：全新隔离 home（不共享源记忆/会话） |
| GET | `/profiles/{id}/export` | 导出为可移植 JSON |
| POST | `/profiles/import` | 批量导入（按 handle 去重） |
| POST | `/profiles/scan` | 扫描 hermes FS 中的 profile（未注册的自动创建） |

### 技能 `/memory/skills`
技能与 hermes FS 双向同步：平台创建/修改 → 写 `{home}/skills/{slug}/SKILL.md`（frontmatter 保留原始 name + `platform_skill_id`，slug 冲突自动加 hash 后缀）；hermes 侧创建/修改 → 由 runner 启动 + 每日自动 scan 入库（也可手动触发）。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/memory/skills?profile_id=` | 技能列表（可按 profile 过滤） |
| POST | `/memory/skills` | 创建：`{name,description,content,trigger_conditions:{keywords:[…]},profile_id?}` |
| PATCH | `/memory/skills/{id}` | 更新（改名自动清理旧 FS 目录） |
| DELETE | `/memory/skills/{id}` | 删除（按 id 标记清理所有 home 目录） |
| POST | `/memory/skills/import` | ZIP 技能包导入（agentskills.io 布局，资源文件投影到各 home） |
| POST | `/memory/skills/scan` | Direction B：手动扫描 hermes FS 技能入库 → `{new,updated,skipped,tombstoned}` |
| GET | `/memory/skills/evolution/auto-status` | 自动演化状态（开关 + 自动应用计数）——实际路径 `skill-evolution/auto-status`（见下） |

### 技能演化 `/skill-evolution`
| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/skill-evolution/skills/{id}/evolve` | 触发技能演化（GEPA 优化器） |
| GET | `/skill-evolution/skills/{id}/evolve/status` | 演化状态 |
| GET | `/skill-evolution/auto-status` | 自动演化状态（配置开关 + 自动应用计数） |
| GET | `/skill-evolution/proposals` | 演化提案列表（审批/驳回/自动应用） |
| PATCH | `/skill-evolution/proposals/{id}` | 审批 / 驳回提案 |

### 记忆 `/memory`
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/memory?profile_id=` | 长期记忆（notes/user_profile/soul；profile 专属优先、全局回退） |
| PUT | `/memory?profile_id=` | 更新长期记忆（exact-scope：profile 写不碰全局行） |

---

## 5. 会话 `/conversations`

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/conversations?q=&pinned=` | 列表（搜索标题 / 仅置顶） |
| POST | `/conversations` | 新建：`{title?,primary_agent_id,profile_id?,first_message?}` → 含 messages 的详情 |
| GET | `/conversations/{id}` | 详情（含全部消息） |
| PATCH | `/conversations/{id}` | 改名 / 置顶：`{title?,pinned?}` |
| DELETE | `/conversations/{id}` | 删除 |
| POST | `/conversations/bulk-delete` | 批量删除：`{ids:[…]}` → `{deleted:n}` |
| PUT | `/conversations/{id}/agents` | 设活跃助手：`{agent_ids:[…]}`（>1 即圆桌模式） |
| POST | `/conversations/{id}/messages` | 发送：`{text}` → `{user_message, agent_message}`；触发 ACP；受限流（429） |
| POST | `/conversations/{id}/cancel` | 取消生成 → 202 |
| GET | `/conversations/{id}/stream?access_token=` | **SSE** 流（见 §6） |
| WS | `/conversations/{id}/ws?access_token=` | **WebSocket** 双向（见 §6） |
| GET | `/conversations/{id}/files` | 工作区文件列表 |
| GET | `/conversations/{id}/files/{file_id}` | 文件详情（含 content；MinIO 时取回内容） |

---

## 6. 流式协议（SSE 与 WebSocket）

两者承载**同一套事件帧**（来自 Redis PubSub）。单聊用 SSE，圆桌用 WebSocket（双向）。
EventSource 不能设 header，故 SSE/WS 用 `?access_token=` 传令牌。

### 客户端 → 服务端（仅 WebSocket）
```jsonc
{ "action": "send", "text": "…" }   // 发送（单/圆桌由活跃助手数决定）
{ "action": "cancel" }              // 取消
```

### 服务端 → 客户端 事件帧
```jsonc
// 单聊
{ "type":"start",     "message_id":"…" }
{ "type":"token",     "message_id":"…", "delta":"片段" }      // 流式追加
{ "type":"tool_call", "message_id":"…", "title":"…","status":"…" }
{ "type":"file",      "message_id":"…", "file_id":"…","name":"会议纪要.md","kind":"md","version":1 }
{ "type":"done",      "message_id":"…", "status":"complete","stop_reason":"end_turn" }
{ "type":"error",     "message_id":"…", "detail":"…" }

// 圆桌（多 agent 并行 + Hermes 综合）
{ "type":"rt_start",      "message_id":"…", "agents":[{"agent_id":"hermes","slot":0,"label":"…","color":"…","stance":"综合协调"}, …] }
{ "type":"rt_token",      "message_id":"…", "slot":1, "delta":"片段" }   // 路由到第 slot 路回复
{ "type":"rt_reply_done", "message_id":"…", "slot":1 }
{ "type":"merge_start",   "message_id":"…" }
{ "type":"merge_token",   "message_id":"…", "delta":"片段" }            // Hermes 综合
{ "type":"done",          "message_id":"…", "status":"complete" }
```
SSE 每 ~15s 发 `: keepalive` 心跳；收到 `done` 后服务端关闭该 SSE（浏览器 EventSource 自动重连等待下一轮）。

---

## 7. 团队 / 项目 / 任务

> 读操作需团队成员；写操作受**团队内容权限矩阵**（governance）约束。owner 恒通过。

### 团队 `/teams`
| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| GET | `/teams` | 成员 | 我所在团队 |
| POST | `/teams` | 任意登录 | 建团队（建者=owner）：`{name,handle?,tagline?,color?}` |
| GET | `/teams/{id}` | 成员 | 详情（含 my_role + 成员） |
| PATCH | `/teams/{id}` | owner/admin | 改团队 |
| DELETE | `/teams/{id}` | owner | 解散 |

### 成员
| 方法 | 路径 | 权限 |
|---|---|---|
| GET | `/teams/{id}/members` | 成员 |
| POST | `/teams/{id}/members` | `member.invite` | `{email,role}` |
| PATCH | `/teams/{id}/members/{uid}` | `member.role` | `{role}` |
| DELETE | `/teams/{id}/members/{uid}` | `member.remove` |

### 权限矩阵
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/teams/{id}/policy` | `{my_role,editable,permissions(分组目录),policy(perm×role)}` |
| PUT | `/teams/{id}/policy` | owner/admin 改矩阵：`{policy}` |

权限点：`project.create/edit/delete`、`knowledge.upload/edit/delete`、`conversation.pin`、`agent.manage`、`member.invite/role/remove`。

### 项目 / 任务
| 方法 | 路径 | 权限 |
|---|---|---|
| GET | `/teams/{id}/projects` | 成员 |
| POST | `/teams/{id}/projects` | `project.create` |
| GET | `/projects/{pid}` | 成员 |
| PATCH | `/projects/{pid}` | `project.edit` |
| DELETE | `/projects/{pid}` | `project.delete` |
| GET | `/projects/{pid}/tasks` | 成员 |
| POST | `/projects/{pid}/tasks` | `project.edit` | `{title,owner_id?,agent_id?}` |
| PATCH | `/tasks/{tid}` | `project.edit` | `{title?,status?,owner_id?,agent_id?,order_idx?}` |
| DELETE | `/tasks/{tid}` | `project.edit` |

---

## 8. 后台管理 `/admin`（均需平台管理员）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/admin/stats` | 仪表盘计数（users/teams/conversations/messages/agents） |
| GET | `/admin/users?q=` | 用户搜索 |
| POST | `/admin/users` | 建用户：`{email,name,password,role,department?,title?}` |
| PATCH | `/admin/users/{id}` | 改用户：`{role?,status?,department?,title?,is_active?}` |
| GET | `/admin/audit?action=&result=&limit=` | 审计日志 |
| GET | `/admin/session-logs?date_from=&date_to=&source=&status=&q=&page=&page_size=` | 会话日志列表（来源 personal/group，状态 success/fail/running/cancelled，分页返回 `{total,items}`） |
| GET | `/admin/session-logs/{conversation_id}` | 会话日志详情（按轮 turns：用户输入/助手回复/思考过程/耗时/模型与工具调用概览） |
| GET | `/admin/session-logs/export` | 会话日志 CSV 导出（utf-8-sig，Excel 可直接打开） |
| GET/PUT | `/admin/settings` | 系统设置（品牌 / 模型网关 / 配额）；PUT 改限流即时生效 |
| GET | `/admin/mcp-servers` | MCP 服务器目录（stdio/http，含 risk_level） |
| GET | `/admin/identity` | 身份提供商列表 |
| PATCH | `/admin/identity/{pid}` | 启用/配置：`{enabled?,config?}` |
| GET/POST | `/admin/identity/{pid}/mappings` | 部门→团队映射（增/查） |
| DELETE | `/admin/identity/mappings/{mid}` | 删映射 |

**系统设置结构**
```jsonc
{ "data": {
  "branding": {"tenant_name","display","login_tagline","accent"},
  "model_gateway": {"default_model","monthly_token_quota","rate_limit_per_min","overage"}
}, "updated_at":"…" }
```

**部门→团队映射**
```jsonc
{ "match_basis":"attribute|dn", "source_value":"Design",
  "default_role":"team_admin", "auto_join_team_id":"<team uuid>", "dept":"设计" }
```

---

## 9. 系统

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/healthz` | 存活 |
| GET | `/api/v1/readyz` | 就绪（检查 PG/Redis） |
| GET | `/metrics` | Prometheus 指标 |

---

## 10. 调用示例（curl）

```bash
# 登录拿 token
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"method":"local","username":"admin@hermes.io","password":"Hermes@2026"}' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')

# 建会话并发消息
CID=$(curl -s -X POST http://localhost:8000/api/v1/conversations \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"primary_agent_id":"hermes"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')

# 先订阅 SSE，再发消息（另一个终端发）
curl -N "http://localhost:8000/api/v1/conversations/$CID/stream?access_token=$TOKEN" &
curl -s -X POST "http://localhost:8000/api/v1/conversations/$CID/messages" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"text":"写一份启动会纪要"}'
```
