<script setup lang="ts">
/* 1:1 port of the prototype team page (project/hermes-team.js), wired to the
   real API. Only the data wiring differs from the prototype; the markup/classes
   and UX structure are reproduced faithfully. */
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import Icon from "@/components/Icon.vue";
import NewProjectModal from "@/components/NewProjectModal.vue";
import InviteMembersModal from "@/components/InviteMembersModal.vue";
import KnowledgeModal from "@/components/KnowledgeModal.vue";
import SharedAgentsModal from "@/components/SharedAgentsModal.vue";
import ConfirmModal from "@/components/ConfirmModal.vue";
import ProjectMembersModal from "@/components/ProjectMembersModal.vue";
import WorkspacePanel from "@/components/WorkspacePanel.vue";
import { teamsApi } from "@/api/teams";
import { agentsApi, type Profile } from "@/api/agents";
import { projectsApi } from "@/api/projects";
import { useChatStore } from "@/stores/chat";
import type { Agent, FileItem, Member, Project, TeamDetail, TeamPolicy, WsAdapter } from "@/types";

const route = useRoute();
const router = useRouter();
const chat = useChatStore();
const teamId = computed(() => route.params.id as string);
const showNewProject = ref(false);
const editingProject = ref<Project | null>(null);
const editingMembersProject = ref<Project | null>(null);
const showInvite = ref(false);
const showKnowledgeModal = ref(false);
const editingKnowledge = ref<{ id: string; name: string; kind: string; size_bytes: number } | null>(null);
const showSharedAgentsModal = ref(false);
const confirmAction = ref<{ title: string; message: string; confirmText: string; danger: boolean; onConfirm: () => Promise<void> } | null>(null);
const showKnowledgePanel = ref(false);
const clickedKnowledgeId = ref<string | null>(null);
const teamProfiles = ref<Profile[]>([]);

function agentInfo(id: string): Agent {
  return chat.agents.find((a) => a.id === id) || ({ id, label: id, color: "#b8852a", icon: "sparkle", description: "" } as Agent);
}

const detail = ref<TeamDetail | null>(null);
const projects = ref<Project[]>([]);
const policy = ref<TeamPolicy | null>(null);
const policyMap = reactive<Record<string, Record<string, boolean>>>({});
const tab = ref<string>("overview");
const menuFor = ref<string | null>(null);
const projMenuFor = ref<string | null>(null);

const ROLE_LABEL: Record<string, string> = { owner: "所有者", admin: "管理员", member: "成员", viewer: "只读" };
const ROLE_KEYS = ["owner", "admin", "member", "viewer"];
const GROUP_ICON: Record<string, string> = { 项目: "cube", 知识库: "doc", "会话与助手": "chat", 成员: "user" };

onMounted(() => {
  document.addEventListener("click", closeMenus);
  load();
});
onBeforeUnmount(() => {
  document.removeEventListener("click", closeMenus);
  stopChannelPoll();
  if (channelEsRef.value) { channelEsRef.value.close(); channelEsRef.value = null; }
});

watch(() => route.params.id, async (newId, oldId) => {
  if (newId && newId !== oldId) {
    tab.value = "overview";
    stopChannelPoll();
    channelConvo.value = null;
    channelMessages.value = [];
    await load();
  }
});
function closeMenus() {
  menuFor.value = null;
  projMenuFor.value = null;
  chCardMsg.value = null;
  showKnowledgePicker.value = false;
}

async function load() {
  const tid = teamId.value;
  const [d, ps, pol, allProfiles] = await Promise.all([
    teamsApi.get(tid),
    projectsApi.listByTeam(tid).catch(() => []),
    teamsApi.policy(tid).catch(() => null),
    agentsApi.profiles().catch(() => []),
  ]);
  detail.value = d;
  projects.value = ps;
  policy.value = pol;
  teamProfiles.value = allProfiles.filter((p) => p.team_id === tid || p.scope === "global");
  if (pol) {
    Object.keys(policyMap).forEach((k) => delete policyMap[k]);
    Object.entries(pol.policy).forEach(([pid, roles]) => (policyMap[pid] = { ...roles }));
  }
  if (!chat.agents.length) chat.loadAgents();
  if (!chat.profiles.length) chat.loadProfiles();
}

const knowledgeFiles = computed<FileItem[]>(() =>
  (detail.value?.knowledge || []).map((k) => ({
    id: k.id,
    name: k.name,
    kind: k.kind,
    size_bytes: k.size_bytes,
  }))
);

const kbAdapter = computed<WsAdapter>(() => ({
  getContent: (fid) => teamsApi.knowledgeContent(teamId.value, fid),
  getRawUrl: (fid) => teamsApi.knowledgeRawUrl(teamId.value, fid),
  patchContent: (fid, content) => teamsApi.updateKnowledgeContent(teamId.value, fid, content),
  upload: async (file) => {
    await teamsApi.uploadKnowledge(teamId.value, file);
    await load();
  },
}));

function openKnowledgeFile(fileId: string) {
  clickedKnowledgeId.value = fileId;
  showKnowledgePanel.value = true;
}

watch(tab, (newTab) => {
  if (newTab !== "knowledge") showKnowledgePanel.value = false;
  if (newTab === "channel") {
    loadChannel();
  } else {
    stopChannelPoll();
  }
});

// ── Group channel ──
import { conversationsApi } from "@/api/conversations";
import { tokenStore } from "@/api/client";
import type { Conversation, Message } from "@/types";
const API_BASE = import.meta.env.VITE_API_BASE || "/api/v1";

const channelConvo = ref<Conversation | null>(null);
const channelMode = ref<string>("mention");
const channelMessages = ref<Message[]>([]);
const channelDraft = ref("");
const channelLoading = ref(false);
const channelSending = ref(false);
const channelScroller = ref<HTMLElement | null>(null);
const channelEsRef = ref<EventSource | null>(null);
const channelPollTimer = ref<ReturnType<typeof setInterval> | null>(null);
const channelAttachments = ref<{ id: string; name: string }[]>([]);
const showKnowledgePicker = ref(false);
const chCardMsg = ref<Message | null>(null);
const chCardPos = ref({ x: 0, y: 0 });

