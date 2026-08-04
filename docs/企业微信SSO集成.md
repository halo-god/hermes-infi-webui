# Hermes Web UI 企业微信 SSO 接入指南

>本文档基于 Hermes Web UI 的实际部署经验整理，提供给 IT 管理员或开发人员参考。

---

## 一、接入流程概览

```
用户点击工作台应用
        ↓
企业微信将用户导至 https://your-domain.com/api/v1/auth/wecom/silent
        ↓
后端返回 302 跳转到企业微信 OAuth authorize（scope=snsapi_base）
        ↓
企业微信自动授权，带 code 回调到 https://your-domain.com/api/v1/auth/wecom/callback?code=xxx
        ↓
后端交换 code → userid → 用户详情 → 创建/更新本地账户 → 签发 JWT
        ↓
后端返回 HTML，将 token 传递到前端 /login#access_token=xxx&refresh_token=xxx
        ↓
前端提取 token，存入 tokenStore，bootstrap 认证状态，跳转到首页
```

---

## 二、需要提供的信息

### 1. 企业微信基础信息

| 参数 | 位置 | 说明 |
|------|------|------|
| 企业 ID (corp_id) | 管理后台 → 我的企业 → 企业ID | 例：`ww1234567890abcdef` |
| AgentId | 管理后台 → 应用管理 → 自建应用 → 查看 AgentId | 例：`1000001` |
| Secret | 同上，点击「查看」获取 | 例：`xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx` |

### 2. 域名与证书

企业微信 OAuth 和工作台入口必须 **HTTPS，且使用可信 CA 证书**。

| 需要项 | 说明 |
|--------|------|
| 正式域名 | 例：`your-domain.com`，DNS A 记录指向服务器 IP |
| SSL 证书 | 可信 CA 签发（如 DigiCert、Let's Encrypt、公司现有通配符证书）；需有 `.crt`/`.pem` + `.key` |
| 服务器公网 IP | 用于企业微信「企业可信 IP」配置，可通过 `curl -4 https://httpbin.org/ip` 获取 |

### 3. 认证文件

企业微信要求验证域名所有权，需将一个 `.txt` 验证文件放置到域名根目录。

- 文件名：`WW_verify_xxxxxxxx.txt` （企业微信后台下载）
- 存放路径：`frontend/dist/WW_verify_xxxxxxxx.txt`
- 访问地址：`https://your-domain.com/WW_verify_xxxxxxxx.txt`

---

## 三、后端配置步骤

### 1. 在数据库中配置企业微信 Provider

可通过管理后台 → 身份认证 → 企业微信配置页面填写，或直接写入 `identity_providers` 表：

```json
{
  "id": "wecom",
  "label": "企业微信",
  "enabled": true,
  "config": {
    "corp_id": "YOUR_CORP_ID",
    "agent_id": "YOUR_AGENT_ID",
    "app_secret": "YOUR_APP_SECRET",
    "redirect_uri": "https://your-domain.com/api/v1/auth/wecom/callback",
    "silent_redirect_uri": "https://your-domain.com/api/v1/auth/wecom/callback"
  }
}
```

>注意：`app_secret` 使用前端保存时，管理后台会有意留空缺省机制。如果前端发送空字符串，后端会保留已有 secret，不会覆盖。

### 2. CORS 配置

在 `backend/.env` 中添加 HTTPS 域名：

```env
CORS_ORIGINS=["http://localhost:5173","https://your-domain.com"]
```

### 3. 邮箱校验修复（重要）

企业微信用户可能没有邮箱。后端自动生成占位符邮箱时，需确保不会被 Pydantic `EmailStr` 拒绝。建议修改 `schemas/user.py`：

```python
# 原来
class UserBase(BaseModel):
    email: EmailStr

# 改为
class UserBase(BaseModel):
    email: str
```

>同时，`auth_providers/wecom.py` 中的占位符邮箱建议使用真实域名（如 `@wecom.your-domain.com`），避免 `.local` 被邮箱验证库拒绝。

---

## 四、前端配置步骤

### 1. Vite preview allowedHosts

在 `frontend/vite.config.ts` 中添加：

```typescript
export default defineConfig(({ mode }) => {
  // ...
  return {
    // ...
    preview: {
      host: true,
      port: 5173,
      allowedHosts: ["your-domain.com"],
    },
  };
});
```

### 2. 登录页处理工作台回调

在 `frontend/src/views/LoginView.vue` 的 `onMounted` 中添加 hash 解析逻辑：

```typescript
onMounted(async () => {
  // 原有代码...

  // 处理企业微信工作台回调的 token
  const hash = window.location.hash;
  if (hash && hash.includes("access_token=")) {
    const params = new URLSearchParams(hash.replace("#", ""));
    const accessToken = params.get("access_token");
    const refreshToken = params.get("refresh_token");
    const hashError = params.get("error");

    if (hashError) {
      wecomError.value = decodeURIComponent(hashError);
    } else if (accessToken && refreshToken) {
      tokenStore.set(accessToken, refreshToken);
      history.replaceState(null, "", window.location.pathname + window.location.search);
      try {
        await auth.bootstrap();
        if (auth.isAuthenticated) {
          router.replace((route.query.redirect as string) || "/");
        }
      } catch {
        wecomError.value = "登录状态恢复失败，请重试";
      }
    }
  }
});
```

>原因：工作台 WebView 的 `localStorage` 与标准浏览器隔离，且没有 `window.opener`，无法通过 postMessage 传递 token。

---

## 五、企业微信后台配置步骤

### 1. 域名验证

