<script setup lang="ts">
/* 1:1 port of the prototype chat (hermes-app.js landing + thread), main-content
   only — the sidebar/topbar live in AppLayout. Uses the prototype CSS classes
   so it renders pixel-identical; wired to the real chat store. */
import { computed, defineAsyncComponent, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useVirtualizer } from "@tanstack/vue-virtual";
import Icon from "@/components/Icon.vue";
import Composer from "@/components/Composer.vue";
import ConfirmModal from "@/components/ConfirmModal.vue";
import ConvoSeal from "@/components/ConvoSeal.vue";
import { useChatStore } from "@/stores/chat";
import { useAuthStore } from "@/stores/auth";
import { useBrandingStore } from "@/stores/branding";
import { useNotificationStore } from "@/stores/notifications";
import { conversationsApi } from "@/api/conversations";
import { teamsApi } from "@/api/teams";
import { projectsApi } from "@/api/projects";
import { renderMarkdown, renderMarkdownAsync } from "@/utils/markdown";
import { fmtNum } from "@/utils/format";
import type { GroupMember, Knowledge, Message, RoundtableReply, WsAdapter } from "@/types";
import type { SendOptions } from "@/components/Composer.vue";
import type { Profile } from "@/api/agents";

// Lazy-load heavy components (split from main bundle)
const WorkspacePanel = defineAsyncComponent(() => import("@/components/WorkspacePanel.vue"));
const ExtractItemsModal = defineAsyncComponent(() => import("@/components/ExtractItemsModal.vue"));
const MemberPanel = defineAsyncComponent(() => import("@/components/MemberPanel.vue"));

const chat = useChatStore();
const auth = useAuthStore();
const branding = useBrandingStore();
const ns = useNotificationStore();
const route = useRoute();
const router = useRouter();