function stopChannelPoll() {
  if (channelPollTimer.value) { clearInterval(channelPollTimer.value); channelPollTimer.value = null; }
}

async function refreshChannelMessages() {
  if (!channelConvo.value) return;
  try {
    const d = await conversationsApi.get(channelConvo.value.id);
    channelMessages.value = (d as any).messages || [];
  } catch { /* ignore */ }
}

function applyChannelEvent(ev: { type: string; delta?: string; text?: string }) {
  if (!channelMessages.value.length) return;
  const last = channelMessages.value[channelMessages.value.length - 1];
  if (last.role !== "agent" && last.role !== "roundtable") return;
  if (ev.type === "token" && ev.delta) {
    last.content = { ...last.content, text: (last.content.text || "") + ev.delta };
  } else if (ev.type === "done" && ev.text !== undefined) {
    last.content = { ...last.content, text: ev.text };
    last.status = "complete";
  }
}

async function loadChannel() {
  if (channelLoading.value) return;
  channelLoading.value = true;
  try {
    const res = await teamsApi.getChannel(teamId.value);
    channelConvo.value = res.channel;
    channelMode.value = res.channel_mode;
    const d = await conversationsApi.get(res.channel.id);
    channelMessages.value = (d as any).messages || [];
    await nextTick();
    if (channelScroller.value) channelScroller.value.scrollTop = channelScroller.value.scrollHeight;
    // Poll every 5s so other members see fresh messages
    stopChannelPoll();
    channelPollTimer.value = setInterval(async () => {
      await refreshChannelMessages();
    }, 5000);
  } catch {
    /* ignore */
  } finally {
    channelLoading.value = false;
  }
}

async function sendChannelMessage() {
  const text = channelDraft.value.trim();
  if (!text || !channelConvo.value || channelSending.value) return;
  channelDraft.value = "";
  channelSending.value = true;

  // Inline-attach any selected knowledge items
  let fullText = text;
  if (channelAttachments.value.length) {
    try {
      const parts: string[] = [];
      for (const att of channelAttachments.value) {
        const content = await teamsApi.knowledgeContent(teamId.value, att.id);
        if (content) parts.push(`【知识库: ${att.name}】\n${content}`);
      }
      if (parts.length) fullText = parts.join("\n\n---\n\n") + "\n\n---\n\n" + text;
    } catch { /* ignore */ }
    channelAttachments.value = [];
  }

  const convoId = channelConvo.value.id;
  const token = tokenStore.access || "";
  const url = `${API_BASE}/conversations/${convoId}/stream?access_token=${encodeURIComponent(token)}`;

  // Open SSE before POSTing so we don't miss early tokens
  if (channelEsRef.value) { channelEsRef.value.close(); }
  const es = new EventSource(url);
  channelEsRef.value = es;
  es.onmessage = (e) => {
    try { applyChannelEvent(JSON.parse(e.data)); } catch { /* heartbeat */ }
  };
  await new Promise<void>((resolve) => {
    es.addEventListener("open", () => resolve());
    setTimeout(resolve, 600);
  });

  try {
    const res = await conversationsApi.send(convoId, fullText);
    channelMessages.value.push(res.user_message, res.agent_message);
    await nextTick();
    if (channelScroller.value) channelScroller.value.scrollTop = channelScroller.value.scrollHeight;
  } finally {
    channelSending.value = false;
    setTimeout(() => { if (channelEsRef.value === es) { es.close(); channelEsRef.value = null; } }, 4000);
  }
}

async function updateChannelMode(mode: string) {
  channelMode.value = mode;
  await teamsApi.setChannelMode(teamId.value, mode);
}

function toggleKnowledgePicker() {
  showKnowledgePicker.value = !showKnowledgePicker.value;
}
function addChannelAttachment(k: { id: string; name: string }) {
  if (!channelAttachments.value.find((a) => a.id === k.id)) {
    channelAttachments.value.push({ id: k.id, name: k.name });
  }
  showKnowledgePicker.value = false;
}
function removeChannelAttachment(id: string) {
  channelAttachments.value = channelAttachments.value.filter((a) => a.id !== id);
}

function channelMemberInfo(ownerId: string | null | undefined): { name: string; initials: string; color: string } | null {
  if (!ownerId) return null;
  const m = members.value.find((x) => x.user_id === ownerId);
  if (!m) return null;
  return { name: m.name || "成员", initials: m.initials || (m.name || "?").slice(0, 1), color: m.color || "#b8852a" };
}

function showChCard(ev: MouseEvent, msg: Message) {
  chCardMsg.value = msg;
  chCardPos.value = { x: (ev.currentTarget as HTMLElement).getBoundingClientRect().left, y: (ev.currentTarget as HTMLElement).getBoundingClientRect().bottom + 6 };
}
function hideChCard() {
  chCardMsg.value = null;
}

// ── computed stat footer values using real data ──
const lastKnowledgeUpdate = computed(() => {
  const items = detail.value?.knowledge;
  if (!items?.length) return "—";
  const latest = items.reduce((a, b) => (a.created_at > b.created_at ? a : b));
  const diff = (Date.now() - new Date(latest.created_at).getTime()) / 1000;
  if (diff < 60) return "刚刚";
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
  if (diff < 86400 * 2) return "昨天";
  return `${Math.floor(diff / 86400)} 天前`;
});

// ── adapted "team" object matching the prototype template shape ──
const myRole = computed(() => policy.value?.my_role || detail.value?.my_role || "member");
const canEditPolicy = computed(() => myRole.value === "owner" || myRole.value === "admin");

function can(perm: string): boolean {
  if (myRole.value === "owner") return true;
  return !!policyMap[perm]?.[myRole.value];
}

const members = computed<Member[]>(() => detail.value?.members || []);

