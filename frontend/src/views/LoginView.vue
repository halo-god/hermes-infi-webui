<script setup lang="ts">
import { onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { AxiosError } from "axios";
import Icon from "@/components/Icon.vue";
import { useAuthStore } from "@/stores/auth";
import { authApi } from "@/api/auth";
import { tokenStore } from "@/api/client";
import type { ProviderInfo } from "@/types";

const auth = useAuthStore();
const router = useRouter();
const route = useRoute();

/** Only allow same-site relative redirects — blocks `?redirect=https://evil.com`
 *  open-redirect / `//evil.com` protocol-relative attacks. */
function safeRedirect(): string {
  const raw = route.query.redirect;
  const target = typeof raw === "string" ? raw : "";
  return /^\/(?!\/)/.test(target) ? target : "/";
}

const providers = ref<ProviderInfo[]>([]);
const activeTab = ref<string>("local");
const showPassword = ref(false);
const busy = ref(false);
const error = ref("");

const form = reactive({
  username: "",
  password: "",
  remember: true,
});

// WeCom OAuth popup flow
const wecomLoading = ref(false);
const wecomError = ref("");
let wecomPopup: Window | null = null;
let wecomListener: ((e: MessageEvent) => void) | null = null;

async function startWecomOAuth() {
  wecomError.value = "";
  wecomLoading.value = true;
  try {
    const { authorize_url } = await authApi.wecomAuthorize();
    // Open popup centered
    const w = 480, h = 640;
    const left = (window.innerWidth - w) / 2 + window.screenX;
    const top = (window.innerHeight - h) / 2 + window.screenY;
    wecomPopup = window.open(
      authorize_url,
      "wecom_oauth",
      `width=${w},height=${h},left=${left},top=${top},scrollbars=yes,resizable=yes`,
    );
    // Listen for callback message
    wecomListener = (e: MessageEvent) => {
      if (e.data?.type === "wecom-callback" && e.data.access_token) {
        cleanupWecom();
        // Store tokens and load user session
        tokenStore.set(e.data.access_token, e.data.refresh_token);
        auth.bootstrap().then(() => {
          router.replace(safeRedirect());
        });
      } else if (e.data?.type === "wecom-error") {
        cleanupWecom();
        wecomError.value = e.data.error || "企业微信登录失败";
      }
    };
    window.addEventListener("message", wecomListener);
    // Poll for popup close (user cancelled)
    const poll = setInterval(() => {
      if (wecomPopup?.closed) {
        clearInterval(poll);
        cleanupWecom();
        wecomLoading.value = false;
      }
    }, 500);
  } catch (e) {
    wecomLoading.value = false;
    const ax = e as AxiosError<{ detail?: string }>;
    wecomError.value = ax.response?.data?.detail || "企业微信登录未启用";
  }
}

function cleanupWecom() {
  wecomLoading.value = false;
  if (wecomListener) {
    window.removeEventListener("message", wecomListener);
    wecomListener = null;
  }
  try { wecomPopup?.close(); } catch { /* */ }
  wecomPopup = null;
}

watch(activeTab, (t) => {
  error.value = "";
  wecomError.value = "";
  if (t !== "wecom") cleanupWecom();
});
onBeforeUnmount(() => cleanupWecom());

onMounted(async () => {
  try {
    providers.value = await authApi.providers();
  } catch {
    providers.value = [{ id: "local", label: "账号密码", enabled: true, kind: "local" }];
  }

  // Handle WeCom workbench callback (tokens passed in URL hash)
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
      // Clean hash from URL
      history.replaceState(null, "", window.location.pathname + window.location.search);
      try {
        await auth.bootstrap();
        if (auth.isAuthenticated) {
          router.replace(safeRedirect());
        }
      } catch {
        wecomError.value = "登录状态恢复失败，请重试";
      }
    }
  }
});