/** HTML-escape a string for safe interpolation into v-html content. */
function escapeHtml(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

const draft = ref("");
const scroller = ref<HTMLElement | null>(null);
const loadMoreSentinel = ref<HTMLElement | null>(null);
const showWorkspace = ref(false);
const showMemberPanel = ref(false);
const showExtractModal = ref(false);
const landingProfileId = ref<string>("");
const teamKnowledge = ref<Knowledge[]>([]);
const projectTasks = ref<{ id: string; title: string; status: string }[]>([]);
const showProjectTasks = ref(false);
// roundtable per-reply chosen state (keyed by messageId:slot)
const chosenMap = ref<Record<string, boolean>>({});

onMounted(async () => {
  if (!chat.profiles.length) await chat.loadProfiles();
  // Request browser notification permission
  if ("Notification" in window && Notification.permission === "default") {
    Notification.requestPermission();
  }
  // Set landing profile from query param or first available
  const queryProfile = route.query.profile as string | undefined;
  if (queryProfile && chat.profiles.find((p) => p.id === queryProfile)) {
    landingProfileId.value = queryProfile;
  } else if (chat.profiles.length) {
    const firstProfile = chat.profiles.find((p) => p.is_active && p.default_agent_id);
    if (firstProfile) landingProfileId.value = firstProfile.id;
  }
  const cid = route.query.c as string | undefined;
  const teamCtx = route.query.team as string | undefined;
  const projCtx = route.query.project as string | undefined;
  const seed = route.query.seed as string | undefined;
  if (cid) {
    await chat.openConversation(cid);
    await scrollDown();
  } else if (teamCtx || projCtx) {
    const landingAgentId = landingProfile.value?.default_agent_id || "hermes";
    const d = await conversationsApi.create({ primary_agent_id: landingAgentId, profile_id: landingProfileId.value, team_id: teamCtx, project_id: projCtx, first_message: seed });
    await chat.loadConversations();
    await chat.openConversation(d.id);
    if (seed) draft.value = seed;
    await scrollDown();
  }
  // Observe load-more sentinel for infinite scroll
  setupLoadMoreObserver();
});

// ── Infinite scroll: load older messages when sentinel is visible ──
let observer: IntersectionObserver | null = null;
function setupLoadMoreObserver() {
  observer = new IntersectionObserver(
    async (entries) => {
      for (const entry of entries) {
        if (entry.isIntersecting && chat.hasMoreMessages && !chat.loadingOlder && chat.activeId) {
          const el = scroller.value;
          const prevHeight = el?.scrollHeight || 0;
          await chat.loadMoreMessages();
          // Preserve scroll position after prepending
          await nextTick();
          if (el) el.scrollTop = el.scrollHeight - prevHeight;
        }
      }
    },
    { root: scroller.value, threshold: 0.1 }
  );
  if (loadMoreSentinel.value) observer.observe(loadMoreSentinel.value);
}
watch(loadMoreSentinel, (el) => {
  if (el && observer) observer.observe(el);
});

// ── Greeting: time-aware + voice-aware ──
const greeting = computed(() => {
  const hour = new Date().getHours();
  const timePart = hour < 6 ? "夜深了" : hour < 11 ? "早上好" : hour < 14 ? "中午好" : hour < 18 ? "下午好" : "晚上好";
  let voice = "warm";
  try { voice = JSON.parse(localStorage.getItem("hermes.tweaks") || "{}").voice || "warm"; } catch { /* noop */ }
  if (voice === "classical") return { main: branding.tagline, sub: "Quidquid mittere vis, mihi crede." };
  if (voice === "engineering") return { main: `> <em>${branding.shortName}</em> ready —`, sub: `agents: ${chat.activeProfiles.length} active · model: ACP · uptime: 99.9%` };
  return { main: `${timePart}，<em>今天有什么安排？</em>`, sub: "Ask me anything · 我会调度合适的助手为你完成。" };
});

const landingProfile = computed(() => chat.profiles.find((p) => p.id === landingProfileId.value) || chat.profiles.find((p) => p.is_active) || null);
const activeConvo = computed(() => chat.conversations.find((c) => c.id === chat.activeId));
const primaryProfile = computed(() => {
  // Priority: conversation's profile_id > conversation's agent > landing
  const pid = activeConvo.value?.profile_id;
  if (pid) {
    const p = chat.profiles.find((pp) => pp.id === pid);
    if (p) return p;
  }
  const aid = activeConvo.value?.primary_agent_id;
  return aid ? chat.profiles.find((p) => p.default_agent_id === aid) || null : landingProfile.value;
});
const isGroup = computed(() => activeConvo.value?.type === "group");
const groupAgents = computed(() => {
  if (!isGroup.value || !activeConvo.value) return [];
  return (activeConvo.value.active_agent_ids || []).map((aid) => {
    const p = chat.profiles.find((pp) => pp.default_agent_id === aid);
    return {
      agent_id: aid,
      name: p?.name || aid,
      color: p?.color || branding.accent,
      icon: p?.icon || "sparkle",
    };
  });
});

const groupMembers = ref<GroupMember[]>([]);
watch(
  () => chat.activeId,
  async (id) => {
    if (id && isGroup.value) {
      try { groupMembers.value = await conversationsApi.getMembers(id); } catch { groupMembers.value = []; }
    } else {
      groupMembers.value = [];
    }
  },
  { immediate: true }
);

// Load team knowledge when the active conversation has a team_id
watch(
  () => activeConvo.value?.team_id,
  async (tid) => {
    if (tid) {
      try { teamKnowledge.value = await teamsApi.listKnowledge(tid); } catch { teamKnowledge.value = []; }
    } else {
      teamKnowledge.value = [];
    }
  },
  { immediate: true }
);

// Load project tasks when the active conversation has a project_id
watch(
  () => activeConvo.value?.project_id,
  async (pid) => {
    if (pid) {
      try { projectTasks.value = await projectsApi.tasks(pid); } catch { projectTasks.value = []; }
    } else {
      projectTasks.value = [];
    }
  },
  { immediate: true }
);

// ── Team / project context tags in thread meta ──
const convoTeamName = computed(() => {
  const tid = activeConvo.value?.team_id;
  return tid ? chat.teams.find((t) => t.id === tid)?.name : null;
});
const convoProjectName = computed(() => {
  return activeConvo.value?.project_name || null;
});
const convoProjectId = computed(() => {
  return activeConvo.value?.project_id || null;
});
const TASK_STATUS_ICON: Record<string, string> = { todo: "○", doing: "►", done: "✓" };

function profileByAgentId(agentId: string): Profile | undefined {
  // Prefer conversation's profile_id over agent_id lookup
  const convoProfileId = activeConvo.value?.profile_id;
  if (convoProfileId) {
    const p = chat.profiles.find((pp) => pp.id === convoProfileId);
    if (p) return p;
  }
  return chat.profiles.find((p) => p.default_agent_id === agentId);
}

// Display info from profile
function profileDisplay(profile: Profile | null | undefined): { label: string; icon: string; color: string; description: string } {
  return { label: profile?.name || branding.shortName, icon: profile?.icon || "sparkle", color: profile?.color || branding.accent, description: profile?.desc || "" };
}

function md(text: string) {
  return renderMarkdown(text);
}

// ── @mention highlighting for group chats ──
function highlightMentions(html: string): string {
  if (!isGroup.value) return html;
  return html.replace(/@([\w-]+)/g, (_match, agentId) => {
    const profile = chat.profiles.find(p => p.default_agent_id === agentId);
    const name = profile?.name || agentId;
    const color = profile?.color || branding.accent;
    return `<span class="mention-tag" style="background:${color}22;color:${color};border:1px solid ${color}44">@${escapeHtml(name)}</span>`;
  });
}

// ── Get user display info for group chat messages ──
function colorForId(id: string): string {
  let h = 0;
  for (let i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) % 360;
  return `hsl(${h}, 52%, 48%)`;
}
function getUserDisplay(msg: Message): { name: string; initials: string; color: string } {
  const meId = auth.user?.id;
  // owner_id null = local optimistic bubble = self; or owner matches me = self.
  if (!msg.owner_id || (meId && msg.owner_id === meId)) {
    return { name: '你', initials: '我', color: 'var(--accent)' };
  }
  const m = groupMembers.value.find((gm) => gm.user_id === msg.owner_id);
  const name = m?.user_name || '成员';
  return { name, initials: name.slice(0, 1).toUpperCase(), color: colorForId(msg.owner_id) };
}

// ── Group: reply / reactions / edit / recall / typing ──
const replyTarget = ref<{ id: string; label: string; snippet: string } | null>(null);
const REACTION_EMOJIS = ["👍", "❤️", "😄", "🎉", "👀", "🙏"];
const openEmojiFor = ref<string | null>(null);
function toggleEmojiPicker(msgId: string) {
  openEmojiFor.value = openEmojiFor.value === msgId ? null : msgId;
}
function closeEmojiPicker() { openEmojiFor.value = null; }

function setReply(msg: Message) {
  const label = msg.role === "user"
    ? getUserDisplay(msg).name
    : (chat.profiles.find((p) => p.default_agent_id === msg.agent_id)?.name || msg.agent_id || "AI");
  replyTarget.value = { id: msg.id, label, snippet: (msg.content?.text || "").slice(0, 60) };
}
function clearReply() { replyTarget.value = null; }

async function toggleReaction(msg: Message, emoji: string) {
  if (!chat.activeId || msg.id.startsWith("tmp-")) return;
  try {
    const updated = await conversationsApi.toggleReaction(chat.activeId, msg.id, emoji);
    msg.reactions = updated.reactions;
  } catch { ns.toast("操作失败"); }
}
function reactionEntries(msg: Message): { emoji: string; count: number; mine: boolean }[] {
  const r = msg.reactions || {};
  const meId = auth.user?.id || "";
  return Object.entries(r).map(([emoji, users]) => ({
    emoji, count: users.length, mine: users.includes(meId),
  }));
}

async function editMsg(msg: Message) {
  if (!chat.activeId) return;
  const next = window.prompt("编辑消息", msg.content?.text || "");
  if (next == null || next.trim() === "" || next === msg.content?.text) return;
  try {
    const updated = await conversationsApi.editMessage(chat.activeId, msg.id, next.trim());
    msg.content = { ...msg.content, ...updated.content };
    msg.edited_at = updated.edited_at;
  } catch { ns.toast("编辑失败"); }
}
async function recallMsg(msg: Message) {
  if (!chat.activeId || !window.confirm("撤回这条消息？")) return;
  try {
    const updated = await conversationsApi.recallMessage(chat.activeId, msg.id);
    msg.content = { ...msg.content, text: "" };
    msg.deleted_at = updated.deleted_at;
  } catch { ns.toast("撤回失败"); }
}
function canModify(msg: Message): boolean {
  return msg.role === "user" && !msg.deleted_at && !msg.id.startsWith("tmp-") &&
    (!msg.owner_id || msg.owner_id === auth.user?.id);
}

function onComposerTyping() {
  const me = auth.user?.name || auth.user?.email || "有人";
  chat.sendTyping(me);
}
const typingText = computed(() => {
  const names = chat.typingUsers
    .filter((u) => u.user_id !== auth.user?.id)
    .map((u) => u.name || "成员");
  if (!names.length) return "";
  if (names.length === 1) return `${names[0]} 正在输入…`;
  return `${names.slice(0, 2).join("、")} 等正在输入…`;
});

// Refetch members when the server signals a membership change.
function onMembersChanged(e: Event) {
  const cid = (e as CustomEvent).detail?.conversation_id;
  if (cid && cid === chat.activeId && isGroup.value) {
    conversationsApi.getMembers(cid).then((m) => (groupMembers.value = m)).catch(() => {});
  }
}
onMounted(() => window.addEventListener("hermes:members-changed", onMembersChanged));
onUnmounted(() => window.removeEventListener("hermes:members-changed", onMembersChanged));

// Post-process Mermaid blocks after DOM updates
watch(() => chat.messages.length, async () => {
  await nextTick();
  const blocks = document.querySelectorAll('.md-body pre code.language-mermaid');
  for (const block of blocks) {
    const pre = block.parentElement;
    if (!pre || pre.dataset.mermaidDone) continue;
    pre.dataset.mermaidDone = '1';
    const code = block.textContent || '';
    try {
      const html = await renderMarkdownAsync(`\`\`\`mermaid\n${code}\n\`\`\``);
      const wrapper = document.createElement('div');
      wrapper.innerHTML = html;
      pre.replaceWith(wrapper.firstElementChild!);
    } catch { /* leave as code block */ }
  }
});

async function scrollDown() {
  await nextTick();
  if (scroller.value) scroller.value.scrollTop = scroller.value.scrollHeight;
}
watch(() => chat.messages.length, () => nextTick(scrollDown));
watch(() => chat.activeId, scrollDown);

// ── Route query watcher: handle ?c= changes while already in ChatView ──
watch(() => route.query.c as string | undefined, async (cid) => {
  if (cid && cid !== chat.activeId) {
    await chat.openConversation(cid);
    await scrollDown();
  }
});

// ── Virtual scroll for message list ──
const virtualizerContainer = ref<HTMLElement | null>(null);

const virtualizer = useVirtualizer({
  count: chat.messages.length,
  getScrollElement: () => scroller.value,
  estimateSize: () => 120, // default estimate for a message
  overscan: 5,
  getItemKey: (index) => chat.messages[index]?.id ?? index,
});

// Update virtualizer count when messages change
watch(() => chat.messages.length, (newCount) => {
  virtualizer.value.options.count = newCount;
  // Auto-scroll to bottom for new messages
  nextTick(() => {
    virtualizer.value.scrollToIndex(newCount - 1, { align: "end" });
  });
});

// Measure actual element height for variable-height messages
function onMeasure(el: HTMLElement, _index: number) {
  if (el) {
    virtualizer.value.measureElement(el);
  }
}

// Re-measure virtual items when streaming message content grows.
// Without this, the virtualizer keeps the initially-measured height
// while the DOM grows, causing content to appear clipped / "not refreshing".
watch(
  () => chat.messages.length,
  () => {
    nextTick(() => virtualizer.value.measure());
  },
);

const openFileId = ref<string | null>(null);

function fmtTime(iso: string) {
  return new Date(iso).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
}

// Auto-reveal workspace when AI creates files during streaming
watch(() => chat.files.length, (newLen, oldLen) => {
  if (newLen > oldLen && chat.streaming) showWorkspace.value = true;
});

async function onSend(opts?: SendOptions) {
  let text = draft.value.trim();
  if (!text) return;
  if (chat.isActivelyStreaming(chat.activeId || "")) return;
  draft.value = "";
  if (opts?.stagedFiles?.length) showWorkspace.value = true;
  // Prepend knowledge content inline
  console.log('[onSend] knowledgeIds:', opts?.knowledgeIds, 'team_id:', activeConvo.value?.team_id, 'teamKnowledge:', teamKnowledge.value.length);
  if (opts?.knowledgeIds?.length && activeConvo.value?.team_id) {
    const tid = activeConvo.value.team_id;
    const blocks: string[] = [];
    for (const kid of opts.knowledgeIds) {
      try {
        const content = await teamsApi.knowledgeContent(tid, kid);
        const item = teamKnowledge.value.find((k) => k.id === kid);
        console.log('[onSend] knowledge', kid, 'content length:', content?.length, 'name:', item?.name);
        if (content) blocks.push(`【知识库: ${item?.name || kid}】\n${content}`);
      } catch (e) { console.error('[onSend] knowledge fetch failed:', kid, e); }
    }
    if (blocks.length) {
      // Wrap knowledge content in markers so it can be hidden in display but sent to AI
      const knowledgeBlock = blocks.map(b => `<knowledge>${b}</knowledge>`).join("\n\n");
      text = knowledgeBlock + "\n\n" + text;
      console.log('[onSend] final text length:', text.length);
    }
  }
  // File references are handled via attached_file_ids in the API payload.
  // We do NOT inline file content into the text — the backend resolves
  // attachments and tells the agent where they are in the workspace.
  // This avoids 422 (text too large) and keeps bubbles clean.
  await chat.send(text, landingProfile.value?.default_agent_id || "hermes", opts);
  clearReply();
  await scrollDown();
}

function openFile(fid: string) {
  openFileId.value = fid;
  showWorkspace.value = true;
}

// ── Message actions ──
async function copyMessage(text: string) {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
    } else {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.cssText = "position:fixed;left:-9999px;top:-9999px";
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      document.body.removeChild(ta);
    }
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
  // Find profile by agent ID and set as landing
  const profile = profileByAgentId(agentId);
  if (profile) landingProfileId.value = profile.id;
  (document.querySelector(".dock .composer-input") as HTMLTextAreaElement)?.focus();
}
async function startWithProfile(profile: Profile) {
  landingProfileId.value = profile.id;
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

// ── Context window ring ──
const ctxMax = computed(() => chat.contextSize > 0 ? chat.contextSize : 200_000);
const ctxPct = computed(() => Math.min(1, chat.contextTokens / ctxMax.value));
const ctxColor = computed(() => ctxPct.value > 0.85 ? "var(--danger)" : ctxPct.value > 0.65 ? "#e6a817" : "var(--ok)");
const ctxPctLabel = computed(() => `${Math.round(ctxPct.value * 100)}%`);
const ctxTooltip = computed(() =>
  chat.contextSize > 0
    ? `${fmtNum(chat.contextTokens)} / ${fmtNum(chat.contextSize)} tokens (${ctxPctLabel.value})`
    : `${fmtNum(chat.contextTokens)} tokens`
);

// ── Session controls ──
const forking = ref(false);
const sessionMode = ref<string>(activeConvo.value?.session_mode || "ask");
const SESSION_MODES = [
  { id: "ask", label: "Ask", desc: "编辑需确认" },
  { id: "accept_edits", label: "Accept", desc: "自动审批工作区" },
  { id: "dont_ask", label: "Auto", desc: "全自动" },
];

async function forkSession() {
  if (!chat.activeId || forking.value) return;
  forking.value = true;
  try {
    const d = await conversationsApi.forkSession(chat.activeId);
    await chat.loadConversations();
    await chat.openConversation(d.id);
  } catch (e: any) {
    console.error("Fork failed:", e);
  } finally {
    forking.value = false;
  }
}

async function changeSessionMode(mode: string) {
  if (!chat.activeId) return;
  const prev = sessionMode.value;
  sessionMode.value = mode;
  try {
    await conversationsApi.setSessionMode(chat.activeId, mode);
  } catch {
    sessionMode.value = prev;
    ns.toast("切换失败", "error");
  }
}


const ctxK = computed(() => chat.contextTokens >= 1000 ? `${(chat.contextTokens / 1000).toFixed(0)}k` : `${chat.contextTokens}`);

// ── Session export ──
const showExport = ref(false);
const editingTitle = ref(false);
const titleDraft = ref("");

function startEditTitle() {
  titleDraft.value = activeConvo.value?.title || "";
  editingTitle.value = true;
  nextTick(() => {
    const input = document.querySelector(".thread-title-edit") as HTMLInputElement;
    if (input) { input.focus(); input.select(); }
  });
}
async function saveTitle() {
  editingTitle.value = false;
  const v = titleDraft.value.trim();
  if (!v || !chat.activeId || v === activeConvo.value?.title) return;
  await conversationsApi.update(chat.activeId, { title: v });
  await chat.loadConversations();
}
function download(blob: Blob, name: string) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
  URL.revokeObjectURL(a.href);
}
function exportMd() {
  const title = activeConvo.value?.title || "conversation";
  const md_content = chat.messages.map((m) => {
    const who = m.role === "user" ? "**用户**" : `**${primaryProfile.value?.name || branding.shortName}**`;
    return `${who}\n\n${m.content.text || ""}`;
  }).join("\n\n---\n\n");
  download(new Blob([md_content], { type: "text/markdown" }), `${title}.md`);
  showExport.value = false;
}
function exportJson() {
  const title = activeConvo.value?.title || "conversation";
  download(new Blob([JSON.stringify(chat.messages, null, 2)], { type: "application/json" }), `${title}.json`);
  showExport.value = false;
}