const team = computed(() => {
  const d = detail.value;
  return {
    name: d?.name || "",
    handle: d?.handle || "",
    tagline: d?.tagline || "",
    color: d?.color || "#b8852a",
    icon: "cube",
    plan: d?.plan || "team",
    created: d ? new Date(d.created_at).toLocaleDateString("zh-CN") : "",
    role: myRole.value,
    stats: d?.stats || { members: members.value.length, agents: 1, threads: 0, knowledge: 0 },
    members: members.value.map((m): {
      handle: string; name: string; initials: string; color: string;
      role: string; roleKey: string; status: string; last: string;
    } => ({
      handle: m.email || m.user_id,
      name: m.name || "未命名",
      initials: m.initials || (m.name || "?").slice(0, 1),
      color: m.color || "#b8852a",
      role: ROLE_LABEL[m.role] || m.role,
      roleKey: m.role,
      status: m.status || "offline",
      last: "—",
    })),
    sharedAgents: (d?.shared_agents || ["hermes"]) as string[],
    pinned: (d?.pinned || []) as { id: string; title: string; primary_agent_id: string; updated_at: string }[],
    activity: (d?.activity || []) as { who: string; action: string; target: string; icon: string; ago: string }[],
    knowledge: (d?.knowledge || []) as { id: string; name: string; kind: string; size_bytes: number; uploaded_by_name: string | null }[],
  };
});

function fmtSize(b: number): string {
  return b >= 1048576 ? (b / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(b / 1024)) + " KB";
}
async function toggleSharedAgent(id: string) {
  if (!can("agent.manage")) return;
  const cur = new Set(team.value.sharedAgents);
  cur.has(id) ? cur.delete(id) : cur.add(id);
  await teamsApi.setSharedAgents(teamId.value, [...cur]);
  await load();
}
async function openSharedAgentsModal() {
  if (!chat.profiles.length) await chat.loadProfiles();
  showSharedAgentsModal.value = true;
}
async function addKnowledge() {
  editingKnowledge.value = null;
  showKnowledgeModal.value = true;
}
function editKnowledge(k: { id: string; name: string; kind: string; size_bytes: number }) {
  editingKnowledge.value = k;
  showKnowledgeModal.value = true;
}
function deleteKnowledge(kid: string) {
  confirmAction.value = {
    title: "删除知识条目",
    message: "确认删除该知识条目？此操作不可恢复。",
    confirmText: "删除",
    danger: true,
    onConfirm: async () => {
      await teamsApi.deleteKnowledge(teamId.value, kid);
      confirmAction.value = null;
      await load();
    },
  };
}

const roleLabel = computed(() => ROLE_LABEL[myRole.value] || "成员");
const projCount = computed(() => projects.value.length);

const gov = computed(() => ({
  ROLE_LABEL,
  ROLE_KEYS,
  CONTENT_PERMS: (policy.value?.permissions || []).map((g) => ({
    group: g.group,
    icon: GROUP_ICON[g.group] || "settings",
    items: g.permissions.map((p) => ({ id: p.id, name: p.label, hint: "" })),
  })),
}));

const policyArrays = computed<Record<string, string[]>>(() => {
  const out: Record<string, string[]> = {};
  for (const [pid, roles] of Object.entries(policyMap)) {
    out[pid] = ROLE_KEYS.filter((rk) => roles[rk]);
  }
  return out;
});

const tabs: { id: string; label: string; countKey?: string }[] = [
  { id: "overview", label: "概览" },
  { id: "channel", label: "群聊" },
  { id: "projects", label: "项目", countKey: "__projects" },
  { id: "members", label: "成员", countKey: "members" },
  { id: "agents", label: "共享助手", countKey: "agents" },
  { id: "knowledge", label: "知识库", countKey: "knowledge" },
  { id: "activity", label: "活动" },
  { id: "settings", label: "设置" },
];
const ROLE_OPTIONS = ["管理员", "成员", "只读"];
const ROLE_OPTION_KEY: Record<string, string> = { 管理员: "admin", 成员: "member", 只读: "viewer" };

function roleClass(r: string) {
  if (r.includes("所有")) return "owner";
  if (r.includes("管理")) return "admin";
  return "";
}
function statusLabel(s: string) {
  return ({ online: "在线", idle: "离开", offline: "离线" } as Record<string, string>)[s] || s;
}
function shade(hex: string, percent: number) {
  const c = hex.replace("#", "");
  const r = parseInt(c.slice(0, 2), 16);
  const g = parseInt(c.slice(2, 4), 16);
  const b = parseInt(c.slice(4, 6), 16);
  const adj = (n: number) => Math.max(0, Math.min(255, Math.round(n + (percent / 100) * 255)));
  return "#" + [adj(r), adj(g), adj(b)].map((v) => v.toString(16).padStart(2, "0")).join("");
}
function toggleMenu(h: string) {
  menuFor.value = menuFor.value === h ? null : h;
}

// ── actions wired to API ──
async function togglePolicy(permId: string, roleKey: string) {
  if (roleKey === "owner" || !canEditPolicy.value) return;
  if (!policyMap[permId]) policyMap[permId] = {};
  policyMap[permId][roleKey] = !policyMap[permId][roleKey];
  const res = await teamsApi.updatePolicy(teamId.value, JSON.parse(JSON.stringify(policyMap)));
  policy.value = res;
}
async function resetPolicyDefaults() {
  // re-fetch server defaults is not exposed; clear member/viewer flags to false as a soft reset
  await load();
}
async function changeMemberRole(handleEmail: string, roleZh: string) {
  const m = members.value.find((x) => (x.email || x.user_id) === handleEmail);
  if (!m) return;
  await teamsApi.updateMember(teamId.value, m.user_id, ROLE_OPTION_KEY[roleZh] || "member");
  await load();
  menuFor.value = null;
}
async function removeMember(handleEmail: string) {
  const m = members.value.find((x) => (x.email || x.user_id) === handleEmail);
  if (!m) return;
  if (!confirm(`移出成员 ${m.name}？`)) return;
  await teamsApi.removeMember(teamId.value, m.user_id);
  await load();
  menuFor.value = null;
}
function onInvited(d: TeamDetail) {
  detail.value = d;
  showInvite.value = false;
}
function onProjectCreated(p: Project) {
  showNewProject.value = false;
  router.push(`/projects/${p.id}`);
}
function editProject(p: Project) {
  editingProject.value = p;
  projMenuFor.value = null;
}
function editProjectMembers(p: Project) {
  editingMembersProject.value = p;
  projMenuFor.value = null;
}
function onProjectUpdated(_p: Project) {
  editingProject.value = null;
  load();
}
async function archiveProject(p: Project) {
  await projectsApi.update(p.id, { status: p.status === "active" ? "paused" : "active" });
  await load();
  projMenuFor.value = null;
}
async function deleteProject(p: Project) {
  if (!confirm(`删除项目「${p.name}」？`)) return;
  await projectsApi.remove(p.id);
  await load();
  projMenuFor.value = null;
}
function openProject(id: string) {
  router.push(`/projects/${id}`);
}
async function deleteTeam() {
  if (!confirm(`确认解散团队「${team.value.name}」？此操作不可恢复。`)) return;
  await teamsApi.remove(teamId.value);
  router.push("/");
}
</script>

