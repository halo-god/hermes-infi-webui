<script setup lang="ts">
/* Personal settings — uses the prototype's user-page styles (.up-*). 个人资料
   editing is wired to PATCH /users/me; other tabs reproduce the structure. */
import { computed, reactive, ref, watch } from "vue";
import Icon from "@/components/Icon.vue";
import { http } from "@/api/client";
import { authApi } from "@/api/auth";
import { useAuthStore } from "@/stores/auth";
import { useNotificationStore } from "@/stores/notifications";

const auth = useAuthStore();
const ns = useNotificationStore();
const tab = ref<"profile" | "prefs" | "security" | "notify">("profile");
const COLORS = ["#b8852a", "#3a6da1", "#8a5aa1", "#5b8a4a", "#c45a3a", "#3a8a7a", "#6a3aa1", "#1d1a14"];

// ── profile form ──
const form = reactive({
  name: auth.user?.name || "",
  handle: auth.user?.handle || "",
  title: auth.user?.title || "",
  department: auth.user?.department || "",
  bio: "",
  color: auth.user?.color || "#b8852a",
});
const initial = JSON.stringify({ ...form });
const dirty = computed(() => JSON.stringify({ ...form }) !== initial);
const saving = ref(false);

watch(
  () => auth.user,
  (u) => {
    if (u) {
      form.name = u.name;
      form.handle = u.handle || "";
      form.title = u.title || "";
      form.department = u.department || "";
      form.color = u.color || "#b8852a";
    }
  },
);

async function save() {
  saving.value = true;
  try {
    await http.patch("/users/me", { ...form });
    auth.user = await authApi.me();
    ns.toast("个人资料已保存");
  } catch {
    ns.toast("保存失败，请重试", "error");
  } finally {
    saving.value = false;
  }
}

// ── security ──
const twoFaEnabled = ref(false);
const sessions = ref([
  { id: "current", device: navigator.userAgent.includes("Mobile") ? "移动设备" : "当前浏览器", ip: "—", ts: new Date().toISOString(), current: true },
]);
async function revokeSession(id: string) {
  sessions.value = sessions.value.filter((s) => s.id !== id);
  try {
    await http.delete(`/users/me/sessions/${id}`);
  } catch { /* endpoint may not exist yet */ }
  ns.toast("会话已撤销");
}
function relTime(ts: string) {
  const diff = (Date.now() - new Date(ts).getTime()) / 1000;
  if (diff < 60) return "刚刚";
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
  return `${Math.floor(diff / 86400)} 天前`;
}

// ── notifications preferences ──
const notifyPrefs = reactive({
  mention: true,
  team_invite: true,
  project_update: true,
  agent_done: true,
  agent_error: true,
  system: false,
  email_digest: false,
  email_mention: true,
});
const notifyDirty = ref(false);
const notifySaving = ref(false);
watch(notifyPrefs, () => { notifyDirty.value = true; }, { deep: true });
async function saveNotifyPrefs() {
  notifySaving.value = true;
  try {
    await http.patch("/users/me", { notify_prefs: { ...notifyPrefs } });
    notifyDirty.value = false;
    ns.toast("通知偏好已保存");
  } catch {
    ns.toast("保存失败", "error");
  } finally {
    notifySaving.value = false;
  }
}
</script>