// ── Conversation fork ──
async function forkFrom(msgId: string) {
  if (!chat.activeId) return;
  try {
    const forked = await conversationsApi.fork(chat.activeId, msgId);
    await chat.loadConversations();
    // 直接打开新会话，不依赖路由（ChatView 已 mounted，query 变化不会触发 onMounted）
    await chat.openConversation(forked.id);
    await scrollDown();
    nextTick(() => {
      (document.querySelector(".dock .composer-input") as HTMLTextAreaElement)?.focus();
    });
  } catch {
    ns.toast("分叉失败", "error");
  }
}

// ── File diff colorizer ──
function colorDiff(text: string): string {
  return text.split("\n").map((line) => {
    const esc = escapeHtml(line);
    if (line.startsWith("+") && !line.startsWith("+++")) return `<span class="diff-add">${esc}</span>`;
    if (line.startsWith("-") && !line.startsWith("---")) return `<span class="diff-del">${esc}</span>`;
    return `<span>${esc}</span>`;
  }).join("\n");
}

// ── Smart follow-up suggestion chips ──
function getFollowupChips(text: string): string[] {
  if (/```[\s\S]*```/.test(text))
    return ["解释这段代码", "如何测试这段代码？", "有没有优化空间？"];
  if (/^\s*\d+\.|^[-*]\s/m.test(text))
    return ["展开第一点，详细说明", "比较这几个方案的优劣", "哪个方案最推荐，为什么？"];
  if (/error|错误|fail|失败|exception|bug/i.test(text))
    return ["如何具体修复这个问题？", "能给一个完整的修复示例吗？", "根本原因是什么？"];
  if (/步骤|第.{1,3}步|how to|如何|怎么/i.test(text))
    return ["从第一步开始，逐步引导我", "有没有更简单的方法？", "给出完整的可运行代码"];
  return ["继续深入分析", "给一个具体的实际例子", "用要点列表总结一下"];
}

