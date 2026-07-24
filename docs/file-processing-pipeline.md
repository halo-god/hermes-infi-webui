# 文件处理系统通信逻辑流程文档

> 本文档描述从用户给 AI 发送文件(聊天附件)、上传知识库文件、引用知识库,
> 到最终内容注入 AI prompt 的完整通信链路。涵盖 FastAPI 后端、Agent Runner 进程、
> ACP 子进程、对象存储四者间的数据流。
>
> **适用版本**:P0-P2 全功能 + 文件处理深度优化(Docling/magic校验/EXIF/压缩包/RAG)之后。

---

## 0. 角色与进程边界

```
┌─────────────────────────────────────────────────────────────────┐
│  浏览器 (Vue3)                                                    │
│  Composer / KnowledgeModal / FilesView                           │
└──────────────┬──────────────────────────────────┬───────────────┘
               │ multipart/form-data 上传           │ SSE 流式事件
               ▼                                    ▲
┌──────────────────────────────┐  ┌────────────────────────────────┐
│  FastAPI 进程 (app/)          │  │  Agent Runner 进程 (agent_runner/)│
│  - api/v1/* (路由层,薄)       │  │  - runner.py (主循环)            │
│  - files.py (上传处理)        │  │  - acp_client.py (JSON-RPC)      │
│  - conversation_service.py    │←→│  - session_pool.py              │
│    (dispatch 编排)            │  │  - workspace_watcher.py         │
│  - rag_service.py (向量检索)  │  │  ↓                              │
│  - team_service.py (知识库CRUD)│  │  ┌──────────────────────┐       │
└──────────┬───────────────────┘  │  │ ACP 子进程 (hermes CLI)│       │
           │                      │  │ 持有 ReAct 循环 + 工具 │       │
           ▼                      │  └──────────────────────┘       │
┌────────────────────┐            └───────────────┬────────────────┘
│  PostgreSQL         │  Redis Stream acp:prompt    │
│  - messages         │←───────────────────────────┘
│  - team_knowledge   │  evt:conv:{id} (SSE 事件流)
│  - team_knowledge_  │
│    chunks (RAG)     │
│  - conversation_    │
│    summaries        │
└────────────────────┘
           ▲
           │ asyncio.to_thread
┌──────────┴───────────┐
│  对象存储 (MinIO/S3)  │
│  原始文件字节          │
└──────────────────────┘
```

**三个进程,两种通信**:
1. **FastAPI ↔ Runner**:Redis Stream `acp:prompt`(任务下发)+ `evt:conv:{id}`(SSE 回传)
2. **Runner ↔ ACP 子进程**:JSON-RPC over stdio(`session/prompt` 请求 + `session/update` 流式通知)
3. **进程 ↔ 存储**:PostgreSQL(结构化)+ MinIO(大文件字节)

---

## 1. 聊天附件:从上传到注入 AI

### 1.1 用户上传附件(前端 → FastAPI)

```
用户在 Composer 选文件(支持多文件/拖拽)
  │
  ▼
POST /api/v1/conversations/{conv_id}/upload
  multipart/form-data
  │
  ▼
conversations.py: upload_file()
  ├─ read_upload_capped(raw, max_upload_bytes=25MB)   # 分块读,超限 HTTP 413
  ├─ name 净化(保留 \w . - 中文)                       # files.py 文件名安全
  ├─ process_upload(raw, ext, "conversations/{id}", name, content_type)
  │    │
  │    ├─ 【P2新增】validate_upload(raw, ext)           # magic number 校验防伪装
  │    ├─ 【P2新增】strip_exif(raw, ext)                # 图片 EXIF 剥离(隐私)
  │    ├─ 【P2新增】if ext in (zip,tar,gz):             # 压缩包解压
  │    │     _extract_archive(raw, ext) → 合并文本       #   zip bomb 防护
  │    │
  │    ├─ if ext in OFFICE_EXTRACTORS (docx/xlsx/pptx/csv/rtf):
  │    │     ① 原始字节 → MinIO (storage_key)
  │    │     ② _extract_doc_content(raw, ext):          # 【P2】Docling 优先
  │    │        Docling → Markdown (失败回退 openpyxl/python-docx HTML)
  │    │     ③ content = Markdown 或 HTML
  │    │
  │    ├─ elif len(raw) > 256KB or storage_backend=="minio":
  │    │     ① 原始字节 → MinIO
  │    │     ② PDF → Docling/pymupdf 文本; 文本类 → charset 检测解码
  │    │
  │    └─ else (小文件内联):
  │          文本类 → charset 检测解码; PDF → 文本提取; 其他 → base64
  │
  ├─ WorkspaceFile 入库(content=提取内容, storage_key)
  └─ 返回 file_id
```