<template>
  <div class="stage">
    <div class="team-hero">
      <div class="team-hero-row">
        <div class="up-avatar" :style="{ background: form.color }">{{ auth.user?.initials || form.name.slice(0, 1) }}</div>
        <div class="team-info">
          <div class="team-crumb">个人设置 · PROFILE</div>
          <h1 class="team-name">{{ form.name }}<span class="handle">@{{ form.handle || "me" }}</span></h1>
          <div class="team-tagline">{{ form.title || "—" }}</div>
          <div class="team-meta-row">
            <span><Icon name="user" /> {{ auth.user?.email }}</span>
            <span class="role-pill">{{ auth.user?.role }}</span>
            <span><Icon name="globe" /> {{ auth.user?.source }}</span>
          </div>
        </div>
      </div>
      <div class="team-tabs">
        <button class="team-tab" :class="{ active: tab === 'profile' }" @click="tab = 'profile'">个人资料</button>
        <button class="team-tab" :class="{ active: tab === 'prefs' }" @click="tab = 'prefs'">偏好设置</button>
        <button class="team-tab" :class="{ active: tab === 'security' }" @click="tab = 'security'">安全</button>
        <button class="team-tab" :class="{ active: tab === 'notify' }" @click="tab = 'notify'">通知</button>
      </div>
    </div>

    <div class="team-body" style="max-width: 760px">

      <!-- ── 个人资料 ── -->
      <template v-if="tab === 'profile'">
        <div class="section-card">
          <div class="section-head"><div class="section-title">基本资料</div></div>
          <div style="padding: 18px; display: grid; grid-template-columns: 140px 1fr; gap: 14px 18px; align-items: center; font-size: 13px">
            <div class="text-mute">姓名</div>
            <input class="cfg-input" v-model="form.name" />
            <div class="text-mute">用户名</div>
            <input class="cfg-input" v-model="form.handle" />
            <div class="text-mute">职位</div>
            <input class="cfg-input" v-model="form.title" />
            <div class="text-mute">部门</div>
            <input class="cfg-input" v-model="form.department" />
            <div class="text-mute">简介</div>
            <textarea class="cfg-input" style="height: 64px; padding-top: 8px" v-model="form.bio"></textarea>
            <div class="text-mute">头像颜色</div>
            <div class="up-swatches">
              <button v-for="c in COLORS" :key="c" class="up-sw" :class="{ active: form.color === c }" :style="{ background: c }" @click="form.color = c"></button>
            </div>
          </div>
        </div>
      </template>

      <!-- ── 偏好设置 ── -->
      <template v-else-if="tab === 'prefs'">
        <div class="section-card">
          <div class="section-head"><div class="section-title">界面偏好</div></div>
          <div style="padding: 18px; font-size: 13px; color: var(--ink-mute)">
            主题、密度和语气风格可在右上角 <strong>Tweaks</strong> 面板中实时调整，更改会自动保存到本地。
          </div>
        </div>
        <div class="section-card" style="margin-top: 16px">
          <div class="section-head"><div class="section-title">语言与时区</div></div>
          <div style="padding: 18px; display: grid; grid-template-columns: 140px 1fr; gap: 14px 18px; align-items: center; font-size: 13px">
            <div class="text-mute">界面语言</div>
            <select class="cfg-input" style="height: 34px">
              <option value="zh">中文（简体）</option>
              <option value="en">English</option>
            </select>
            <div class="text-mute">时区</div>
            <input class="cfg-input text-mute" value="Asia/Shanghai (UTC+8)" readonly />
          </div>
        </div>
      </template>

      <!-- ── 安全 ── -->
      <template v-else-if="tab === 'security'">
        <div class="section-card">
          <div class="section-head"><div class="section-title">两步验证 (2FA)</div></div>
          <div style="padding: 18px; display: flex; align-items: center; justify-content: space-between; gap: 12px">
            <div>
              <div style="font-size: 13.5px; font-weight: 500; color: var(--ink)">{{ twoFaEnabled ? "已启用" : "未启用" }}</div>
              <div style="font-size: 12px; color: var(--ink-mute); margin-top: 3px">通过 TOTP 应用（Google Authenticator / Authy）保护账号。</div>
            </div>
            <button class="btn" :class="{ primary: !twoFaEnabled }" @click="twoFaEnabled = !twoFaEnabled">
              {{ twoFaEnabled ? "关闭 2FA" : "启用 2FA" }}
            </button>
          </div>
        </div>

        <div class="section-card" style="margin-top: 16px">
          <div class="section-head">
            <div class="section-title">登录会话</div>
            <span style="font-size: 11.5px; color: var(--ink-mute)">{{ sessions.length }} 个活跃设备</span>
          </div>
          <div class="section-body flush">
            <div v-for="s in sessions" :key="s.id" class="row-item" style="padding: 12px 16px">
              <Icon name="globe" style="color: var(--ink-mute); flex-shrink: 0" />
              <div class="flex-1-min">
                <div style="font-size: 13px; font-weight: 500; color: var(--ink)">
                  {{ s.device }}
                  <span v-if="s.current" style="margin-left: 6px; font-size: 10.5px; background: var(--accent-tint); color: var(--accent-deep); border-radius: 4px; padding: 1px 5px; font-weight: 600">当前</span>
                </div>
                <div style="font-size: 11.5px; color: var(--ink-mute); margin-top: 2px">{{ s.ip }} · {{ relTime(s.ts) }}</div>
              </div>
              <button v-if="!s.current" class="btn text-danger" @click="revokeSession(s.id)">撤销</button>
            </div>
          </div>
        </div>

        <div class="section-card" style="margin-top: 16px; border-color: color-mix(in srgb, var(--danger) 25%, var(--rule))">
          <div class="section-head"><div class="section-title text-danger">危险操作</div></div>
          <div style="padding: 18px; display: flex; align-items: center; justify-content: space-between; gap: 12px">
            <div>
              <div style="font-size: 13px; font-weight: 500; color: var(--ink)">修改密码</div>
              <div class="text-mute-sm" style="margin-top:2px">需要验证当前密码。</div>
            </div>
            <button class="btn">修改密码</button>
          </div>
        </div>
      </template>

      <!-- ── 通知 ── -->
      <template v-else-if="tab === 'notify'">
        <div class="section-card">
          <div class="section-head"><div class="section-title"><Icon name="bolt" /> 站内通知</div></div>
          <div style="padding: 6px 0">
            <div v-for="(label, key) in { mention: '被@提及', team_invite: '团队邀请', project_update: '项目动态', agent_done: 'Agent 任务完成', agent_error: 'Agent 错误报警', system: '系统公告' }"
              :key="key" class="up-toggle-row">
              <div>
                <div class="up-toggle-nm">{{ label }}</div>
              </div>
              <label class="cfg-toggle-wrap">
                <input type="checkbox" v-model="(notifyPrefs as any)[key]" style="display:none" />
                <span class="cfg-toggle" :class="{ on: (notifyPrefs as any)[key] }" @click="(notifyPrefs as any)[key] = !(notifyPrefs as any)[key]"></span>
              </label>
            </div>
          </div>
        </div>

        <div class="section-card" style="margin-top: 16px">
          <div class="section-head"><div class="section-title"><Icon name="globe" /> 邮件通知</div></div>
          <div style="padding: 6px 0">
            <div class="up-toggle-row">
              <div><div class="up-toggle-nm">被@提及时发邮件</div><div class="up-toggle-ds">仅当你不在线时</div></div>
              <label class="cfg-toggle-wrap">
                <input type="checkbox" v-model="notifyPrefs.email_mention" style="display:none" />
                <span class="cfg-toggle" :class="{ on: notifyPrefs.email_mention }" @click="notifyPrefs.email_mention = !notifyPrefs.email_mention"></span>
              </label>
            </div>
            <div class="up-toggle-row">
              <div><div class="up-toggle-nm">每日摘要邮件</div><div class="up-toggle-ds">汇总前一天的活动，每天早 9 点发送</div></div>
              <label class="cfg-toggle-wrap">
                <input type="checkbox" v-model="notifyPrefs.email_digest" style="display:none" />
                <span class="cfg-toggle" :class="{ on: notifyPrefs.email_digest }" @click="notifyPrefs.email_digest = !notifyPrefs.email_digest"></span>
              </label>
            </div>
          </div>
        </div>

        <div v-if="notifyDirty" style="margin-top: 16px; display: flex; align-items: center; gap: 12px">
          <button class="btn primary" :disabled="notifySaving" @click="saveNotifyPrefs">{{ notifySaving ? "保存中…" : "保存偏好" }}</button>
          <span class="text-mute-sm">有未保存的更改</span>
        </div>
      </template>

    </div>

    <Transition name="savebar">
      <div v-if="dirty && tab === 'profile'" class="up-savebar">
        <Icon name="bolt" :size="14" /> 有未保存的更改
        <button class="btn" @click="save" :disabled="saving" style="color: var(--bg-canvas); border-color: rgba(255,255,255,0.3)">{{ saving ? "保存中…" : "保存" }}</button>
      </div>
    </Transition>
  </div>