1. 登录企业微信管理后台（https://work.weixin.qq.com/wework_admin）
2. 应用管理 → 自建应用 → 选择 Hermes 应用
3. 下滑到「网页授权及JS-SDK」
4. 点击「设置可信域名」
5. 填写你的域名（如 `your-domain.com`）
6. 上传验证文件 `WW_verify_xxxxxxxx.txt`
7. 保存

### 2. 应用可见范围

确保测试用户在应用的「可见范围」内，否则会提示"没有访问权限"。

### 3. 应用主页

将应用主页设为免登入口：

```
https://your-domain.com/api/v1/auth/wecom/silent
```

>这是后端 `/wecom/silent` 接口，会返回 302 跳转到企业微信 OAuth authorize（scope=snsapi_base）。

### 4. 企业可信 IP

1. 同一个页面，找到「企业可信IP」
2. 点击「配置」
3. 填入服务器的公网出口 IP（可通过 `curl -4 https://httpbin.org/ip` 获取）
4. 保存

>如果服务器有多个出口 IP，建议将可能的 IP 段都加入白名单，或先填 `0.0.0.0/0` 测试确认是这个问题，再改为具体 IP。

### 5. 通讯录权限

确保应用有通讯录读取权限，否则无法通过 userid 获取用户详情。

---

## 六、反向代理配置（Caddy/Nginx）

### Caddy 示例

```caddy
{
	auto_https off
}

your-domain.com:443 {
	tls /path/to/cert.pem /path/to/key.pem

	# API backend - 使用 handle 保留 /api 前缀
	handle /api/* {
		reverse_proxy 127.0.0.1:8001
	}

	# Web frontend (Vite preview)
	reverse_proxy 127.0.0.1:5173
}
```

>注意：使用 `handle` 而非 `handle_path`，否则 `/api/` 前缀会被去掉导致 404。

---

## 七、常见问题排查

### 1. 提示"not allow to access from your ip"（errcode=60020）

**原因**：服务器出口 IP 不在企业微信白名单内。

**解决**：
1. 通过 `curl -4 https://httpbin.org/ip` 获取服务器出口 IP
2. 在企业微信管理后台 → 应用管理 → 自建应用 → 企业可信IP 中添加该 IP

### 2. 提示"企业微信未返回用户 ID，可能用户未授权"

**原因**：企业微信 `getuserinfo` 接口返回的字段名是 `UserId`（大写 U），而代码可能只读取了 `userid`。

**解决**：修改 `auth_providers/wecom.py`：

```python
# 兼容大小写
userid = data.get("UserId") or data.get("userid") or data.get("openid")
```

### 3. 提示"登录成功"但还是在登录页

**原因 A**：后端回调 HTML 用 `localStorage.setItem()` 存 token，但工作台 WebView 的 localStorage 与标准浏览器隔离。

**原因 B**：email 校验失败导致 `/auth/me` 500，token 被清除。如果 WeCom 用户邮箱是占位符 `xxx@wecom.local`，Pydantic `EmailStr` 会拒绝 `.local` 域名。

**解决**：
- 后端：改用 URL hash 传递 token：`/login#access_token=xxx&refresh_token=xxx`
- 前端：在 `LoginView.vue` 的 `onMounted` 中解析 hash，提取 token 后 bootstrap
- 后端：将 `schemas/user.py` 中的 `email: EmailStr` 改为 `email: str`
- 后端：将占位符邮箱从 `@wecom.local` 改为 `@wecom.your-domain.com`

### 4. 管理后台用户列表空白

**原因**：同上的 email 校验问题，`/admin/users` 返回的用户列表中包含 `EmailStr` 校验失败的记录时，整个请求 500。

**解决**：修复 email 校验后，更新已有用户记录：

```sql
UPDATE users SET email = REPLACE(email, '@wecom.local', '@wecom.your-domain.com')
WHERE email LIKE '%@wecom.local';
```

### 5. 提示"Blocked request. This host is not allowed."

**原因**：Vite preview server 默认不允许自定义域名访问。

**解决**：在 `vite.config.ts` 中添加 `preview.allowedHosts`。

### 6. HTTPS 证书问题

**企业微信要求**：
- 必须 HTTPS
- 必须使用可信 CA 签发的证书（自签名证书不行）
- 不支持 IP 地址回调

**解决**：使用正式域名 + 可信 CA 证书（如 DigiCert、Let's Encrypt、公司通配符证书）。

---

## 八、后端主要代码变更清单

### 文件 1：`backend/app/auth_providers/wecom.py`

- 兼容 `UserId` 大写字段
- 邮箱占位符改用 `@wecom.your-domain.com`

### 文件 2：`backend/app/api/v1/auth.py`

- 错误回调：跳转到 `/login#error=...`
- 成功回调（无 opener）：跳转到 `/login#access_token=...&refresh_token=...`

### 文件 3：`backend/app/schemas/user.py`

- `email: EmailStr` → `email: str`

### 文件 4：`frontend/src/views/LoginView.vue`

- `onMounted` 中添加 hash 解析和 bootstrap 逻辑

### 文件 5：`frontend/vite.config.ts`

- 添加 `preview.allowedHosts`

---

## 九、开发者注意事项

1. **不要直接推 main/master**：所有代码变更必须通过 PR 走开发分支，等待手动 Review 后合并。
2. **科学上网**：企业微信公网 IP 可能与服务器实际出口 IP 不一致，用 `curl -4 https://httpbin.org/ip` 确认。
3. **证书管理**：不要在代码库中提交 `.key` 文件。证书应放在服务器安全目录，并加入 `.gitignore`。
4. **密钥保护**：WeCom Secret 使用前端缺省机制保护，管理后台保存时如果前端发空字符串，后端保留原值不覆盖。
5. **日志排查**：临时问题可通过 `journalctl` 或后端日志查看最新错误。