async function sendFollowup(text: string) {
  draft.value = text;
  await onSend();
}

async function onChannelModeChange(mode: string) {
  if (!chat.activeId) return;
  try {
    await conversationsApi.update(chat.activeId, { channel_mode: mode });
    // Update local state
    const idx = chat.conversations.findIndex((c) => c.id === chat.activeId);
    if (idx !== -1) chat.conversations[idx].channel_mode = mode;
  } catch (e) {
    console.error('[onChannelModeChange] failed:', e);
  }
}

// ── Knowledge reference display filter ──
function extractKnowledgeRefs(text: string): string[] {
  const refs: string[] = [];
  const regex = /<knowledge>[\s\S]*?【知识库:\s*([^】]+)】[\s\S]*?<\/knowledge>/g;
  let m;
  while ((m = regex.exec(text)) !== null) {
    refs.push(m[1].trim());
  }
  return refs;
}
function displayText(text: string): string {
  // Remove <knowledge>...</knowledge> blocks, keep the rest
  return text.replace(/<knowledge>[\s\S]*?<\/knowledge>\s*/g, "").trim();
}
function displayHtml(text: string): string {
  // Strip knowledge blocks then render markdown (handles <quote> etc.)
  let stripped = displayText(text);
  // Convert <quote> tags to markdown blockquotes for rendering
  stripped = stripped.replace(/<quote(?:\s+summary="([^"]*)")?>\s*\n?([\s\S]*?)\n?\s*<\/quote>/g,
    (_m, summary, content) => {
      const lines = (content || "").trim().split("\n");
      const quoted = lines.map((l: string) => `> ${l}`).join("\n");
      return summary ? `> **${summary}**\n${quoted}` : quoted;
    });
  return renderMarkdown(stripped);
}

// ── Agent working phase display ──
function getAgentPhase(msg: Message): string | null {
  if (msg.status !== "streaming") return null;
  const hasText = (msg.content.text?.length ?? 0) > 0;
  if (hasText) return null;
  const runningStep = (msg.steps || []).find(s => s.status === "running" || s.status === "started");
  if (runningStep) return `🔧 ${runningStep.title}`;
  if (msg.thinking && !hasText) return "💭 推理中…";
  return "🔍 分析问题…";
}

// ── Regenerate ──
async function regenerate(agentMsgId: string) {
  const agentIdx = chat.messages.findIndex(m => m.id === agentMsgId);
  if (agentIdx <= 0) return;
  const userMsg = [...chat.messages].slice(0, agentIdx).reverse().find(m => m.role === "user");
  if (!userMsg || !userMsg.content.text) return;
  draft.value = userMsg.content.text;
  await onSend();
}

// ── Roundtable progress ──
function rtProgress(replies: RoundtableReply[]): number[] {
  const lengths = replies.map(r => r.text.length);
  const max = Math.max(...lengths, 1);
  return lengths.map(l => Math.min(100, Math.round((l / max) * 100)));
}