</template>

<style scoped>
.savebar-enter-active,
.savebar-leave-active {
  transition: opacity 180ms, transform 180ms;
}
.savebar-enter-from,
.savebar-leave-to {
  opacity: 0;
  transform: translateX(-50%) translateY(12px);
}
.up-toggle-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 18px;
  border-bottom: 1px solid var(--rule);
  gap: 12px;
}
.up-toggle-row:last-child { border-bottom: none }
.up-toggle-nm { font-size: 13px; font-weight: 500; color: var(--ink) }
.up-toggle-ds { font-size: 11.5px; color: var(--ink-mute); margin-top: 2px }
.cfg-toggle-wrap { flex-shrink: 0; cursor: pointer }
.cfg-toggle {
  display: inline-block;
  width: 36px; height: 20px;
  background: var(--rule);
  border-radius: 999px;
  position: relative;
  transition: background 200ms;
}
.cfg-toggle::after {
  content: "";
  position: absolute;
  top: 3px; left: 3px;
  width: 14px; height: 14px;
  background: #fff;
  border-radius: 50%;
  transition: transform 200ms;
  box-shadow: 0 1px 3px rgba(0,0,0,0.2);
}
.cfg-toggle.on { background: var(--accent) }
.cfg-toggle.on::after { transform: translateX(16px) }
</style>