<template>
  <div class="stage" v-if="detail">
    <div class="team-hero">
      <div class="team-hero-row">
        <div class="team-shield" :style="{ background: 'linear-gradient(180deg,' + team.color + ',' + shade(team.color, -20) + ')' }">
          <Icon :name="team.icon" />
        </div>
        <div class="team-info">
          <div class="team-crumb">团队 · TEAM</div>
          <h1 class="team-name">{{ team.name }}<span class="handle">@{{ team.handle }}</span></h1>
          <div class="team-tagline">{{ team.tagline }}</div>
          <div class="team-meta-row">
            <span class="role-pill">{{ roleLabel }}</span>
            <span><Icon name="user" /> {{ team.stats.members }} 位成员</span>
            <span><Icon name="sparkle" /> {{ team.stats.agents }} 个共享助手</span>
            <span><Icon name="clock" /> {{ team.created }}</span>
            <span><Icon name="star" /> {{ team.plan }}</span>
          </div>
        </div>
        <div class="team-actions">
          <div class="mem-stack" style="margin-right: 8px">
            <div v-for="m in team.members.slice(0, 4)" :key="m.handle" class="mem-avatar" :style="{ background: m.color }" :title="m.name">{{ m.initials }}</div>
            <div v-if="team.members.length > 4" class="mem-more">+{{ team.members.length - 4 }}</div>
          </div>
          <button v-if="can('member.invite')" class="btn" @click="showInvite = true"><Icon name="plus" /> 邀请成员</button>
          <button class="btn primary" @click="router.push({ path: '/', query: { team: teamId } })"><Icon name="chat" /> 在团队中开会话</button>
          <button class="icon-btn" @click="tab = 'settings'" title="团队设置"><Icon name="settings" /></button>
        </div>
      </div>
      <div class="team-tabs">
        <button v-for="tt in tabs" :key="tt.id" class="team-tab" :class="{ active: tab === tt.id }" @click="tab = tt.id">
          {{ tt.label }}
          <span v-if="tt.countKey === '__projects'" class="pill">{{ projCount }}</span>
          <span v-else-if="tt.countKey" class="pill">{{ (team.stats as Record<string, number>)[tt.countKey as string] }}</span>
        </button>
      </div>
    </div>

    <div class="team-body">
      <!-- OVERVIEW -->
      <template v-if="tab === 'overview'">
        <div class="stat-grid">
          <div class="stat"><div class="stat-label"><Icon name="user" /> 成员</div><div class="stat-value">{{ team.stats.members }}</div><div class="stat-foot">活跃成员</div></div>
          <div class="stat"><div class="stat-label"><Icon name="sparkle" /> 共享助手</div><div class="stat-value">{{ team.stats.agents }}</div><div class="stat-foot">已共享</div></div>
          <div class="stat"><div class="stat-label"><Icon name="chat" /> 本周对话</div><div class="stat-value">{{ team.stats.threads }}</div><div class="stat-foot">本周累计</div></div>
          <div class="stat"><div class="stat-label"><Icon name="doc" /> 知识条目</div><div class="stat-value">{{ team.stats.knowledge }}</div><div class="stat-foot">最近更新 <em>{{ lastKnowledgeUpdate }}</em></div></div>
        </div>

        <div class="col-grid">
          <div style="display: flex; flex-direction: column; gap: 18px">
            <div class="section-card">
              <div class="section-head">
                <div class="section-title"><Icon name="sparkle" /> 共享助手</div>
                <button class="section-link" @click="openSharedAgentsModal">管理 <Icon name="chevron_right" /></button>
              </div>
              <div class="agent-mini-grid">
                <button v-for="id in team.sharedAgents" :key="id" class="agent-mini" @click="router.push('/')">
                  <div class="agent-icon" :style="{ background: agentInfo(id).color || '#b8852a' }"><Icon :name="agentInfo(id).icon || 'sparkle'" /></div>
                  <div style="min-width: 0; flex: 1"><div class="nm">{{ agentInfo(id).label }}</div><div class="ds">{{ agentInfo(id).description }}</div></div>
                </button>
              </div>
            </div>
            <div class="section-card">
              <div class="section-head"><div class="section-title"><Icon name="pin" /> 团队置顶</div></div>
              <div class="section-body">
                <div v-if="!team.pinned.length" style="padding: 24px; text-align: center; color: var(--ink-mute); font-size: 12.5px">还没有置顶会话。</div>
                <div v-for="cv in team.pinned" :key="cv.id" class="row-item" @click="router.push({ path: '/', query: { c: cv.id } })">
                  <div class="convo-ico" :style="{ background: (agentInfo(cv.primary_agent_id).color || '#b8852a') + '22', color: agentInfo(cv.primary_agent_id).color || '#b8852a' }"><Icon :name="agentInfo(cv.primary_agent_id).icon || 'chat'" /></div>
                  <div class="row-text"><div class="row-title">{{ cv.title }}</div><div class="row-sub">{{ agentInfo(cv.primary_agent_id).label }}</div></div>
                  <Icon name="chevron_right" style="width: 14px; height: 14px; color: var(--ink-mute)" />
                </div>
              </div>
            </div>
          </div>

          <div style="display: flex; flex-direction: column; gap: 18px">
            <div class="section-card">
              <div class="section-head">
                <div class="section-title"><Icon name="user" /> 成员</div>
                <button class="section-link" @click="tab = 'members'">全部 ({{ team.stats.members }}) <Icon name="chevron_right" /></button>
              </div>
              <div class="section-body">
                <div v-for="m in team.members.slice(0, 6)" :key="m.handle" class="row-item">
                  <div class="mem-avatar" :style="{ background: m.color }">{{ m.initials }}<span class="status" :class="m.status"></span></div>
                  <div class="row-text">
                    <div class="row-title">{{ m.name }} <span style="color: var(--ink-mute); font-weight: 400; font-size: 11.5px; margin-left: 4px">@{{ m.handle }}</span></div>
                    <div class="row-sub">{{ m.last }}</div>
                  </div>
                  <span class="mem-role" :class="roleClass(m.role)">{{ m.role }}</span>
                </div>
              </div>
            </div>
            <div class="section-card">
              <div class="section-head"><div class="section-title"><Icon name="bolt" /> 最近活动</div><button class="section-link" @click="tab = 'activity'">全部 <Icon name="chevron_right" /></button></div>
              <div class="section-body flush">
                <div v-if="!team.activity.length" style="padding: 24px; text-align: center; color: var(--ink-mute); font-size: 12.5px">暂无活动。</div>
                <div v-for="(a, i) in team.activity.slice(0, 5)" :key="i" class="activity-item">
                  <div class="activity-dot"><Icon :name="a.icon" /></div>
                  <div style="flex: 1; min-width: 0"><div class="activity-text"><b>{{ a.who }}</b> {{ a.action }} <em>{{ a.target }}</em></div><div class="activity-time">{{ a.ago }}</div></div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </template>

      <!-- PROJECTS -->
      <template v-else-if="tab === 'projects'">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px">
          <div>
            <div style="font-family: var(--font-serif); font-size: 22px; font-weight: 600; color: var(--ink)">项目 · {{ projects.length }}</div>
            <div style="font-size: 12.5px; color: var(--ink-mute); margin-top: 2px">每个项目是一组对话、文件和共享助手围绕同一个目标展开。</div>
          </div>
          <button v-if="can('project.create')" class="btn primary" @click="showNewProject = true"><Icon name="plus" /> 新建项目</button>
        </div>
        <div class="proj-grid">
          <div v-for="p in projects" :key="p.id" class="proj-card-wrap">
            <button class="proj-card" @click="openProject(p.id)">
              <div class="proj-card-head">
                <div class="proj-icon" :style="{ background: p.color || '#b8852a' }"><Icon :name="p.icon || 'sparkle'" /></div>
                <div style="min-width: 0; flex: 1">
                  <div class="proj-name">{{ p.name }}</div>
                  <div class="proj-handle">@{{ p.handle }}</div>
                </div>
                <span class="proj-status" :class="p.status"><span class="dot"></span>{{ p.status === "active" ? "进行中" : "已暂停" }}</span>
              </div>
              <div class="proj-summary">{{ p.summary || "（暂无简介）" }}</div>
              <div class="proj-progress"><div :style="{ width: p.progress + '%' }"></div></div>
              <div class="proj-foot">
                <span>{{ p.progress }}% · 截至 {{ p.deadline || "—" }}</span>
                <span><Icon name="sparkle" :size="11" /> {{ (p.pinned_agents || []).length }}</span>
              </div>
            </button>
            <div v-if="can('project.edit') || can('project.delete')" style="position: absolute; top: 12px; right: 12px">
              <button class="card-menu-btn" @click.stop="projMenuFor = projMenuFor === p.id ? null : p.id"><Icon name="settings" :size="14" /></button>
              <div v-if="projMenuFor === p.id" class="menu" style="top: 32px; right: 0; min-width: 170px" @click.stop>
                <button v-if="can('project.edit')" class="menu-item" @click="editProject(p)"><Icon name="edit" /> <span class="m-name">编辑项目</span></button>
                <button v-if="can('project.edit')" class="menu-item" @click="editProjectMembers(p)"><Icon name="user" /> <span class="m-name">管理成员</span></button>
                <div v-if="can('project.edit')" class="menu-sep"></div>
                <button v-if="can('project.delete')" class="menu-item" @click="archiveProject(p)"><Icon name="pin" /> <span class="m-name">{{ p.status === "active" ? "归档项目" : "重新启用" }}</span></button>
                <div v-if="can('project.delete')" class="menu-sep"></div>
                <button v-if="can('project.delete')" class="menu-item danger" @click="deleteProject(p)"><Icon name="close" /> <span class="m-name">删除项目</span></button>
              </div>
            </div>
          </div>
        </div>
        <div v-if="!projects.length" style="padding: 48px; text-align: center; color: var(--ink-mute); font-size: 13px">还没有项目。<template v-if="can('project.create')">点击右上角「新建项目」开始。</template></div>
      </template>

      <!-- MEMBERS -->
      <template v-else-if="tab === 'members'">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px">
          <div>
            <div style="font-family: var(--font-serif); font-size: 22px; font-weight: 600; color: var(--ink)">所有成员 · {{ team.members.length }}</div>
            <div style="font-size: 12.5px; color: var(--ink-mute); margin-top: 2px">管理成员角色、权限与可访问的助手。</div>
          </div>
          <button v-if="can('member.invite')" class="btn primary" @click="showInvite = true"><Icon name="plus" /> 邀请成员</button>
        </div>
        <div class="table">
          <div class="table-row head"><div>成员</div><div>角色</div><div>状态</div><div class="col-last-active">最近活动</div><div></div></div>
          <div v-for="m in team.members" :key="m.handle" class="table-row">
            <div class="mem-cell">
              <div class="mem-avatar" :style="{ background: m.color }">{{ m.initials }}<span class="status" :class="m.status"></span></div>
              <div style="min-width: 0"><div class="nm">{{ m.name }}</div><div class="hd">@{{ m.handle }}</div></div>
            </div>
            <div><span class="mem-role" :class="roleClass(m.role)">{{ m.role }}</span></div>
            <div style="font-size: 12px; color: var(--ink-mute)"><span class="dot-inline" :class="m.status"></span>{{ statusLabel(m.status) }}</div>
            <div class="col-last-active" style="font-size: 12px; color: var(--ink-mute)">{{ m.last }}</div>
            <div style="position: relative">
              <button v-if="(can('member.role') || can('member.remove')) && m.roleKey !== 'owner'" class="icon-btn" @click.stop="toggleMenu(m.handle)"><Icon name="settings" /></button>
              <div v-if="menuFor === m.handle" class="menu" style="top: 34px; right: 0; min-width: 180px" @click.stop>
                <template v-if="can('member.role')">
                  <div class="menu-label">调整角色</div>
                  <button v-for="r in ROLE_OPTIONS" :key="r" class="menu-item" :class="{ active: m.role === r }" @click="changeMemberRole(m.handle, r)">
                    <Icon name="user" /><span class="m-name">{{ r }}</span><Icon v-if="m.role === r" name="check" style="margin-left: auto" />
                  </button>
                </template>
                <div v-if="can('member.role') && can('member.remove')" class="menu-sep"></div>
                <button v-if="can('member.remove')" class="menu-item danger" @click="removeMember(m.handle)"><Icon name="logout" /> <span class="m-name">移出团队</span></button>
              </div>
            </div>
          </div>
        </div>
      </template>

      <!-- AGENTS -->
      <template v-else-if="tab === 'agents'">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px">
          <div><div style="font-family: var(--font-serif); font-size: 22px; font-weight: 600; color: var(--ink)">助手 · {{ teamProfiles.length }}</div><div style="font-size: 12.5px; color: var(--ink-mute); margin-top: 2px">团队可用的助手（含全局助手）。点击可直接开启对话。</div></div>
          <button v-if="can('agent.manage')" class="btn primary" @click="openSharedAgentsModal"><Icon name="settings" /> 管理共享</button>
        </div>
        <div class="agents-grid" style="max-width: none">
          <button
            v-for="p in teamProfiles"
            :key="p.id"
            class="agent-card"
            :style="'border-color:var(--accent-soft);box-shadow:var(--shadow-sm);text-align:left;'"
            @click="router.push({ path: '/', query: { team: teamId, profile: p.id } })"
          >
            <div class="agent-icon" :style="{ background: p.color || '#b8852a' }"><Icon :name="p.icon || 'sparkle'" /></div>
            <div class="agent-meta">
              <div class="agent-name">{{ p.name }}<span v-if="p.scope === 'global'" class="official">全局</span></div>
              <div class="agent-desc">{{ p.desc || p.default_model }}</div>
            </div>
          </button>
          <div v-if="!teamProfiles.length" style="grid-column: 1/-1; padding: 40px; text-align: center; color: var(--ink-mute); font-size: 13px">暂无可用助手，请联系管理员在后台配置。</div>
        </div>
      </template>

      <!-- KNOWLEDGE -->
      <template v-else-if="tab === 'knowledge'">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px">
          <div><div style="font-family: var(--font-serif); font-size: 22px; font-weight: 600; color: var(--ink)">知识库 · {{ team.knowledge.length }}</div><div style="font-size: 12.5px; color: var(--ink-mute); margin-top: 2px">所有团队助手都能引用这些文件作为上下文。点击文件可预览内容。</div></div>
          <button v-if="can('knowledge.upload')" class="btn primary" @click="addKnowledge"><Icon name="paperclip" /> 上传文件</button>
        </div>
        <div class="section-card">
          <div v-if="!team.knowledge.length" style="padding: 40px; text-align: center; color: var(--ink-mute); font-size: 13px">知识库还是空的。</div>
          <div
            v-for="f in team.knowledge"
            :key="f.id"
            class="file-row has-actions"
            style="cursor: pointer"
            @click="openKnowledgeFile(f.id)"
          >
            <div class="file-ico"><Icon name="doc" /></div>
            <div style="flex: 1; min-width: 0"><div class="row-title">{{ f.name }}</div><div class="file-meta">{{ fmtSize(f.size_bytes) }} · 由 {{ f.uploaded_by_name || "成员" }} 上传</div></div>
            <span class="file-kind">{{ f.kind }}</span>
            <div class="row-actions" @click.stop>
              <button v-if="can('knowledge.upload')" class="row-act" title="编辑元数据" @click="editKnowledge(f)"><Icon name="edit" :size="13" /></button>
              <button v-if="can('knowledge.delete')" class="row-act danger" title="删除" @click="deleteKnowledge(f.id)"><Icon name="close" :size="13" /></button>
            </div>
          </div>
        </div>
      </template>

      <!-- CHANNEL (group chat) -->
      <template v-else-if="tab === 'channel'">
        <div style="display:flex;justify-content:space-between;align-items:flex-end;margin-bottom:14px">
          <div>
            <div style="font-family:var(--font-serif);font-size:22px;font-weight:600;color:var(--ink)">群聊频道</div>
            <div style="font-size:12.5px;color:var(--ink-mute);margin-top:2px">团队成员共享频道 · 输入 @助手名 呼出助手，或开启「始终响应」模式</div>
          </div>
          <div style="display:flex;align-items:center;gap:8px">
            <span style="font-size:12px;color:var(--ink-mute)">响应模式：</span>
            <select :value="channelMode" @change="updateChannelMode(($event.target as HTMLSelectElement).value)"
              style="font-size:12.5px;padding:4px 8px;border:1px solid var(--rule);border-radius:6px;background:var(--bg-canvas);color:var(--ink)">
              <option value="off">不自动响应</option>
              <option value="mention">@提及响应</option>
              <option value="always">始终响应</option>
            </select>
          </div>
        </div>

        <div class="section-card" style="display:flex;flex-direction:column;height:520px">
          <div ref="channelScroller" style="flex:1;overflow-y:auto;padding:14px 18px;display:flex;flex-direction:column;gap:10px">
            <div v-if="channelLoading" style="text-align:center;color:var(--ink-mute);font-size:13px;padding:40px 0">加载中…</div>
            <div v-else-if="!channelMessages.length" style="text-align:center;color:var(--ink-mute);font-size:13px;padding:40px 0">
              还没有消息。发送 @hermes + 问题，或开启「始终响应」模式。
            </div>
            <template v-for="m in channelMessages" :key="m.id">
              <!-- User message row -->
              <div v-if="m.role === 'user'" class="ch-row user" style="align-items:flex-end;gap:8px">
                <div class="ch-bubble" v-html="m.content.text"></div>
                <div class="ch-av"
                  :style="{ background: channelMemberInfo(m.owner_id)?.color || 'var(--accent)' }"
                  :title="channelMemberInfo(m.owner_id)?.name || '成员'"
                  @click.stop="showChCard($event, m)">
                  {{ channelMemberInfo(m.owner_id)?.initials || '?' }}
                </div>
              </div>
              <!-- Agent message row -->
              <div v-else class="ch-row agent" style="align-items:flex-end;gap:8px">
                <div class="ch-av-icon"
                  :style="{ background: (agentInfo(m.agent_id || 'hermes').color || '#b8852a') + '22' }"
                  :title="agentInfo(m.agent_id || 'hermes').label"
                  @click.stop="showChCard($event, m)">
                  <Icon :name="agentInfo(m.agent_id || 'hermes').icon || 'sparkle'" :size="16"
                    :style="{ color: agentInfo(m.agent_id || 'hermes').color || '#b8852a' }" />
                </div>
                <div class="ch-bubble" v-html="m.content.markdown || m.content.text || '…'"></div>
              </div>
            </template>
          </div>

          <!-- Knowledge attachment chips -->
          <div v-if="channelAttachments.length" style="padding:6px 14px 0;display:flex;flex-wrap:wrap;gap:6px">
            <span v-for="a in channelAttachments" :key="a.id"
              style="display:inline-flex;align-items:center;gap:4px;background:var(--accent-soft);color:var(--accent);font-size:11.5px;padding:2px 8px;border-radius:12px">
              <Icon name="doc" :size="10" />{{ a.name }}
              <button @click="removeChannelAttachment(a.id)" style="background:none;border:none;cursor:pointer;color:var(--ink-mute);padding:0;line-height:1">×</button>
            </span>
          </div>

          <div style="padding:10px 14px;border-top:1px solid var(--rule-soft);display:flex;flex-direction:column;gap:6px">
            <!-- Knowledge picker -->
            <div v-if="showKnowledgePicker" style="position:relative">
              <div style="background:var(--bg-panel);border:1px solid var(--rule);border-radius:8px;padding:6px;max-height:180px;overflow-y:auto;box-shadow:var(--shadow-lg)">
                <div v-if="!team.knowledge.length" style="padding:8px;font-size:12.5px;color:var(--ink-mute)">没有知识库文件</div>
                <button v-for="k in team.knowledge" :key="k.id" class="menu-item" style="width:100%;text-align:left" @click="addChannelAttachment(k)">
                  <Icon name="doc" :size="13" />{{ k.name }}
                </button>
              </div>
            </div>
            <div style="display:flex;gap:8px;align-items:flex-end">
              <button class="icon-btn" title="引用知识库" @click="toggleKnowledgePicker" :style="showKnowledgePicker ? 'color:var(--accent)' : ''"><Icon name="paperclip" :size="15" /></button>
              <textarea
                v-model="channelDraft"
                placeholder="发消息… 输入 @hermes 呼出助手"
                rows="2"
                style="flex:1;resize:none;padding:8px 12px;border:1px solid var(--rule);border-radius:8px;font-size:13.5px;background:var(--bg-canvas);color:var(--ink);outline:none;font-family:inherit"
                @keydown.enter.exact.prevent="sendChannelMessage"
              ></textarea>
              <button class="btn primary" :disabled="!channelDraft.trim() || channelSending" @click="sendChannelMessage" style="height:36px;min-width:60px">
                <Icon name="send" :size="14" />
              </button>
            </div>
          </div>
        </div>

        <!-- Avatar info popover -->
        <div v-if="chCardMsg" class="ch-card" :style="{ left: chCardPos.x + 'px', top: chCardPos.y + 'px' }" @click.stop>
          <template v-if="chCardMsg.role === 'user'">
            <div class="ch-card-name">{{ channelMemberInfo(chCardMsg.owner_id)?.name || '成员' }}</div>
            <div class="ch-card-meta">{{ members.find(x => x.user_id === chCardMsg.owner_id)?.role || '' }}</div>
          </template>
          <template v-else>
            <div class="ch-card-name">{{ agentInfo(chCardMsg.agent_id || 'hermes').label }}</div>
            <div class="ch-card-meta">{{ agentInfo(chCardMsg.agent_id || 'hermes').description }}</div>
          </template>
        </div>
      </template>

      <!-- ACTIVITY -->
      <template v-else-if="tab === 'activity'">
        <div style="margin-bottom: 14px"><div style="font-family: var(--font-serif); font-size: 22px; font-weight: 600; color: var(--ink)">活动日志</div><div style="font-size: 12.5px; color: var(--ink-mute); margin-top: 2px">团队内的动作，按时间倒序。</div></div>
        <div class="section-card">
          <div v-if="!team.activity.length" style="padding: 40px; text-align: center; color: var(--ink-mute); font-size: 13px">暂无活动记录。</div>
          <div v-for="(a, i) in team.activity" :key="i" class="activity-item">
            <div class="activity-dot"><Icon :name="a.icon" /></div>
            <div style="flex: 1; min-width: 0"><div class="activity-text"><b>{{ a.who }}</b> {{ a.action }} <em>{{ a.target }}</em></div><div class="activity-time">{{ a.ago }}</div></div>
          </div>
        </div>
      </template>

      <!-- SETTINGS -->
      <template v-else-if="tab === 'settings'">
        <div style="margin-bottom: 14px">
          <div style="font-family: var(--font-serif); font-size: 22px; font-weight: 600; color: var(--ink)">团队设置</div>
          <div style="font-size: 12.5px; color: var(--ink-mute); margin-top: 2px">基本信息与内容权限。只有所有者与管理员可修改。</div>
        </div>
        <div class="section-card">
          <div class="section-head"><div class="section-title">基本信息</div></div>
          <div style="padding: 18px">
            <div style="display: grid; grid-template-columns: 140px 1fr; gap: 14px 18px; font-size: 13px">
              <div style="color: var(--ink-mute)">团队名称</div><div>{{ team.name }}</div>
              <div style="color: var(--ink-mute)">标识符</div><div style="font-family: var(--font-mono); font-size: 12.5px">@{{ team.handle }}</div>
              <div style="color: var(--ink-mute)">标语</div><div style="font-style: italic; font-family: var(--font-serif)">{{ team.tagline }}</div>
              <div style="color: var(--ink-mute)">套餐</div><div>{{ team.plan }}</div>
              <div style="color: var(--ink-mute)">创建时间</div><div>{{ team.created }}</div>
            </div>
          </div>
        </div>

        <div class="section-card" style="margin-top: 14px" v-if="policy">
          <div class="section-head">
            <div class="section-title"><Icon name="settings" /> 内容权限</div>
            <button v-if="canEditPolicy" class="section-link" @click="resetPolicyDefaults"><Icon name="refresh" :size="12" /> 重新加载</button>
          </div>
          <div class="perm-note">
            <span>勾选某个角色后，该角色即可执行对应的团队内容操作。<b>所有者</b>始终拥有全部权限。</span>
            <span class="perm-yours">你的角色：<span class="mem-role" :class="roleClass(roleLabel)">{{ roleLabel }}</span><template v-if="!canEditPolicy"> · 仅可查看</template></span>
          </div>
          <div class="perm-table team-perm" :class="{ readonly: !canEditPolicy }">
            <div class="perm-grid team-grid">
              <div class="cell head">内容操作</div>
              <div v-for="rk in gov.ROLE_KEYS" :key="rk" class="cell head center">{{ gov.ROLE_LABEL[rk] }}</div>
              <template v-for="g in gov.CONTENT_PERMS" :key="g.group">
                <div class="group-row"><Icon :name="g.icon" :size="13" /> {{ g.group }}</div>
                <template v-for="p in g.items" :key="p.id">
                  <div class="cell"><div><div>{{ p.name }}</div><div class="perm-hint">{{ p.hint }}</div></div></div>
                  <div v-for="rk in gov.ROLE_KEYS" :key="rk" class="cell center">
                    <button class="perm-cell"
                            :class="{ on: (policyArrays[p.id] || []).includes(rk) || rk === 'owner', locked: rk === 'owner' || !canEditPolicy }"
                            :disabled="rk === 'owner' || !canEditPolicy"
                            @click="togglePolicy(p.id, rk)">
                      <Icon v-if="(policyArrays[p.id] || []).includes(rk) || rk === 'owner'" name="check" :size="11" />
                    </button>
                  </div>
                </template>
              </template>
            </div>
          </div>
        </div>

        <div class="section-card" style="margin-top: 14px">
          <div class="section-head"><div class="section-title" style="color: var(--danger)">危险区</div></div>
          <div style="padding: 16px 18px; display: flex; align-items: center; justify-content: space-between">
            <div>
              <div style="font-weight: 600; color: var(--ink)">解散团队</div>
              <div style="font-size: 12px; color: var(--ink-mute); margin-top: 2px">将删除所有团队会话、共享助手与知识条目。不可恢复。</div>
            </div>
            <button class="btn" :disabled="myRole !== 'owner'" :style="myRole === 'owner' ? 'color:var(--danger);border-color:var(--danger);' : ''" @click="myRole === 'owner' && deleteTeam()">解散</button>
          </div>
        </div>
      </template>
    </div>

    <NewProjectModal
      v-if="showNewProject"
      :team-id="teamId"
      :team-name="team.name"
      :members="members"
      @close="showNewProject = false"
      @created="onProjectCreated"
    />
    <NewProjectModal
      v-if="editingProject"
      :team-id="teamId"
      :team-name="team.name"
      :members="members"
      :project="editingProject"
      @close="editingProject = null"
      @updated="onProjectUpdated"
    />
    <ProjectMembersModal
      v-if="editingMembersProject"
      :project-id="editingMembersProject.id"
      :project-name="editingMembersProject.name"
      :team-members="members"
      :current-member-ids="(editingMembersProject as any).member_ids || []"
      @close="editingMembersProject = null"
      @updated="load()"
    />
    <InviteMembersModal
      v-if="showInvite"
      :team="{ id: teamId, name: team.name, handle: team.handle }"
      @close="showInvite = false"
      @invited="onInvited"
    />
    <KnowledgeModal
      v-if="showKnowledgeModal"
      :team-id="teamId"
      :editing="editingKnowledge"
      @close="showKnowledgeModal = false"
      @saved="showKnowledgeModal = false; editingKnowledge = null; load()"
    />
    <SharedAgentsModal
      v-if="showSharedAgentsModal"
      :team-id="teamId"
      :shared-agent-ids="team.sharedAgents"
      @close="showSharedAgentsModal = false"
      @toggle="toggleSharedAgent"
    />
    <ConfirmModal
      v-if="confirmAction"
      :title="confirmAction.title"
      :message="confirmAction.message"
      :confirm-text="confirmAction.confirmText"
      :danger="confirmAction.danger"
      @close="confirmAction = null"
      @confirm="confirmAction.onConfirm()"
    />
    <WorkspacePanel
      v-if="showKnowledgePanel && knowledgeFiles.length"
      :files="knowledgeFiles"
      :adapter="kbAdapter"
      :initial-file-id="clickedKnowledgeId || undefined"
      title="知识库"
      :uploadable="can('knowledge.upload')"
      @close="showKnowledgePanel = false"
    />
  </div>
</template>