**产出**:一个 `WorkspaceFile` 行,`content` 存提取后文本,`storage_key` 指向 MinIO 原始字节。

### 1.2 用户发送消息时引用附件(FastAPI 编排)

```
用户发送消息 + 勾选附件(stagedFiles)
  │
  ▼
POST /api/v1/conversations/{conv_id}/messages
  { text, attached_file_ids: [...], ... }
  │
  ▼
conversations.py: send_message()
  └─ conversation_service.py: dispatch()
       │
       ├─ _resolve_attached_files(db, attached_file_ids)    # line 223
       │    │
       │    ├─ 鉴权:文件必须属于本会话或用户的 __file_storage__ 虚拟会话
       │    ├─ 批量查 WorkspaceFile (防 N+1)
       │    │
       │    ├─ 【三级内联策略】按文本大小决定给 AI 多少:           # line 309
       │    │   ├─ < 30KB  → 全量内联进 prompt
       │    │   ├─ 30-100KB → 截断到 100KB + "use read_file" 提示
       │    │   └─ > 100KB → 仅元数据(文件名+路径),agent 用工具自取
       │    │
       │    └─ 【附件落盘到 Agent 工作区】                          # line 360
       │         ws_dir = {workspace_root}/{conversation_id}/attachments/
       │         ├─ PDF: 写原始字节(.pdf)
       │         ├─ Office HTML: 写 .html + .txt(纯文本版,_html_to_plain_text)
       │         └─ 图片: 写解码后字节到工作区(让 read_image 能用)
       │
       ├─ _build_attached_prompt(text, attached)              # line 671
       │    └─ 在用户文本后追加:
       │       【当前对话已引用以下文件】
       │       - 产品手册.pdf (attachments/产品手册.pdf)
       │       - data.xlsx (attachments/data.xlsx) — 文件较大，建议使用 read_file 分段读取
       │
       └─ _build_attachment_content_blocks(convo, attached)   # line 701
            └─ 为 ACP 协议构建结构化块:
               ├─ 文件类: resource_link { type, uri, name }
               └─ 图片类: image { type:"image", source:{type:"base64",...} }
```

**关键设计**:附件内容**不全量塞进 prompt**(会爆上下文)。大文件只给路径引用,让 agent 用 `read_file`/`read_image` 工具按需读取。

### 1.3 dispatch 组装 system_prompt 的完整分层顺序

```
dispatch() 组装 system_prompt(从早到晚叠加):           # conversation_service.py:1495+

  ① 【P1-2】早期对话摘要(如果有)
     ┌─ conversation_summaries 表 → 【早期对话摘要】...
     
  ② 【P1-3】staged 阶段 prompt(如果 staged_enabled)
     ┌─ _resolve_staged_profile(profile, convo.staged_stage)
     │  → 按 clarify/implement/review 选 prompt + 工具子集
     
  ③ profile.system_prompt(基础人设)
  
  ④ 【P1-1】知识库内容
     ┌─ rag_enabled? 
     │   YES + 已索引 → _build_knowledge_prompt_rag(): 向量检索 top-k chunks
     │   NO  或未索引 → 全量拼接(每条截断 2000 字,总计 8000 字)
     
  ⑤ 请求级知识引用(用户在 Composer 临时勾选的 knowledge_ids)
     ┌─ _build_request_knowledge_prompt()
     
  ⑥ 用户长期记忆
     ┌─ _build_memory_prompt(owner_id): notes / user_profile / soul
     
  ⑦ 情景记忆 + 技能(按需触发,非 always-on)
     ┌─ episodic memory: pg_trgm 检索相关历史片段
     └─ skills: trigger 匹配命中的技能内容
     
  ⑧ clarify 指令
     ┌─ 首轮: _CLARIFY_PREAMBLE(先确认需求再行动)
     └─ 后续轮: _ANTI_CLARIFY(直接执行)
```