async function submit() {
  if (busy.value) return;
  error.value = "";
  busy.value = true;
  try {
    await auth.login({
      method: activeTab.value === "ldap" ? "ldap" : "local",
      username: form.username,
      password: form.password,
      remember_device: form.remember,
    });
    router.replace(safeRedirect());
  } catch (e) {
    const ax = e as AxiosError<{ detail?: string }>;
    error.value = ax.response?.data?.detail || "登录失败，请重试";
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <div class="login-screen">
    <!-- Brand panel -->
    <div class="login-brand">
      <div class="login-brand-top">
        <div class="login-mark">H</div>
        <div>
          <div class="login-wordmark">Hermes</div>
          <div class="login-wordtag">信使 · MESSENGER</div>
        </div>
      </div>
      <div class="login-brand-mid">
        <div class="login-quote">凡所欲遣，<br />皆可托<em>信使</em>。</div>
        <div class="login-quote-sub">连接你的 Hermes Agent，开始协作。</div>
      </div>
      <div class="login-brand-foot">
        <div class="login-foot-line"><span class="lf-dot" /> ACP 连接就绪</div>
        <div class="login-foot-meta">production · ACP v1</div>
      </div>
      <div class="login-seal-bg">信</div>
    </div>

    <!-- Auth card -->
    <div class="login-panel">
      <div class="login-card">
        <div class="login-card-head">
          <div class="login-card-title">欢迎回来</div>
          <div class="login-card-sub">登录以进入你的工作区</div>
        </div>

        <div class="login-tabs">
          <button
            v-for="p in providers.filter((x) => ['local', 'ldap', 'wecom'].includes(x.id))"
            :key="p.id"
            class="login-tab"
            :class="{ active: activeTab === p.id }"
            @click="activeTab = p.id"
          >
            {{ p.label }}
          </button>
        </div>

        <!-- Local / LDAP (username + password) -->
        <form v-if="activeTab === 'local' || activeTab === 'ldap'" class="login-pane" @submit.prevent="submit">
          <div v-if="activeTab === 'ldap'" class="login-note">
            使用你的<b>域账号</b>登录；首次登录将按部门自动分配团队与角色。
          </div>
          <div class="login-field">
            <label class="login-label">{{ activeTab === 'ldap' ? '域账号' : '账号' }}</label>
            <div class="login-input-wrap">
              <input
                v-model="form.username"
                class="login-input"
                type="text"
                :placeholder="activeTab === 'ldap' ? '域账号 (如 zhiwei)' : '邮箱或用户名'"
                autocomplete="username"
              />
            </div>
          </div>
          <div class="login-field">
            <label class="login-label">密码</label>
            <div class="login-input-wrap">
              <input
                v-model="form.password"
                class="login-input"
                :type="showPassword ? 'text' : 'password'"
                placeholder="••••••••"
                autocomplete="current-password"
              />
              <button type="button" class="li-eye" @click="showPassword = !showPassword">
                {{ showPassword ? "隐藏" : "显示" }}
              </button>
            </div>
          </div>

          <label class="login-remember">
            <span class="login-check" :class="{ on: form.remember }" @click="form.remember = !form.remember" />
            记住此设备
          </label>

          <div v-if="error" class="login-error">⚠ {{ error }}</div>

          <button type="submit" class="login-submit" :class="{ busy }" :disabled="busy">
            {{ busy ? "登录中…" : "登 录" }}
          </button>

          <!-- Demo credentials removed for production -->
        </form>

        <!-- WeCom OAuth -->
        <div v-else-if="activeTab === 'wecom'" class="login-pane login-pane-qr">
          <div class="login-wecom-box">
            <div class="login-wecom-icon">
              <svg width="48" height="48" viewBox="0 0 48 48" fill="none">
                <rect width="48" height="48" rx="12" fill="#3a7a2a"/>
                <path d="M16 20c0-4.4 3.6-8 8-8s8 3.6 8 8-3.6 8-8 8c-1.2 0-2.3-.3-3.3-.7L16 29v-3.3C16 24 16 20 16 20z" fill="#fff" opacity="0.9"/>
                <circle cx="21" cy="19.5" r="1.5" fill="#3a7a2a"/>
                <circle cx="27" cy="19.5" r="1.5" fill="#3a7a2a"/>
              </svg>
            </div>
            <button
              class="login-submit"
              :class="{ busy: wecomLoading }"
              :disabled="wecomLoading"
              @click="startWecomOAuth"
            >
              <span v-if="wecomLoading" class="login-spinner"></span>
              {{ wecomLoading ? "等待扫码确认…" : "企业微信扫码登录" }}
            </button>
            <div class="login-wecom-tip">点击后将弹出企业微信扫码窗口，扫码授权即可登录。</div>
          </div>
          <div v-if="wecomError" class="login-error" style="justify-content: center">⚠ {{ wecomError }}</div>
          <div class="login-note"><Icon name="check" :size="12" /> 首次扫码登录将按部门→团队映射自动开通账号。</div>
        </div>

        <!-- Other reserved providers -->
        <div v-else class="login-pane">
          <div class="login-note" style="background: var(--rule-soft)">
            {{ providers.find((p) => p.id === activeTab)?.label || activeTab }} 登录需管理员在后台「身份与连接」中配置启用。
          </div>
          <button class="login-submit" @click="activeTab = 'local'">返回账号密码登录</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.login-screen {
  height: 100vh;
  width: 100%;
  display: grid;
  grid-template-columns: 1.05fr 1fr;
  background: var(--bg-canvas);
  overflow: hidden;
}
.login-brand {
  position: relative;
  background: linear-gradient(155deg, #211b12 0%, #15110b 55%, #0f0c07 100%);
  color: #efe6cd;
  padding: 48px 52px;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.login-brand-top {
  display: flex;
  align-items: center;
  gap: 14px;
  position: relative;
  z-index: 2;
}
.login-mark {
  width: 52px;
  height: 52px;
  border-radius: 13px;
  background: linear-gradient(180deg, #2a241a, #15110b);
  display: grid;
  place-items: center;
  color: var(--accent);
  font-family: var(--font-serif);
  font-size: 26px;
  font-weight: 600;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.08), 0 2px 6px rgba(0, 0, 0, 0.3);
}
.login-wordmark {
  font-family: var(--font-serif);
  font-size: 26px;
  font-weight: 600;
  line-height: 1;
  color: #f3ecda;
}
.login-wordtag {
  font-size: 9.5px;
  letter-spacing: 0.24em;
  color: #9a8e6e;
  margin-top: 4px;
}
.login-brand-mid {
  margin-top: auto;
  margin-bottom: auto;
  position: relative;
  z-index: 2;
}
.login-quote {
  font-family: var(--font-serif);
  font-size: 46px;
  font-weight: 500;
  line-height: 1.18;
  letter-spacing: -0.01em;
}
.login-quote em {
  font-style: italic;
  color: var(--accent);
}
.login-quote-sub {
  font-family: var(--font-serif);
  font-style: italic;
  font-size: 16px;
  color: #8f835f;
  margin-top: 16px;
}
.login-brand-foot {
  position: relative;
  z-index: 2;
  font-size: 12px;
  color: #9a8e6e;
}
.login-foot-line {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 500;
  color: #c2b896;
}
.login-foot-line .lf-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #6fae5a;
  box-shadow: 0 0 8px rgba(111, 174, 90, 0.6);
}
.login-foot-meta {
  margin-top: 6px;
  font-family: var(--font-mono);
  font-size: 11px;
  color: #7e7457;
}
.login-seal-bg {
  position: absolute;
  right: -40px;
  bottom: -60px;
  font-family: var(--font-serif);
  font-size: 380px;
  font-weight: 600;
  color: rgba(184, 133, 42, 0.05);
  line-height: 1;
  pointer-events: none;
  z-index: 1;
  user-select: none;
}
.login-panel {
  display: grid;
  place-items: center;
  padding: 40px;
  overflow-y: auto;
}
.login-card {
  width: 100%;
  max-width: 380px;
}
.login-card-head {
  margin-bottom: 22px;
}
.login-card-title {
  font-family: var(--font-serif);
  font-size: 27px;
  font-weight: 600;
  color: var(--ink);
  letter-spacing: -0.01em;
}
.login-card-sub {
  font-size: 13px;
  color: var(--ink-mute);
  margin-top: 4px;
}
.login-tabs {
  display: flex;
  gap: 2px;
  padding: 3px;
  background: rgba(29, 26, 20, 0.05);
  border-radius: 999px;
  margin-bottom: 22px;
}
.login-tab {
  flex: 1;
  padding: 8px 6px;
  border-radius: 999px;
  font-size: 12.5px;
  font-weight: 500;
  color: var(--ink-mute);
  transition: background 150ms, color 150ms;
  white-space: nowrap;
}
.login-tab:hover {
  color: var(--ink);
}
.login-tab.active {
  background: var(--bg-panel);
  color: var(--ink);
  box-shadow: var(--shadow-sm);
}
.login-pane {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.login-field {
  display: flex;
  flex-direction: column;
  gap: 7px;
}
.login-label {
  font-size: 12px;
  font-weight: 500;
  color: var(--ink-soft);
}
.login-input-wrap {
  display: flex;
  align-items: center;
  gap: 9px;
  height: 44px;
  padding: 0 13px;
  border: 1px solid var(--rule);
  border-radius: var(--r-sm);
  background: var(--bg-panel);
  transition: border-color 150ms, box-shadow 150ms;
}
.login-input-wrap:focus-within {
  border-color: var(--accent-soft);
  box-shadow: 0 0 0 3px rgba(184, 133, 42, 0.1);
}
.login-input {
  flex: 1;
  border: none;
  outline: none;
  background: transparent;
  color: var(--ink);
  font-size: 14px;
  min-width: 0;
}
.li-eye {
  color: var(--ink-mute);
  font-size: 12px;
  padding: 4px 6px;
  border-radius: 5px;
  flex-shrink: 0;
}
.li-eye:hover {
  background: rgba(29, 26, 20, 0.05);
  color: var(--ink);
}
.login-remember {
  display: flex;
  align-items: center;
  gap: 9px;
  font-size: 12.5px;
  color: var(--ink-soft);
}
.login-check {
  width: 18px;
  height: 18px;
  border-radius: 5px;
  flex-shrink: 0;
  border: 1.5px solid var(--ink-faint);
  display: grid;
  place-items: center;
  cursor: pointer;
  transition: background 120ms, border-color 120ms;
}
.login-check.on {
  background: var(--accent);
  border-color: var(--accent);
}
.login-error {
  font-size: 12.5px;
  color: var(--danger);
  font-weight: 500;
}
.login-submit {
  height: 46px;
  border-radius: var(--r-sm);
  background: var(--ink);
  color: var(--bg-canvas);
  font-size: 14px;
  font-weight: 600;
  letter-spacing: 0.05em;
  transition: background 140ms, transform 120ms, opacity 140ms;
}
.login-submit:hover {
  background: var(--accent);
  transform: translateY(-1px);
}
.login-submit.busy {
  background: var(--ink-faint);
  cursor: default;
}
.login-note {
  display: flex;
  gap: 7px;
  font-size: 11.5px;
  color: var(--ink-mute);
  line-height: 1.5;
  background: var(--accent-tint);
  border-radius: var(--r-sm);
  padding: 9px 11px;
}
.login-note b {
  color: var(--accent-deep);
}
.login-wecom-box {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 18px;
  padding: 32px 20px 24px;
}
.login-wecom-icon {
  width: 64px;
  height: 64px;
  display: grid;
  place-items: center;
  border-radius: 16px;
  background: rgba(58, 122, 42, 0.08);
}
.login-wecom-tip {
  font-size: 12.5px;
  color: var(--ink-mute);
  text-align: center;
  line-height: 1.5;
}
.login-spinner {
  display: inline-block;
  width: 14px;
  height: 14px;
  border: 2px solid rgba(255,255,255,0.3);
  border-top-color: #fff;
  border-radius: 50%;
  animation: spin 0.6s linear infinite;
  margin-right: 6px;
  vertical-align: middle;
}
@keyframes spin {
  to { transform: rotate(360deg); }
}
@media (max-width: 860px) {
  .login-screen {
    grid-template-columns: 1fr;
  }
  .login-brand {
    display: none;
  }
}
</style>
