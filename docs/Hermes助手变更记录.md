# Hermes Agent 部署改动清单

> 所有改动基于 hermes-agent 主分支。部署时需要逐个 apply。

## 1. 依赖安装

```bash
cd ~/.hermes/hermes-agent
source .venv/bin/activate
pip install redis
```

`clarify` 工具的 ACP 回调需要 `redis` 包来桥接 agent ↔ runner 通信。

---

## 2. toolsets.py — hermes-acp 工具集加入 clarify

**文件**: `toolsets.py`  
**改动**: 在 `hermes-acp` 工具集的 tools 列表中加入 `"clarify"`

```diff
     "hermes-acp": {
-        "description": "Editor integration (VS Code, Zed, JetBrains) — coding-focused tools without messaging, audio, or clarify UI",
+        "description": "Editor integration (VS Code, Zed, JetBrains) and web app backend — includes clarify for interactive confirmation",
         "tools": [
             ...
             "execute_code", "delegate_task",
+            "clarify",
         ],
```

**原因**: ACP 模式下 agent 默认不包含 clarify 工具，导致模型无法调用确认弹窗。

---

## 3. acp_adapter/session.py — clarify_callback 桥接 Redis（协议 v2：LIST + BLPOP）

**文件**: `acp_adapter/session.py`  
**位置**: `SessionManager._create_agent()` 方法末尾，`return agent` 之前  
**改动**: 添加 `_acp_clarify_callback` 函数并绑定到 `agent.clarify_callback`

> **v2 协议说明**：旧版（GET + pubsub）存在结构性竞态——runner 的应答可能在
> callback 订阅 pubsub 之前发出，导致 agent 干等 5 分钟超时。v2 改用
> RPUSH/BLPOP：即使应答先于等待到达，BLPOP 也会立即返回，无需任何时序协调。

```python
        # ── Clarify callback: bridge agent's clarify tool → Redis (v2) ──
        # RPUSH the request onto a per-session list; the runner LPOPs it and
        # shows a confirmation modal. Then BLPOP the per-clarify response list —
        # race-free: BLPOP returns even if the answer was pushed before we wait.
        def _acp_clarify_callback(question: str, choices=None) -> str:
            import redis as _sync_redis
            import uuid as _uuid

            clarify_id = _uuid.uuid4().hex[:12]
            req_key = f"hermes:clarify:req:{session_id}"
            resp_key = f"hermes:clarify:resp:{session_id}:{clarify_id}"

            data = {
                "clarify_id": clarify_id,
                "question": question,
                "options": list(choices) if choices else [],
            }
            try:
                r = _sync_redis.Redis.from_url(
                    os.environ.get("REDIS_URL", "redis://localhost:6379")
                )
                r.rpush(req_key, json.dumps(data))
                r.expire(req_key, 600)
            except Exception:
                logger.warning("Failed to write clarify request to Redis", exc_info=True)
                return json.dumps({"error": "Redis unavailable"})

            # Block (agent thread) until the runner pushes the answer, max 5 min.
            res = r.blpop(resp_key, timeout=300)
            if res is None:
                return json.dumps({"choice": "超时"})
            val = res[1]
            return val.decode("utf-8") if isinstance(val, bytes) else str(val)

        agent.clarify_callback = _acp_clarify_callback
```

**Redis Key 约定（v2）**:
| Key | 类型 | 用途 |
|-----|------|------|
| `hermes:clarify:req:{session_id}` | LIST | agent → runner: 确认请求队列 (RPUSH/LPOP, TTL 600s) |
| `hermes:clarify:resp:{session_id}:{clarify_id}` | LIST | runner → agent: 用户应答 (RPUSH/BLPOP, TTL 60s) |

**流程**:
1. Agent 调用 clarify 工具 → callback `RPUSH` 请求到 `clarify:req` 队列
2. Callback `BLPOP clarify:resp:{sid}:{clarify_id}` 阻塞等待
3. Runner 在 prompt 轮询中 `LPOP clarify:req` → 按策略自动应答或发 SSE 弹窗
4. 用户选择 → API 确认 → runner `RPUSH` 应答（取消/超时也会推送，agent 永不悬挂）
5. BLPOP 返回 → agent 继续

**兼容性**: runner 的 `clarify_protocol=dual`（默认）同时消费旧 `clarify_pending`
key 并双写旧应答格式，所以未更新此补丁的旧 agent 仍可工作；全部部署更新后可将
runner 配置切为 `clarify_protocol=v2`。

---

## 4. (可选) gateway/run.py + tools/approval.py — 审批提示中文化

这两个文件是审批系统的 i18n 改动，与确认弹窗无关，但如果你也需要中文环境：

- `gateway/run.py` L17330: 危险命令审批提示英→中
- `tools/approval.py` L1474: execute_code 描述英→中

---

## 部署检查清单

```bash
# 1. 安装依赖
cd ~/.hermes/hermes-agent && source .venv/bin/activate && pip install redis

# 2. Apply 代码改动 (3个文件必须, 2个可选)
# toolsets.py — hermes-acp 加 clarify
# acp_adapter/session.py — clarify_callback
# gateway/run.py — 审批中文化 (可选)
# tools/approval.py — 审批中文化 (可选)

# 3. 验证
python -c "import redis; print('redis OK')"
grep -n "clarify" toolsets.py  # 应看到 clarify 在 hermes-acp 列表中
grep -n "clarify_callback" acp_adapter/session.py  # 应看到绑定代码

# 4. 重启 runner
# runner 侧代码在 hermes-python 项目中，不在 hermes-agent 中
```