// ── Composer command handler ──
function handleComposerCommand(cmd: string) {
  if (cmd === "new") {
    router.push("/");
    draft.value = "";
  } else if (cmd === "export") {
    showExport.value = true;
  } else if (cmd === "clear") {
    draft.value = "";
  }
}

// ── Global keyboard shortcut (⌘F → search) ──
const showSearch = ref(false);
const searchQuery = ref("");
const searchMatchIndex = ref(0);

const searchMatchIndices = computed(() => {
  if (!searchQuery.value.trim()) return [];
  const q = searchQuery.value.toLowerCase();
  return chat.messages
    .map((m, i) => ({ i, text: (m.content.text || "").toLowerCase() }))
    .filter(({ text }) => text.includes(q))
    .map(({ i }) => i);
});

function mdSearch(text: string): string {
  const html = md(text);
  if (!searchQuery.value.trim()) return html;
  const q = searchQuery.value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return html.replace(new RegExp(`(${q})`, "gi"), (m) => `<mark>${escapeHtml(m)}</mark>`);
}

function searchNext() {
  if (!searchMatchIndices.value.length) return;
  searchMatchIndex.value = (searchMatchIndex.value + 1) % searchMatchIndices.value.length;
  virtualizer.value.scrollToIndex(searchMatchIndices.value[searchMatchIndex.value], { align: "center" });
}
function searchPrev() {
  if (!searchMatchIndices.value.length) return;
  searchMatchIndex.value = (searchMatchIndex.value - 1 + searchMatchIndices.value.length) % searchMatchIndices.value.length;
  virtualizer.value.scrollToIndex(searchMatchIndices.value[searchMatchIndex.value], { align: "center" });
}

function onGlobalKey(e: KeyboardEvent) {
  if ((e.metaKey || e.ctrlKey) && e.key === "f" && chat.activeId) {
    e.preventDefault();
    showSearch.value = true;
    nextTick(() => (document.querySelector(".msg-search-input") as HTMLInputElement)?.focus());
  }
  if (e.key === "Escape" && showSearch.value) { showSearch.value = false; searchQuery.value = ""; }
}
onMounted(() => window.addEventListener("keydown", onGlobalKey));
onUnmounted(() => window.removeEventListener("keydown", onGlobalKey));
</script>