**最终产物**:一个 `system_prompt` 字符串 + `text`(用户消息+附件引用),通过 Redis Stream 发给 Runner。

### 1.4 Runner 驱动 ACP 子进程(Runner ↔ ACP)

```
Redis Stream acp:prompt 收到任务
  │
  ▼
runner.py: handle_single(task)
  │
  ├─ 解析 task: { conversation_id, message_id, system_prompt, text, 
  │              mcp_servers, max_iterations, stage, ... }
  │
  ├─ session_pool.get(conv_id, command, cwd, on_update, ...)
  │    ├─ 复用或新建 ACPClient(子进程)
  │    └─ 工具集(MCP servers)在 session/new 时下发
  │
  ├─ 包裹 prompt:
  │    effective_text = "【角色设定】\n{system_prompt}\n【角色设定结束】\n\n{text}"
  │    (注意: system_prompt 是拼进 user prompt 前缀,非 ACP system 字段)
  │
  ├─ client.prompt(content) → ACP 子进程开始 ReAct 循环
  │    │
  │    │  子进程内部(hermes CLI,不在本仓):
  │    │  Thought → Action(read_file/write_file/search/...) → Observation → ...
  │    │
  │    │  每个 token / tool_call / thought 通过 session/update 流式回传:
  │    │
  │    ▼
  │  on_update(update) 回调:                          # runner.py:599
  │    ├─ agent_message_chunk → 发 SSE "token" 事件
  │    ├─ tool_call → 发 SSE "tool_call" 事件
  │    │    ├─ 【P0-2】熔断: tool_calls >= max_iterations → cancel session
  │    │    └─ 【P2-3】风险拦截: title 含高危工具 + 未授权 → cancel
  │    ├─ agent_thought → 发 SSE "thought" 事件
  │    ├─ plan → 发 SSE "plan" 事件
  │    ├─ usage → 记 token + 接近上限发 compression_warning
  │    └─ confirmation_request → 发 SSE 等用户确认(clarify)
  │
  ├─ prompt 完成 → _finalize: 写 Message 入库(content含 text/tool_calls/thinking/usage)
  │
  └─ 发 SSE "done" 事件
       │
       ▼
  前端 chatStream.ts 收到 done → 渲染完成
```

**关键**:`on_update` 是 Runner 侧的被动回调——ACP 子进程每产出一个事件(token/tool/thought)就调一次,Runner 转成 SSE 发给前端。**ReAct 循环完全在 ACP 子进程内部**,Runner 不参与迭代决策。

---

## 2. 知识库:从上传到 RAG 检索注入

### 2.1 上传知识库文件(写入侧)

```
管理员在 KnowledgeModal 上传文件(支持进度条)
  │
  ▼
POST /api/v1/teams/{team_id}/knowledge/upload
  │
  ▼
teams.py: upload_knowledge()
  ├─ 权限校验: knowledge.upload
  ├─ read_upload_capped()
  ├─ process_upload(raw, ext, "team-knowledge/{team_id}", name)
  │    └─ (同 1.1: magic校验 + EXIF + 压缩包 + Docling/提取器)
  │
  ├─ TeamKnowledge 入库(content=提取内容, storage_key, kind)
  │
  └─ team_service.add_knowledge() → _maybe_index_knowledge(kid)   # P1-1 RAG
       │
       ▼
  rag_service.index_knowledge(db, knowledge_id)
       │
       ├─ 取 content → 若含 HTML 走 _html_to_plain_text → 纯文本
       │
       ├─ _split_into_chunks(text, chunk_size=500, overlap=100)
       │    └─ 滑动窗口切块,最小块 30 字合并,空行过滤
       │
       ├─ embedding_service.encode(chunks) → 512 维向量
       │    └─ BAAI/bge-small-zh-v1.5(本地 sentence-transformers,~10ms/句)
       │
       ├─ 删旧 chunks → 插入新 TeamKnowledgeChunk(knowledge_id, chunk_index, content, embedding)
       │
       └─ pgvector HNSW 索引自动维护(向量 cosine 检索)
```

