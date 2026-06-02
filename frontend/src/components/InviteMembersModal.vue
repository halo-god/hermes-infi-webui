<script setup lang="ts">
/* 1:1 port of the prototype InviteMembersModal. The 邮箱 tab adds existing
   users to the team via the real API; 链接 / SSO tabs reproduce the prototype. */
import { computed, reactive, ref, watch } from "vue";
import Icon from "@/components/Icon.vue";
import ModalShell from "@/components/ModalShell.vue";
import { teamsApi } from "@/api/teams";
import type { TeamDetail } from "@/types";

const props = defineProps<{ team: { id: string; name: string; handle: string | null } }>();
const emit = defineEmits<{ close: []; invited: [TeamDetail] }>();

const tab = ref<"link" | "email" | "sso">("link");
const role = ref("member");
const expires = ref("7d");
const baseUrl = window.location.origin;
const link = ref("");
const copied = ref(false);
const tokenLoading = ref(false);

const EXPIRES_DAYS: Record<string, number> = { "1d": 1, "7d": 7, "30d": 30, "never": 0 };

async function fetchToken() {
  tokenLoading.value = true;
  try {
    const res = await teamsApi.generateInviteToken(props.team.id, role.value, EXPIRES_DAYS[expires.value] ?? 7);
    link.value = `${baseUrl}${res.url}`;
  } catch {
    link.value = `${baseUrl}/i/${props.team.handle || props.team.id}/error`;
  } finally {
    tokenLoading.value = false;
  }
}

// Fetch token when tab becomes active or role/expires changes
watch([tab, role, expires], ([newTab]) => {
  if (newTab === "link") fetchToken();
}, { immediate: true });

function copyLink() {
  navigator.clipboard?.writeText(link.value);
  copied.value = true;
  setTimeout(() => (copied.value = false), 1600);
}
async function regen() {
  await fetchToken();
}

const emailInput = ref("");
const parsedEmails = computed(() => (emailInput.value || "").split(/[,\s;\n]+/).map((s) => s.trim()).filter((s) => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(s)));
const result = ref("");
const sending = ref(false);
async function sendInvites() {
  if (!parsedEmails.value.length || sending.value) return;
  sending.value = true;
  let ok = 0;
  const fail: string[] = [];
  for (const em of parsedEmails.value) {
    try {
      await teamsApi.addMember(props.team.id, em, role.value);
      ok++;
    } catch {
      fail.push(em);
    }
  }
  result.value = `已加入 ${ok} 人${fail.length ? `；${fail.length} 个邮箱未注册（需先有账号）` : ""}`;
  emailInput.value = "";
  sending.value = false;
  const detail = await teamsApi.get(props.team.id);
  emit("invited", detail);
}

const sso = reactive({
  wecom: { on: true, syncedToday: null as number | null, deptAuto: "设计部 → 设计组" },
  ldap: { on: true, syncedToday: null as number | null, filter: "ou=hermes,dc=hermes,dc=io" },
  feishu: { on: false },
});
const ROLES = [
  { id: "team_admin", name: "团队管理员", desc: "管理项目、成员、助手" },
  { id: "member", name: "成员", desc: "参与对话、上传知识" },
  { id: "viewer", name: "只读", desc: "仅查看分享内容" },
];
const EXPIRES = [{ id: "1d", label: "1 天" }, { id: "7d", label: "7 天" }, { id: "30d", label: "30 天" }, { id: "never", label: "永不" }];
</script>

