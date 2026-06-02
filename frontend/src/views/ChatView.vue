<script setup lang="ts">
/* 1:1 port of the prototype chat (hermes-app.js landing + thread), main-content
   only — the sidebar/topbar live in AppLayout. Uses the prototype CSS classes
   so it renders pixel-identical; wired to the real chat store. */
import { computed, nextTick, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import Icon from "@/components/Icon.vue";
import Composer from "@/components/Composer.vue";
import ConfirmModal from "@/components/ConfirmModal.vue";
import ConvoSeal from "@/components/ConvoSeal.vue";
import WorkspacePanel from "@/components/WorkspacePanel.vue";
import ExtractItemsModal from "@/components/ExtractItemsModal.vue";
import { useChatStore } from "@/stores/chat";
import { useNotificationStore } from "@/stores/notifications";
import { conversationsApi } from "@/api/conversations";
import { renderMarkdown } from "@/utils/markdown";
import type { Agent, WsAdapter } from "@/types";
import type { SendOptions } from "@/components/Composer.vue";

const chat = useChatStore();
const ns = useNotificationStore();
const route = useRoute();
const router = useRouter();

const draft = ref("");
const scroller = ref<HTMLElement | null>(null);
const showWorkspace = ref(false);
const showAgentMenu = ref(false);
const showExtractModal = ref(false);
const agentTab = ref("全部");
const landingAgentId = ref("hermes");
// roundtable per-reply chosen state (keyed by messageId:slot)
const chosenMap = ref<Record<string, boolean>>({});

const AGENT_TABS = ["全部", "官方", "协作", "办公", "创作"];

onMounted(async () => {
  if (!chat.agents.length) await chat.loadAgents();
  if (!chat.profiles.length) await chat.loadProfiles();
  const cid = route.query.c as string | undefined;
  const teamCtx = route.query.team as string | undefined;
  const projCtx = route.query.project as string | undefined;
  const seed = route.query.seed as string | undefined;
  if (cid) {
    await chat.openConversation(cid);
    await scrollDown();
  } else if (teamCtx || projCtx) {
    const d = await conversationsApi.create({ primary_agent_id: "hermes", team_id: teamCtx, project_id: projCtx, first_message: seed });
    await chat.loadConversations();
    await chat.openConversation(d.id);
    if (seed) draft.value = seed;
    await scrollDown();
  }
});

// ── Greeting: time-aware + voice-aware ──
const greeting = computed(() => {
  const hour = new Date().getHours();
  const timePart = hour < 6 ? "夜深了" : hour < 11 ? "早上好" : hour < 14 ? "中午好" : hour < 18 ? "下午好" : "晚上好";
  let voice = "warm";
  try { voice = JSON.parse(localStorage.getItem("hermes.tweaks") || "{}").voice || "warm"; } catch { /* noop */ }
  if (voice === "classical") return { main: "凡所欲遣，<em>皆可托信使</em>。", sub: "Quidquid mittere vis, mihi crede." };
  if (voice === "engineering") return { main: `> <em>hermes</em> ready —`, sub: `agents: ${chat.activeAgents.length} active · model: ACP · uptime: 99.9%` };
  return { main: `${timePart}，<em>今天有什么安排？</em>`, sub: "Ask me anything · 我会调度合适的助手为你完成。" };
});

// ── Agent tab filtering ──
const filteredAgents = computed(() => {
  if (agentTab.value === "全部") return chat.agents;
  if (agentTab.value === "官方") return chat.agents.filter((a) => a.official);
  return chat.agents.filter((a) => a.kind === agentTab.value);
});

const hermes = computed(() => chat.agents.find((a) => a.id === "hermes"));
const landingAgent = computed(() => agentById(landingAgentId.value) || hermes.value);
const activeConvo = computed(() => chat.conversations.find((c) => c.id === chat.activeId));
const primaryAgent = computed(() => agentById(activeConvo.value?.primary_agent_id || "hermes") || hermes.value);

// ── Team / project context tags in thread meta ──
const convoTeamName = computed(() => {
  const tid = activeConvo.value?.team_id;
  return tid ? chat.teams.find((t) => t.id === tid)?.name : null;
});
const convoProjectName = computed(() => {
  return (activeConvo.value as any)?.project_name || null;
});

function agentById(id: string): Agent | undefined {
  return chat.agents.find((a) => a.id === id);
}

// Return display info for an agent, preferring profile data over raw agent metadata.
function agentDisplay(id: string): { label: string; icon: string; color: string; description: string } {
  const profile = chat.profiles.find((p) => p.default_agent_id === id || p.handle === id);
  if (profile) return { label: profile.name, icon: profile.icon || "sparkle", color: profile.color || "#b8852a", description: profile.desc || "" };
  const raw = agentById(id);
  return { label: raw?.label || id, icon: raw?.icon || "sparkle", color: raw?.color || "#b8852a", description: raw?.description || "" };
}

function md(text: string) {
  return renderMarkdown(text);
}

// Profiles available to add to the roundtable (each maps to a unique agent).
const availableToAdd = computed(() => {
  const activeProfileAgentIds = new Set(chat.activeAgents);
  // Prefer profiles for the picker; fall back to raw agents that have no profile.
  const profileItems = chat.profiles
    .filter((p) => p.is_active && p.default_agent_id && !activeProfileAgentIds.has(p.default_agent_id))
    .map((p) => ({ id: p.default_agent_id, label: p.name, icon: p.icon || "sparkle", color: p.color || "#b8852a", description: p.desc || "" }));
  const profileAgentIds = new Set(profileItems.map((p) => p.id));
  const rawItems = chat.agents
    .filter((a) => a.available && !activeProfileAgentIds.has(a.id) && !profileAgentIds.has(a.id))
    .map((a) => ({ id: a.id, label: a.label, icon: a.icon || "sparkle", color: a.color || "#b8852a", description: a.description || "" }));
  return [...profileItems, ...rawItems];
});

async function scrollDown() {
  await nextTick();
  if (scroller.value) scroller.value.scrollTop = scroller.value.scrollHeight;
}
watch(() => chat.messages.map((m) => m.content.text).join("|"), scrollDown);
watch(() => chat.activeId, scrollDown);

const openFileId = ref<string | null>(null);

async function onSend(opts?: SendOptions) {
  const text = draft.value.trim();
  if (!text || chat.streaming) return;
  draft.value = "";
  if (opts?.stagedFiles?.length) showWorkspace.value = true;
  await chat.send(text, landingAgentId.value, opts);
  await scrollDown();
}

function openFile(fid: string) {
  openFileId.value = fid;
  showWorkspace.value = true;
}
function pickAgent(a: Agent) {
  landingAgentId.value = a.id;
}

// ── Message actions ──
async function copyMessage(text: string) {
  try {
    await navigator.clipboard.writeText(text);
    ns.toast("已复制到剪贴板");
  } catch {
    ns.toast("复制失败", "error");
  }
}
function shareMessage(conversationId: string) {
  const url = `${location.origin}/?c=${conversationId}`;
  copyMessage(url);
}

// ── Roundtable vote actions ──
function toggleChosen(msgId: string, slot: number) {
  const key = `${msgId}:${slot}`;
  chosenMap.value[key] = !chosenMap.value[key];
}
function isChosen(msgId: string, slot: number): boolean {
  return !!chosenMap.value[`${msgId}:${slot}`];
}
function followUp(agentId: string) {
  // set agent as primary and focus the composer
  landingAgentId.value = agentId;
  (document.querySelector(".dock .composer-input") as HTMLTextAreaElement)?.focus();
}

const wsAdapter = computed<WsAdapter>(() => {
  const cid = chat.activeId;
  return {
    getContent: (fid) => conversationsApi.fileContent(cid!, fid).then((r) => r.content || ""),
    getRawUrl: (fid) => conversationsApi.fileRawUrl(cid!, fid),
    patchContent: async (fid, cnt) => (await conversationsApi.patchFile(cid!, fid, cnt)).content || "",
    getVersions: (fid) => conversationsApi.fileVersions(cid!, fid),
    restoreVersion: async (fid, v) => (await conversationsApi.restoreVersion(cid!, fid, v)).content || "",
    upload: (file) => conversationsApi.upload(cid!, file).then(() => undefined),
  };
});
</script>

<template>
  <div class="stage">
    <!-- LANDING -->
    <div v-if="!chat.activeId" class="landing">
      <div class="landing-inner">
        <h1 class="hello" v-html="greeting.main"></h1>
        <div class="hello-sub">{{ greeting.sub }}</div>

        <!-- agent switcher -->
        <div class="agent-bar">
          <button class="agent-chip active">
            <span class="avatar" :style="{ background: landingAgent?.color || '#b8852a' }"><Icon :name="landingAgent?.icon || 'brand'" :size="11" /></span>
            {{ landingAgent?.label || "Hermes" }}
          </button>
          <span class="agent-divider"></span>
          <button class="agent-add" title="添加助手" @click="showAgentMenu = !showAgentMenu"><Icon name="plus" :size="14" /></button>
        </div>

        <Composer
          v-model="draft"
          :placeholder="`给 ${landingAgent?.label || 'Hermes'} 发消息…  ⌘K 搜索 · Enter 发送`"
          :agent="{ label: landingAgent?.label, color: landingAgent?.color, model: landingAgent?.version || 'ACP' }"
          :streaming="chat.streaming"
          @send="onSend"
        />

        <!-- agents grid -->
        <div class="agents-section">
          <div class="agents-head">
            <div class="agents-title">选择一位助手开始任务</div>
            <div class="agents-tabs">
              <button v-for="t in AGENT_TABS" :key="t" class="agents-tab" :class="{ active: t === agentTab }" @click="agentTab = t">{{ t }}</button>
            </div>
          </div>
          <div class="agents-grid">
            <button v-for="a in filteredAgents" :key="a.id" class="agent-card" @click="pickAgent(a)">
              <div class="agent-icon" :style="{ background: a.color || '#b8852a' }"><Icon :name="a.icon || 'sparkle'" :size="16" /></div>
              <div class="agent-meta">
                <div class="agent-name">{{ a.label }}<span v-if="a.official" class="official">OFFICIAL</span></div>
                <div class="agent-desc">{{ a.description }}</div>
              </div>
            </button>
            <div v-if="!filteredAgents.length" style="grid-column:1/-1;color:var(--ink-mute);font-size:13px;padding:16px 0;">
              暂无该分类的助手
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- THREAD -->
    <div v-else class="thread-split" :class="{ 'ws-closed': !showWorkspace }">
      <div ref="scroller" class="thread">
        <div class="thread-inner">
          <div class="thread-head" style="display:flex;align-items:flex-start;justify-content:space-between;gap:14px;">
            <div style="flex:1;min-width:0;display:flex;align-items:flex-start;gap:10px;">
              <ConvoSeal v-if="chat.activeId" :seed="chat.activeId" :size="40" style="margin-top:2px;" />
              <div style="min-width:0;">
                <h2 class="thread-title">{{ activeConvo?.title || "对话" }}</h2>
                <div class="thread-meta" style="margin-top:5px;">
                  <span class="agent-tag"><Icon :name="primaryAgent?.icon || 'brand'" :size="10" /> {{ primaryAgent?.label || "Hermes" }}</span>
                  <span v-if="chat.activeAgents.length > 1" class="agent-tag" style="background:rgba(184,133,42,0.14);color:var(--accent-deep);">
                    <Icon name="sparkle" :size="10" /> 圆桌 · {{ chat.activeAgents.length }} 位并行
                  </span>
                  <span v-if="convoTeamName" class="agent-tag" style="background:rgba(58,109,161,0.12);color:#3a6da1;">
                    <Icon name="user" :size="10" /> {{ convoTeamName }}
                  </span>
                  <span v-if="convoProjectName" class="agent-tag" style="background:rgba(90,140,74,0.12);color:#3a7a4a;">
                    <Icon name="cube" :size="10" /> {{ convoProjectName }}
                  </span>
                </div>
              </div>
            </div>
            <button class="thread-action" v-if="chat.files.length" @click="showWorkspace = !showWorkspace" style="flex-shrink:0;margin-top:2px;">
              <Icon name="folder" /> 工作区 ({{ chat.files.length }})
            </button>
            <button class="thread-action" v-if="chat.messages.length >= 2" @click="showExtractModal = true" style="flex-shrink:0;margin-top:2px;" title="从对话内容自动创建项目与任务">
              <Icon name="sparkle" /> 智能创建
            </button>
          </div>

          <!-- agent switcher -->
          <div class="agent-bar" style="align-self: flex-start">
            <button v-for="aid in chat.activeAgents" :key="aid" class="agent-chip" :class="{ active: aid === activeConvo?.primary_agent_id }">
              <span class="avatar" :style="{ background: agentDisplay(aid).color }"><Icon :name="agentDisplay(aid).icon" :size="11" /></span>
              {{ agentDisplay(aid).label }}
              <span v-if="aid !== 'hermes'" style="margin-left:4px;cursor:pointer;color:var(--ink-mute);" @click.stop="chat.toggleAgent(aid)">×</span>
            </button>
            <span class="agent-divider"></span>
            <div style="position: relative">
              <button class="agent-add" title="添加助手" @click="showAgentMenu = !showAgentMenu"><Icon name="plus" :size="14" /></button>
              <div v-if="showAgentMenu" class="menu" style="top: 32px; left: 0; min-width: 240px">
                <div class="menu-label">添加助手（圆桌）</div>
                <button v-for="a in availableToAdd" :key="a.id" class="menu-item" @click="chat.toggleAgent(a.id); showAgentMenu = false">
                  <Icon :name="a.icon" :style="{ color: a.color }" />
                  <span class="m-name">{{ a.label }}</span><span class="m-tag">{{ a.description }}</span>
                </button>
                <div v-if="!availableToAdd.length" class="menu-item"><span class="m-name" style="color:var(--ink-mute)">没有更多助手</span></div>
              </div>
            </div>
          </div>

          <!-- messages -->
          <template v-for="m in chat.messages" :key="m.id">
            <!-- roundtable -->
            <div v-if="m.role === 'roundtable'" class="roundtable">
              <div class="roundtable-label">圆桌 · {{ m.content.replies?.length || 0 }} 位助手并行作答</div>
              <div v-for="(r, idx) in m.content.replies" :key="idx" class="rt-card">
                <div class="rt-card-head">
                  <span class="rt-avatar" :style="{ background: agentDisplay(r.agent_id).color }"><Icon :name="agentDisplay(r.agent_id).icon" :size="11" /></span>
                  <span class="rt-name">{{ agentDisplay(r.agent_id).label }}</span>
                  <span class="rt-stance">— {{ agentDisplay(r.agent_id).description }}</span>
                </div>
                <div class="rt-card-body">
                  <span v-if="r.status === 'streaming' && !r.text" class="typing"><span></span><span></span><span></span></span>
                  <div v-else class="md-body" v-html="md(r.text)" />
                </div>
                <!-- vote buttons -->
                <div v-if="r.status !== 'streaming'" class="rt-vote">
                  <button :class="{ chosen: isChosen(m.id, idx) }" @click="toggleChosen(m.id, idx)">
                    <Icon name="check" :size="10" /> 采纳
                  </button>
                  <button @click="followUp(r.agent_id)">
                    <Icon name="chat" :size="10" /> 追问
                  </button>
                  <button @click="copyMessage(r.text)">
                    <Icon name="copy" :size="10" /> 转给我
                  </button>
                </div>
              </div>
              <div v-if="m.content.merged && (m.content.merged.text || m.content.merged.status !== 'pending')" class="rt-merge">
                <div class="rt-merge-head"><Icon name="sparkle" :size="12" /> Hermes 综合各方观点</div>
                <span v-if="m.content.merged.status === 'streaming' && !m.content.merged.text" class="typing"><span></span><span></span><span></span></span>
                <div v-else class="md-body" v-html="md(m.content.merged.text)" />
              </div>
            </div>

            <!-- normal message -->
            <div v-else class="msg" :class="m.role">
              <div v-if="m.role === 'agent'" class="msg-avatar" :style="{ background: agentById(m.agent_id || 'hermes')?.color || '#b8852a' }">
                <Icon :name="agentById(m.agent_id || 'hermes')?.icon || 'brand'" :size="14" />
              </div>
              <div class="msg-body">
                <div v-if="m.role === 'agent'" class="msg-name">
                  {{ agentById(m.agent_id || 'hermes')?.label || "Hermes" }}
                  <span v-if="agentById(m.agent_id || 'hermes')?.official" class="official">OFFICIAL</span>
                </div>
                <div class="msg-bubble">
                  <span v-if="m.status === 'streaming' && !m.content.text" class="typing"><span></span><span></span><span></span></span>
                  <div v-else-if="m.role === 'agent'" class="md-body" v-html="md(m.content.text)" />
                  <template v-else>
                    {{ m.content.text }}
                    <div v-if="m.content.files?.length" class="msg-files">
                      <button v-for="f in m.content.files" :key="f.id" class="msg-file-chip" @click="openFile(f.id)">
                        <Icon name="paperclip" :size="11" /> {{ f.name }}
                      </button>
                    </div>
                  </template>
                </div>
                <div v-if="m.role === 'agent' && m.status !== 'streaming'" class="msg-tools">
                  <button title="复制" @click="copyMessage(m.content.text)"><Icon name="copy" :size="12" /></button>
                  <button title="重新生成"><Icon name="refresh" :size="12" /></button>
                  <button title="点赞"><Icon name="thumbs_up" :size="12" /></button>
                  <button title="分享" @click="shareMessage(m.conversation_id)"><Icon name="share" :size="12" /></button>
                </div>
              </div>
              <div v-if="m.role === 'user'" class="msg-avatar"><Icon name="user" :size="14" /></div>
            </div>
          </template>
        </div>
      </div>

      <div class="dock">
        <Composer
          v-model="draft"
          placeholder="继续对话…"
          :agent="{ label: primaryAgent?.label, color: primaryAgent?.color, model: primaryAgent?.version || 'ACP' }"
          :streaming="chat.streaming"
          :conversation-id="chat.activeId || undefined"
          @send="onSend"
        />
      </div>

      <WorkspacePanel
        v-if="showWorkspace && chat.activeId && chat.files.length"
        :files="chat.files"
        :adapter="wsAdapter"
        :initial-file-id="openFileId || undefined"
        @close="showWorkspace = false; openFileId = null"
      />
    </div>
  </div>

  <ConfirmModal
    v-if="chat.pendingConfirmations.length"
    :request="chat.pendingConfirmations[0]"
    @close="chat.respondConfirmation(chat.pendingConfirmations[0].id, 'deny')"
    @respond="(choice) => chat.respondConfirmation(chat.pendingConfirmations[0].id, choice)"
  />
  <ExtractItemsModal
    v-if="showExtractModal && chat.activeId"
    :conversation-id="chat.activeId"
    :teams="chat.teams.map((t) => ({ id: t.id, name: t.name }))"
    @close="showExtractModal = false"
    @created="(pid) => { showExtractModal = false; router.push(`/projects/${pid}`); }"
  />
</template>