**ProjectDoc 同理**:`add_doc()` → `_maybe_index_project_doc()` → 写入同一张 `team_knowledge_chunks` 表(用 `project_doc_id` 列区分)。

### 2.2 对话时检索注入(读取侧)

```
用户发消息 "A级客户有什么服务?"
  │
  ▼
dispatch() → _build_knowledge_prompt(db, profile, query="A级客户有什么服务?")
  │
  ├─ 收集 profile 绑定的 knowledge_ids / folder_ids / team_ids
  ├─ 去重 → all_ids
  │
  ├─ if rag_enabled AND query 非空:
  │    └─ _build_knowledge_prompt_rag(db, all_ids, query)      # line 1248
  │         │
  │         ├─ 检查哪些 id 已索引(is_indexed)
  │         ├─ rag_service.search(db, query, indexed_ids, top_k=5)
  │         │    ├─ embedding_service.encode([query]) → query 向量
  │         │    └─ pgvector: SELECT content, embedding <=> query_vec
  │         │                   ORDER BY distance LIMIT 5      # HNSW 近邻搜索
  │         │
  │         └─ 拼接 top-k chunks:
  │            【团队知识库·检索】以下是根据你的问题检索到的相关资料片段：
  │            ---
  │            [chunk 内容1]
  │            ---
  │            [chunk 内容2]
  │            ...(截断到 rag_max_context_chars=8000)
  │
  └─ else (RAG 关闭/未索引/检索失败):
       └─ 全量拼接(每条截断 2000 字,总计 8000 字)         # legacy 路径
          【团队知识库】请在回答时参考以下资料...
          [文件名1] 正文前2000字...
          [文件名2] 正文前2000字...
```

**对比**:
- 全量拼接:无论用户问什么,都塞整篇文档(截断),浪费 token
- RAG 检索:只塞与问题相关的 5 个片段,精准省 token

---

## 3. 用户引用知识库(Composer 临时勾选)

```
用户在 Composer 点"引用知识" → KnowledgePickerModal 多选
  │
  ▼
发送消息时带 knowledge_ids: ["kid1", "kid2"]
  │
  ▼
dispatch() → _build_request_knowledge_prompt(db, knowledge_ids)   # line 1378
  │
  ├─ 批量查 TeamKnowledge(防 N+1)
  ├─ 逐条: content → 若 HTML 转 _html_to_plain_text
  ├─ 截断: 每条 2000 字,总计 8000 字
  └─ 拼接进 system_prompt(在 profile 知识库之后)
```

**区别**:
- profile 绑定的知识库 → 每次**自动**注入(_build_knowledge_prompt)
- 用户临时勾选的知识库 → 仅**本次**注入(_build_request_knowledge_prompt)

---

## 4. Agent 写文件(反向:ACP → 工作区 → DB → 前端)

```
ACP 子进程内 agent 调用 write_file 工具
  │
  ├─ 路径 A:MCP write_file 工具(经 ACP request_permission)
  │    └─ acp_client.py: 检查路径在 cwd 内 → allow_once, 否则 deny
  │
  └─ 路径 B:agent 直接写工作区文件系统(绕过 ACP)
       │
       ▼
  WorkspaceWatcher(watchdog Observer) 监听 cwd 变化          # workspace_watcher.py
       │
       ├─ on_created / on_modified / on_moved
       ├─ 防抖 0.5s + 等待 0.3s(避免半写)
       ├─ realpath 校验不逃逸 cwd
       ├─ 文本文件: read_text → storage.save_file() → 发 SSE "file" 事件
       └─ 【P2】二进制文件: 发轻量 "file" 事件(仅元数据,不存 DB)
```

