# 优化计划：Bug 修复 + 工具栏合并 + 代码优化

## 问题 1：群聊消息工具栏显示两行

**根因**：群聊 agent 消息上，`group-actions`（回复/表情/编辑/撤回，`v-if="isGroup"`）和 `msg-tools`（复制/重新生成/分享/沉淀/智能提取，`v-if="role==='agent' && 非streaming"`）是两个条件正交的独立 div，两者同时为 true，各自渲染一行。

**修复**：将两行合并为一行。把 `msg-tools` 的按钮（复制/重新生成/分享/沉淀/生成任务/智能提取）合并进 `group-actions` div 中，这样群聊只有一行工具栏。1:1 聊天的表情行也同理合并。

具体做法：
- 群聊：`group-actions` div 内追加复制/重新生成/分享/沉淀/生成任务/智能提取按钮（仅 agent 消息且非 streaming 时显示这些）
- 1:1：表情行内追加同样的按钮
- 删除独立的 `msg-tools` div
- 统一为一个 `msg-actions` class

## 问题 2：智能提取待办在群聊不生效

**根因**：`detectTasks()` 调用 `chat.send(fullText, result.agent_id, {})` 时未传 `mentions`。群聊中 `expectAgent` 判定为 false（无 @提及且非 always 模式），AI 不回复，静默失效。

**修复**：`detectTasks` 在群聊中发送时附带 `mentions: ["__all_agents__"]`，让群聊路由触发 AI 应答。同时在 1:1 聊天中也开放此功能（去掉按钮的 `v-if="isGroup"` 限制）。

## 代码优化（6 项）

### 3. dispatch_group 单 Agent 路径漏传 skills_prompt（高）
- **文件**: `backend/app/services/conversation_service.py:1987-2020`
- **问题**: `dispatch()` 调用 `_build_skills_prompt` 并传 `matched_skill_ids` 给 `send_message`，但 `dispatch_group` 单 Agent 路径完全遗漏，群聊 @助手触发的应答不记录技能命中数据
- **修复**: 在 `effective_profile_id` 分支后补 `_build_skills_prompt` 调用 + 传 `matched_skill_ids`

### 4. chatStream `_lastCancelRefresh` 泄漏（高）
- **文件**: `frontend/src/stores/chatStream.ts:30-31,77,160`
- **问题**: 模块级单变量只保存最后一次注册的 cancel 函数，快速切会话时旧 refreshTimer 泄漏
- **修复**: 改为返回 disposer 模式 -- `registerStreamHandlers` 返回 cleanup 函数，`chat.ts` 的 `setupStreamHandlers` 保存并在下次注册前调用

### 5. Sidebar computed O(F×C) 重复遍历（中）
- **文件**: `frontend/src/components/Sidebar.vue:68-97`
- **问题**: `folderGroups` 对每个 folder 都 `filter` 全部 `personalConversations`，O(文件夹×会话)
- **修复**: 改为单次遍历建 `Map<folder_id, Conversation[]>`，再映射到 folders

### 6. 圆桌卡片重复调用 profileForEntity（中）
- **文件**: `frontend/src/views/ChatView.vue:1008-1010`
- **问题**: 每个 rt-card 对同一 `r` 调用 3-4 次 `profileDisplay(profileForEntity(...))`，每次触发 `chat.profiles.find` 线性扫描
- **修复**: 在模板外用 `getRtProfile(r)` 函数返回完整 Profile 对象，模板内直接访问属性

### 7. AdminView resultFilter 服务端+客户端双重过滤（低）
- **文件**: `frontend/src/views/AdminView.vue:353,425`
- **问题**: `loadAudit` 把 `resultFilter` 发给服务端，`filteredAudit` 又在客户端过滤一遍
- **修复**: `filteredAudit` 不再重复过滤 `resultFilter`（信任服务端已过滤）

### 8. runner.py `_task_retries` 死代码（低）
- **文件**: `backend/agent_runner/runner.py:88,479`
- **问题**: 重试逻辑已改用 `_attempt` 字段，`_task_retries` dict 从未被写入
- **修复**: 删除初始化和 pop 调用

## 验证
- `ruff check .` 通过
- `npm run type-check` + `npm run build` 通过
- 重启后端 + runner