<template>
  <div class="stage">
    <!-- LANDING -->
    <div v-if="!chat.activeId" class="landing">
      <div class="landing-inner">
        <h1 class="hello" v-html="greeting.main"></h1>
        <div class="hello-sub">{{ greeting.sub }}</div>

        <!-- All profiles for selection -->
        <div class="featured-profiles">
          <button
            v-for="p in chat.profiles.filter(p => p.is_active)"
            :key="p.id"
            class="featured-card"
            :class="{ active: landingProfileId === p.id }"
            @click="startWithProfile(p)"
          >
            <div class="featured-card-icon" :style="{ background: p.color || branding.accent }">
              <Icon :name="p.icon || 'sparkle'" :size="16" />
            </div>
            <div class="featured-card-body">
              <div class="featured-card-name">{{ p.name }}</div>
              <div class="featured-card-desc">{{ p.desc || p.skills?.join(' · ') || p.default_model }}</div>
            </div>
          </button>
        </div>

        <Composer
          v-model="draft"
          :placeholder="`给 ${landingProfile?.name || branding.shortName} 发消息…  ⌘K 搜索 · Enter 发送`"
          :agent="{ label: landingProfile?.name, color: landingProfile?.color, model: landingProfile?.default_model || 'ACP' }"
          :profile-id="landingProfileId"
          :profile-locked="true"
          :streaming="chat.isActivelyStreaming(chat.activeId || '')"
          :knowledge-items="teamKnowledge.length ? teamKnowledge : undefined"
          @send="onSend"
          @cancel="chat.cancel()"
        />

      </div>
    </div>

    <!-- THREAD -->
    <div v-else class="thread-split" :class="{ 'ws-closed': !showWorkspace }">
      <!-- Fixed header: title + action buttons (outside scroll container) -->
      <div class="thread-head-wrap">
        <div class="thread-inner">
          <div class="thread-head" style="display:flex;align-items:flex-start;justify-content:space-between;gap:14px;flex-wrap:wrap;row-gap:8px;">
            <div style="flex:1;min-width:0;display:flex;align-items:flex-start;gap:10px;">
              <ConvoSeal v-if="chat.activeId" :seed="chat.activeId" :size="40" style="margin-top:2px;" />
              <div style="min-width:0;">
                <template v-if="editingTitle">
                  <input
                    class="thread-title-edit"
                    v-model="titleDraft"
                    @keydown.enter="saveTitle"
                    @keydown.escape="editingTitle = false"
                    @blur="saveTitle"
                  />
                </template>
                <h2 v-else class="thread-title" @click="startEditTitle" title="点击编辑标题">{{ activeConvo?.title || "对话" }}</h2>
                <div class="thread-meta" style="margin-top:5px;">
                  <span class="agent-tag"><Icon :name="primaryProfile?.icon || 'brand'" :size="10" /> {{ primaryProfile?.name || branding.shortName }}</span>
                  <span v-if="chat.activeProfiles.length > 1" class="agent-tag" style="background:rgba(184,133,42,0.14);color:var(--accent-deep);">
                    <Icon name="sparkle" :size="10" /> 圆桌 · {{ chat.activeProfiles.length }} 位并行
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
            <div v-if="chat.contextTokens > 0" class="ctx-ring-wrap" :title="ctxTooltip">
              <svg class="ctx-ring" viewBox="0 0 32 32">
                <circle cx="16" cy="16" r="12" fill="none" stroke="var(--rule)" stroke-width="3"/>
                <circle cx="16" cy="16" r="12" fill="none" :stroke="ctxColor" stroke-width="3"
                  stroke-linecap="round" :stroke-dasharray="`${ctxPct * 75.4} 75.4`"
                  stroke-dashoffset="18.85" transform="rotate(-90 16 16)"/>
              </svg>
              <span class="ctx-label">{{ ctxK }}</span>
            </div>
            <button class="thread-action text-mute-sm" v-if="chat.messages.length" @click="showSearch = !showSearch" style="flex-shrink:0;margin-top:2px" title="搜索消息 ⌘F">
              <Icon name="search" />
            </button>
            <button class="thread-action text-mute-sm" v-if="chat.files.length" @click="showWorkspace = !showWorkspace" style="flex-shrink:0;margin-top:2px">
              <Icon name="folder" /> 工作区 ({{ chat.files.length }})
            </button>
            <button class="thread-action text-mute-sm" v-if="isGroup" @click="showMemberPanel = !showMemberPanel" :style="{ flexShrink: '0', marginTop: '2px', color: showMemberPanel ? 'var(--accent)' : undefined }">
              <Icon name="users" /> 成员
            </button>
            <button class="thread-action text-mute-sm" v-if="chat.messages.length >= 2" @click="showExtractModal = true" style="flex-shrink:0;margin-top:2px" title="从对话内容自动创建项目与任务">
              <Icon name="sparkle" /> 智能创建
            </button>
            <!-- Fork session -->
            <button class="thread-action text-mute-sm" v-if="chat.activeId && activeConvo?.acp_session_id" @click="forkSession" :disabled="forking" style="flex-shrink:0;margin-top:2px" title="Fork ACP session (分支历史)">
              <Icon name="copy" /> {{ forking ? 'Forking…' : 'Fork' }}
            </button>
            <!-- Edit approval mode -->
            <div v-if="chat.activeId && activeConvo?.acp_session_id" class="mode-toggle text-mute-sm" style="flex-shrink:0;margin-top:2px">
              <button v-for="m in SESSION_MODES" :key="m.id" class="mode-btn" :class="{ active: sessionMode === m.id }" :title="m.desc" @click="changeSessionMode(m.id)">
                {{ m.label }}
              </button>
            </div>

            <div v-if="chat.messages.length >= 2" style="position:relative;flex-shrink:0;margin-top:2px;">
              <button class="thread-action" @click="showExport = !showExport"><Icon name="download" /> 导出</button>
              <div v-if="showExport" class="export-menu" @mouseleave="showExport = false">
                <button class="menu-item" @click="exportMd()">Markdown</button>
                <button class="menu-item" @click="exportJson()">JSON</button>
              </div>
            </div>
          </div>

          <!-- Message search bar -->
          <div v-if="showSearch" class="msg-search-bar">
            <Icon name="search" :size="13" />
            <input
              class="msg-search-input"
              v-model="searchQuery"
              placeholder="搜索消息… (Enter 下一个)"
              @keydown.enter="searchNext"
              @keydown.escape="showSearch = false; searchQuery = ''"
              autofocus
            />
            <span class="search-count" v-if="searchQuery">
              {{ searchMatchIndices.length ? `${searchMatchIndex + 1} / ${searchMatchIndices.length}` : "无结果" }}
            </span>
            <button class="search-nav" @click="searchPrev" title="上一个"><Icon name="chevron_up" :size="11" /></button>
            <button class="search-nav" @click="searchNext" title="下一个"><Icon name="chevron_down" :size="11" /></button>
            <button class="search-close" @click="showSearch = false; searchQuery = ''"><Icon name="close" :size="11" /></button>
          </div>

          <!-- Project tasks panel -->
          <div v-if="convoProjectId && projectTasks.length" class="project-tasks-panel">
            <button class="project-tasks-toggle" @click="showProjectTasks = !showProjectTasks">
              <Icon name="check" :size="12" /> 项目任务 · {{ projectTasks.filter(t => t.status === 'done').length }}/{{ projectTasks.length }}
              <Icon :name="showProjectTasks ? 'chevron_up' : 'chevron_down'" :size="11" />
            </button>
            <div v-if="showProjectTasks" class="project-tasks-list">
              <div v-for="t in projectTasks" :key="t.id" class="ptask-row" :class="t.status">
                <span class="ptask-icon" :class="t.status">{{ TASK_STATUS_ICON[t.status] || '○' }}</span>
                <span class="ptask-title" :class="{ done: t.status === 'done' }">{{ t.title }}</span>
                <span class="ptask-status">{{ { todo: '待办', doing: '进行中', done: '已完成' }[t.status] || t.status }}</span>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- Scrollable message area -->
      <div ref="scroller" class="thread">
        <div class="thread-inner">
          <!-- messages (virtual scroll) -->
          <div v-if="chat.hasMoreMessages || chat.loadingOlder" ref="loadMoreSentinel" class="load-more-sentinel">
            <span v-if="chat.loadingOlder" class="loading-spinner"></span>
            <span v-else class="load-more-hint">↑ 上滑加载更多消息</span>
          </div>
          <div
            ref="virtualizerContainer"
            :style="{ height: virtualizer.getTotalSize() + 'px', width: '100%', position: 'relative' }"
          >
            <div
              v-for="row in virtualizer.getVirtualItems()"
              :key="String(row.key)"
              :ref="(el) => onMeasure(el as HTMLElement, row.index)"
              :data-index="row.index"
              :style="{ position: 'absolute', top: 0, left: 0, width: '100%', transform: `translateY(${row.start}px)` }"
            >
              <template v-if="chat.messages[row.index]">
                <!-- roundtable -->
                <div v-if="chat.messages[row.index].role === 'roundtable'" class="roundtable">
              <div class="roundtable-label">圆桌 · {{ chat.messages[row.index].content.replies?.length || 0 }} 位助手并行作答</div>
              <div v-for="(r, idx) in chat.messages[row.index].content.replies" :key="idx" class="rt-card">
                <div class="rt-card-head">
                  <span class="rt-avatar" :style="{ background: profileDisplay(profileByAgentId(r.agent_id)).color }"><Icon :name="profileDisplay(profileByAgentId(r.agent_id)).icon" :size="11" /></span>
                  <span class="rt-name">{{ profileDisplay(profileByAgentId(r.agent_id)).label }}</span>
                  <span class="rt-stance">— {{ profileDisplay(profileByAgentId(r.agent_id)).description }}</span>
                  <span class="rt-status" :class="r.status">{{ r.status === 'streaming' ? '生成中' : r.status === 'error' ? '作答失败' : r.status === 'timeout' ? '超时' : '完成' }}</span>
                </div>
                <div v-if="chat.messages[row.index].status === 'streaming'" class="rt-progress-wrap">
                  <div class="rt-progress-bar" :style="{ width: rtProgress(chat.messages[row.index].content.replies || [])[idx] + '%' }" :class="{ done: r.status === 'complete' }"></div>
                </div>
                <div class="rt-card-body">
                  <span v-if="r.status === 'streaming' && !r.text" class="typing"><span></span><span></span><span></span></span>
                  <div v-else class="md-body" v-html="md(r.text)" />
                </div>
                <!-- vote buttons -->
                <div v-if="r.status !== 'streaming'" class="rt-vote">
                  <button :class="{ chosen: isChosen(chat.messages[row.index].id, idx) }" @click="toggleChosen(chat.messages[row.index].id, idx)">
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
              <div v-if="chat.messages[row.index]?.content?.merged && (chat.messages[row.index]?.content?.merged?.text || chat.messages[row.index]?.content?.merged?.status !== 'pending')" class="rt-merge">
                <div class="rt-merge-head"><Icon name="sparkle" :size="12" /> {{ branding.shortName }} 综合各方观点</div>
                <span v-if="chat.messages[row.index].content.merged?.status === 'streaming' && !chat.messages[row.index].content.merged?.text" class="typing"><span></span><span></span><span></span></span>
                <div v-else class="md-body" v-html="md(chat.messages[row.index].content.merged?.text || '')" />
              </div>
            </div>

            <!-- normal message -->
            <div v-else-if="chat.messages[row.index].role === 'system'" class="msg system-msg">
              <div class="system-msg-body">
                <Icon name="info" :size="12" />
                <span>{{ chat.messages[row.index].content.text }}</span>
              </div>
            </div>
            <div v-else class="msg" :class="chat.messages[row.index].role">
              <div v-if="chat.messages[row.index].role === 'agent'" class="msg-avatar" :style="{ background: profileDisplay(profileByAgentId(chat.messages[row.index].agent_id || 'hermes')).color }">
                <Icon :name="profileDisplay(profileByAgentId(chat.messages[row.index].agent_id || 'hermes')).icon" :size="14" />
              </div>
              <div v-else-if="isGroup && chat.messages[row.index].role === 'user'" class="msg-avatar user-avatar-group" :style="{ background: getUserDisplay(chat.messages[row.index]).color }">
                <span class="avatar-initials">{{ getUserDisplay(chat.messages[row.index]).initials }}</span>
              </div>
              <div class="msg-body">
                <div v-if="chat.messages[row.index].role === 'agent'" class="msg-name">
                  {{ profileDisplay(profileByAgentId(chat.messages[row.index].agent_id || 'hermes')).label }}
                </div>
                <div v-else-if="isGroup && chat.messages[row.index].role === 'user'" class="msg-name user-name">
                  {{ getUserDisplay(chat.messages[row.index]).name }}
                </div>
                <details v-if="chat.messages[row.index].thinking" class="msg-think" :open="chat.messages[row.index].status === 'streaming'">
                  <summary class="think-summary">
                    <span v-if="chat.messages[row.index].status === 'streaming'" class="think-pulse"></span>
                    💭 思考过程
                    <span v-if="chat.messages[row.index].status !== 'streaming'" class="think-chars">{{ (chat.messages[row.index].thinking || '').length }} 字</span>
                  </summary>
                  <div class="think-body">{{ chat.messages[row.index].thinking }}</div>
                </details>
                <div v-if="chat.messages[row.index].plan?.length" class="msg-plan">
                  <div class="plan-title">📋 执行计划</div>
                  <div v-for="(e, i) in chat.messages[row.index].plan" :key="i" class="plan-item" :class="e.status">
                    <span class="plan-icon">{{ e.status === 'completed' ? '✓' : e.status === 'in_progress' ? '►' : '○' }}</span>
                    <span class="plan-text">{{ e.content }}</span>
                  </div>
                </div>
                <details v-if="chat.messages[row.index].steps?.length" class="msg-steps" style="margin-bottom:6px">
                  <summary style="font-size:11.5px;color:var(--ink-mute);cursor:pointer;list-style:none">
                    <Icon name="bolt" :size="11" /> 执行了 {{ chat.messages[row.index].steps!.length }} 步
                  </summary>
                  <div v-for="(s, i) in chat.messages[row.index].steps" :key="i" class="step-item">
                    <span class="step-dot" :class="s.status"></span>{{ s.title }}
                  </div>
                </details>
                <div class="msg-bubble">
                  <template v-if="chat.messages[row.index].status === 'streaming' && !chat.messages[row.index].content.text">
                    <div v-if="getAgentPhase(chat.messages[row.index])" class="agent-phase">
                      <span class="phase-dot"></span>
                      <span class="phase-text">{{ getAgentPhase(chat.messages[row.index]) }}</span>
                    </div>
                    <span v-else class="typing"><span></span><span></span><span></span></span>
                  </template>
                  <div v-else-if="chat.messages[row.index].role === 'agent'" class="md-body" v-html="highlightMentions(mdSearch(chat.messages[row.index].content.text))" />
                  <template v-else>
                    <div v-if="isGroup && chat.messages[row.index].reply_to" class="reply-quote">
                      <Icon name="corner-up-left" :size="11" />
                      <span class="rq-label">{{ chat.messages[row.index].reply_to!.snippet }}</span>
                    </div>
                    <div v-if="chat.messages[row.index].deleted_at" class="recalled-msg">该消息已撤回</div>
                    <div v-else-if="displayText(chat.messages[row.index].content.text)" class="md-body" v-html="highlightMentions(displayHtml(chat.messages[row.index].content.text))"></div>
                    <div v-if="displayText(chat.messages[row.index].content.text) && extractKnowledgeRefs(chat.messages[row.index].content.text).length" class="knowledge-refs-badge">
                      <Icon name="doc" :size="11" /> 引用了知识库: {{ extractKnowledgeRefs(chat.messages[row.index].content.text).join(', ') }}
                    </div>
                    <div v-if="!displayText(chat.messages[row.index].content.text) && extractKnowledgeRefs(chat.messages[row.index].content.text).length" class="knowledge-refs-badge standalone">
                      <Icon name="doc" :size="11" /> 已发送知识库: {{ extractKnowledgeRefs(chat.messages[row.index].content.text).join(', ') }}
                    </div>
                    <div v-if="chat.messages[row.index].content.files?.length" class="msg-files">
                      <button v-for="f in chat.messages[row.index].content.files" :key="f.id" class="msg-file-chip" @click="openFile(f.id)">
                        <Icon name="paperclip" :size="11" /> {{ f.name }}
                      </button>
                    </div>
                  </template>
                </div>
                <div v-if="chat.messages[row.index].role === 'agent' && chat.messages[row.index].status === 'cancelled'" class="msg-cancelled-tag">
                  <Icon name="stop" :size="11" /> 已停止生成
                </div>
                <!-- group: reactions + edited marker + hover actions -->
                <template v-if="isGroup && !chat.messages[row.index].deleted_at">
                  <div v-if="reactionEntries(chat.messages[row.index]).length || chat.messages[row.index].edited_at" class="msg-reactions">
                    <button
                      v-for="r in reactionEntries(chat.messages[row.index])"
                      :key="r.emoji"
                      class="reaction-pill"
                      :class="{ mine: r.mine }"
                      @click="toggleReaction(chat.messages[row.index], r.emoji)"
                    >{{ r.emoji }} {{ r.count }}</button>
                    <span v-if="chat.messages[row.index].edited_at" class="edited-tag">(已编辑)</span>
                  </div>
                  <div v-if="!chat.messages[row.index].id.startsWith('tmp-')" class="group-actions">
                    <button title="回复" @click="setReply(chat.messages[row.index])"><Icon name="quote" :size="12" /></button>
                    <div class="react-wrap" @mouseleave="closeEmojiPicker()">
                      <button title="表情" @click.stop="toggleEmojiPicker(chat.messages[row.index].id)"><Icon name="thumbs_up" :size="12" /></button>
                      <div v-if="openEmojiFor === chat.messages[row.index].id" class="react-pop">
                        <button v-for="e in REACTION_EMOJIS" :key="e" @click="toggleReaction(chat.messages[row.index], e)">{{ e }}</button>
                      </div>
                    </div>
                    <button v-if="canModify(chat.messages[row.index])" title="编辑" @click="editMsg(chat.messages[row.index])"><Icon name="edit" :size="12" /></button>
                    <button v-if="canModify(chat.messages[row.index])" title="撤回" @click="recallMsg(chat.messages[row.index])"><Icon name="trash" :size="12" /></button>
                  </div>
                </template>
                <div v-if="chat.messages[row.index].role === 'agent' && chat.messages[row.index].content.files?.length" class="msg-files" style="margin-top:6px">
                  <div v-for="f in chat.messages[row.index].content.files" :key="f.id" class="msg-file-chip-wrap">
                    <button class="msg-file-chip" @click="openFile(f.id)">
                      <Icon name="paperclip" :size="11" /> {{ f.name }}
                    </button>
                    <details v-if="f.diff" class="file-diff">
                      <summary style="font-size:10.5px;cursor:pointer;color:var(--ink-mute)">查看改动</summary>
                      <pre class="diff-body" v-html="colorDiff(f.diff)"></pre>
                    </details>
                  </div>
                </div>
                <div v-if="chat.messages[row.index].role === 'agent' && chat.messages[row.index].status !== 'streaming'" class="msg-tools">
                  <button title="复制" @click="copyMessage(chat.messages[row.index].content.text)"><Icon name="copy" :size="12" /></button>
                  <button title="重新生成" @click="regenerate(chat.messages[row.index].id)"><Icon name="refresh" :size="12" /></button>

                  <button title="分享" @click="shareMessage(chat.messages[row.index].conversation_id)"><Icon name="share" :size="12" /></button>
                </div>
                <!-- Smart follow-up suggestion chips -->
                <div v-if="chat.features.followup_chips && chat.messages[row.index].role === 'agent' && chat.messages[row.index].status !== 'streaming' && chat.messages[row.index].content.text" class="followup-chips">
                  <button v-for="chip in getFollowupChips(chat.messages[row.index].content.text)" :key="chip" class="followup-chip" @click="sendFollowup(chip)">{{ chip }}</button>
                </div>
                <div class="msg-time">{{ fmtTime(chat.messages[row.index].created_at) }}</div>
                <div v-if="chat.messages[row.index].role === 'user'" class="msg-fork-btn" @click="forkFrom(chat.messages[row.index].id)" title="从这里分叉对话">
                  ⎇ 分叉
                </div>
              </div>
              <div v-if="chat.messages[row.index].role === 'user'" class="msg-avatar"><Icon name="user" :size="14" /></div>
            </div>
              </template>
            </div>
          </div>
        </div>
      </div>

      <div class="dock">
        <div v-if="isGroup && typingText" class="typing-indicator">{{ typingText }}</div>
        <Composer
          v-model="draft"
          placeholder="继续对话…"
          :agent="{ label: primaryProfile?.name, color: primaryProfile?.color, model: primaryProfile?.default_model || 'ACP' }"
          :profile-id="primaryProfile?.id"
          :profile-locked="true"
          :streaming="chat.isActivelyStreaming(chat.activeId || '')"
          :conversation-id="chat.activeId || undefined"
          :knowledge-items="teamKnowledge.length ? teamKnowledge : undefined"
          :is-group="isGroup"
          :group-agents="groupAgents"
          :group-members="groupMembers"
          :reply-to="replyTarget"
          @send="onSend"
          @typing="onComposerTyping"
          @cancel-reply="clearReply"
          @cancel="chat.cancel()"
          @command="handleComposerCommand"
        />
      </div>

      <WorkspacePanel
        v-if="showWorkspace && chat.activeId && chat.files.length"
        :files="chat.files"
        :adapter="wsAdapter"
        :initial-file-id="openFileId || undefined"
        @close="showWorkspace = false; openFileId = null"
      />

      <MemberPanel
        v-if="showMemberPanel && isGroup && chat.activeId"
        :conversation-id="chat.activeId"
        :agents="groupAgents"
        :channel-mode="activeConvo?.channel_mode || 'mention'"
        @close="showMemberPanel = false"
        @update:channel-mode="onChannelModeChange"
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