**前端收到 "file" 事件** → chatStream.ts → 显示文件卡片 → 用户可预览/下载。

---

## 5. system_prompt 注入 ACP 的完整数据流(汇总)

```
┌─────────────────────────────────────────────────────────────┐
│ dispatch() 产出的 task payload(经 Redis Stream):            │
│                                                              │
│ {                                                            │
│   type: "single",                                            │
│   conversation_id, message_id, agent_id, profile_id,        │
│   text: "用户问题 + 【引用文件】路径列表",                    │
│   system_prompt: "【摘要】+【人设】+【知识库】+【记忆】+...", │
│   mcp_servers: [{name, command, args}],  # 工具集            │
│   max_iterations: 50,                     # 熔断上限          │
│   stage: "implement" | null,              # P1-3 阶段        │
│   content_blocks: [...]                   # 结构化附件(图片等)│
│ }                                                            │
└──────────────────────────┬──────────────────────────────────┘
                           │ Redis Stream acp:prompt
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ runner.py handle_single():                                   │
│                                                              │
│ effective_text = f"【角色设定】\n{system_prompt}\n【结束】    │
│                   \n\n{text}"                                │
│                                                              │
│ client.prompt(effective_text 或 content_blocks)              │
│   → ACP 子进程: system_prompt 作为 user prompt 前缀注入      │
│   → agent 看到的完整上下文 = 角色设定 + 知识 + 记忆 + 问题    │
└─────────────────────────────────────────────────────────────┘
```

**注意**:Hermes 的 system_prompt **不是**通过 ACP 协议的 `system` 字段下发的(ACP 的 system 字段未使用),而是**拼进 user prompt 文本前缀**(`【角色设定】...【结束】`)。这是现有设计。

---

## 6. 安全机制汇总

| 机制 | 位置 | 作用 |
|---|---|---|
| **magic number 校验** | `file_validation.py` validate_upload | 拒绝伪装扩展名的可执行文件(HTTP 415) |
| **上传大小限制** | `files.py` read_upload_capped | 25MB 上限,超限 HTTP 413 |
| **EXIF 剥离** | `files.py` _strip_exif | 图片上传时移除 GPS/相机元数据 |
| **压缩包防护** | `files.py` _extract_archive | zip bomb 防护(100 文件/100MB 上限)+ 路径穿越拒绝 |
| **路径穿越防护** | `files.py` safe_relative_path + confine_to_dir | 附件落盘/工作区写入不逃逸 cwd |
| **工具风险拦截** | `runner.py` tool_call 分支 | 高危(write/destructive)MCP 工具未授权 → cancel turn |
| **迭代熔断** | `runner.py` tool_call 计数 | tool_calls >= max_iterations → cancel session |
| **token 上限告警** | `runner.py` usage 分支 | 接近 256K context → compression_warning SSE |

---

## 7. 关键文件索引

| 职责 | 文件 | 关键函数 |
|---|---|---|
| 上传处理(单一真相源) | `app/core/files.py` | `process_upload` / `_extract_doc_content` / `_extract_archive` |
| magic 校验 | `app/core/file_validation.py` | `validate_upload` |
| Docling 解析 | `app/core/docling_converter.py` | `convert_bytes_to_markdown_sync` |
| embedding 服务 | `app/core/embedding.py` | `EmbeddingService.encode` |
| RAG 切块/索引/检索 | `app/services/rag_service.py` | `index_knowledge` / `search` / `_split_into_chunks` |
| 知识库 CRUD + 索引 hook | `app/services/team_service.py` | `add_knowledge` / `_maybe_index_knowledge` |
| 附件解析+落盘+注入 | `app/services/conversation_service.py` | `_resolve_attached_files` / `_build_knowledge_prompt` / `dispatch` |
| Runner 主循环 | `agent_runner/runner.py` | `handle_single` / `on_update` |
| ACP 协议 | `agent_runner/acp_client.py` | `prompt` / `new_session` / `cancel` |
| 工作区监听 | `agent_runner/workspace_watcher.py` | `WorkspaceWatcher` |
| 对象存储 | `app/core/object_storage.py` | `put` / `get` / `delete` |