<template>
  <ModalShell title="邀请成员" :subtitle="'加入「' + team.name + '」团队'" :width="640" @close="$emit('close')">
    <div class="np-field">
      <label class="np-label">邀请角色 <span class="np-hint">被邀请者加入后的初始角色</span></label>
      <div class="inv-roles">
        <button v-for="r in ROLES" :key="r.id" class="inv-role" :class="{ on: role === r.id }" @click="role = r.id">
          <div class="inv-role-nm">{{ r.name }}</div>
          <div class="inv-role-ds">{{ r.desc }}</div>
        </button>
      </div>
    </div>

    <div class="inv-tabs">
      <button :class="{ on: tab === 'link' }" @click="tab = 'link'"><Icon name="share" /> 邀请链接</button>
      <button :class="{ on: tab === 'email' }" @click="tab = 'email'"><Icon name="paperclip" /> 邮箱邀请</button>
      <button :class="{ on: tab === 'sso' }" @click="tab = 'sso'"><Icon name="bolt" /> SSO 自动同步</button>
    </div>

    <div v-if="tab === 'link'" class="inv-pane">
      <div class="inv-link-box">
        <Icon name="share" />
        <input class="np-input inv-link-input" :value="tokenLoading ? '生成中…' : link" readonly @click="($event.target as HTMLInputElement).select()" />
        <button class="btn" @click="regen" :disabled="tokenLoading" title="重新生成"><Icon name="refresh" /></button>
        <button class="btn primary" :disabled="tokenLoading || !link" @click="copyLink"><Icon :name="copied ? 'check' : 'copy'" /> {{ copied ? "已复制" : "复制" }}</button>
      </div>
      <div class="np-row" style="margin-top: 14px">
        <div class="np-field">
          <label class="np-label">链接有效期</label>
          <div class="np-seg"><button v-for="e in EXPIRES" :key="e.id" :class="{ active: expires === e.id }" @click="expires = e.id">{{ e.label }}</button></div>
        </div>
        <div class="np-field">
          <label class="np-label">使用限制</label>
          <div class="inv-meta"><span><Icon name="user" /> 不限人数</span><span><Icon name="check" /> 需邮箱验证</span></div>
        </div>
      </div>
    </div>

    <div v-else-if="tab === 'email'" class="inv-pane">
      <textarea class="np-input np-textarea inv-email-input" v-model="emailInput" rows="4" placeholder="一行一个邮箱，或用逗号/空格分隔——例如：&#10;zhiwei@hermes.io, xu.sh@hermes.io"></textarea>
      <div class="inv-email-foot">
        <span v-if="parsedEmails.length" class="inv-parsed"><Icon name="check" /> 识别到 {{ parsedEmails.length }} 个有效邮箱</span>
        <span v-else class="inv-parsed empty">输入邮箱后会自动识别</span>
        <span style="flex: 1"></span>
        <button class="btn primary" :disabled="!parsedEmails.length || sending" @click="sendInvites"><Icon name="arrow_up" /> {{ sending ? "发送中…" : "发送邀请" }}</button>
      </div>
      <div v-if="result" style="font-size: 12.5px; color: var(--ink-soft); margin-top: 8px">{{ result }}</div>
    </div>

    <div v-else class="inv-pane">
      <div class="inv-sso-row">
        <div class="inv-sso-icon" style="background: #3a7a2a; color: white">W</div>
        <div style="flex: 1; min-width: 0">
          <div class="inv-sso-nm">企业微信 SSO <span v-if="sso.wecom.on" class="inv-sso-on">同步中</span><span v-else class="inv-sso-off">已停用</span></div>
          <div class="inv-sso-ds">今日已自动加入 {{ sso.wecom.syncedToday ?? "—" }} 人 · 部门映射：{{ sso.wecom.deptAuto }}</div>
        </div>
        <div class="cfg-toggle" :class="{ on: sso.wecom.on }" @click="sso.wecom.on = !sso.wecom.on"></div>
      </div>
      <div class="inv-sso-row">
        <div class="inv-sso-icon" style="background: #3a6da1; color: white">L</div>
        <div style="flex: 1; min-width: 0">
          <div class="inv-sso-nm">LDAP / AD <span v-if="sso.ldap.on" class="inv-sso-on">同步中</span><span v-else class="inv-sso-off">已停用</span></div>
          <div class="inv-sso-ds" style="font-family: var(--font-mono); font-size: 11px">today: +{{ sso.ldap.syncedToday ?? "—" }} · filter: {{ sso.ldap.filter }}</div>
        </div>
        <div class="cfg-toggle" :class="{ on: sso.ldap.on }" @click="sso.ldap.on = !sso.ldap.on"></div>
      </div>
      <div class="inv-sso-row">
        <div class="inv-sso-icon" style="background: #6a3aa1; color: white">F</div>
        <div style="flex: 1; min-width: 0">
          <div class="inv-sso-nm">飞书 SSO <span class="inv-sso-off">未配置</span></div>
          <div class="inv-sso-ds">在「后台 · 身份连接器」中完成配置后启用</div>
        </div>
        <div class="cfg-toggle" :class="{ on: sso.feishu.on }" @click="sso.feishu.on = !sso.feishu.on"></div>
      </div>
    </div>

    <template #foot>
      <span class="np-foot-hint">邮箱邀请会把已注册用户直接加入团队</span>
      <span style="flex: 1"></span>
      <button class="btn" @click="$emit('close')">关闭</button>
    </template>
  </ModalShell>
</template>