<style scoped>
/* ── Group chat: reply quote / reactions / actions / typing ── */
.reply-quote {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 11.5px;
  color: var(--ink-mute);
  padding: 3px 8px;
  margin-bottom: 4px;
  border-left: 2px solid var(--accent);
  background: var(--accent-tint, rgba(184, 133, 42, 0.1));
  border-radius: 4px;
}
.reply-quote .rq-label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 320px;
}
.recalled-msg {
  font-size: 12.5px;
  font-style: italic;
  color: var(--ink-faint);
}
.msg-reactions {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-wrap: wrap;
  margin-top: 4px;
}
.reaction-pill {
  border: 1px solid var(--border);
  background: var(--surface);
  border-radius: 11px;
  padding: 1px 7px;
  font-size: 12px;
  cursor: pointer;
  line-height: 1.5;
}
.reaction-pill.mine {
  border-color: var(--accent);
  background: var(--accent-tint, rgba(184, 133, 42, 0.14));
}
.edited-tag { font-size: 11px; color: var(--ink-faint); margin-left: 2px; }
.msg-cancelled-tag {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  margin-top: 5px;
  font-size: 11.5px;
  color: var(--ink-faint);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 1px 7px;
}
.group-actions {
  display: flex;
  align-items: center;
  gap: 2px;
  margin-top: 3px;
  opacity: 0;
  transition: opacity 120ms;
}
.msg:hover .group-actions { opacity: 1; }
.group-actions button {
  border: none;
  background: transparent;
  cursor: pointer;
  color: var(--ink-mute);
  padding: 3px;
  border-radius: 5px;
  display: inline-flex;
}
.group-actions button:hover { background: var(--accent-tint); color: var(--accent); }
.react-wrap { position: relative; display: inline-flex; }
.react-pop {
  position: absolute;
  bottom: 120%;
  left: 0;
  display: none;
  gap: 2px;
  padding: 4px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  box-shadow: var(--shadow-md);
  z-index: 20;
}
.react-pop { display: flex; }
.react-pop button { font-size: 15px; padding: 2px 4px; border: none; background: transparent; cursor: pointer; border-radius: 5px; }
.react-pop button:hover { background: var(--accent-tint); }
.typing-indicator {
  font-size: 11.5px;
  color: var(--ink-mute);
  padding: 2px 10px 4px;
  font-style: italic;
}
</style>
