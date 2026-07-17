<script setup lang="ts">
/* 1:1 port of the prototype composer (hermes-app.js Composer): rich toolbar +
   profile dropdown (ACP) + circular send button. */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import Icon from "@/components/Icon.vue";
import ProfileListItem from "@/components/ProfileListItem.vue";
import { profilesApi, type Profile } from "@/api/agents";
import { filesApi, type FileItem } from "@/api/files";
import { useNotificationStore } from "@/stores/notifications";
import { useBrandingStore } from "@/stores/branding";

const ns = useNotificationStore();
const branding = useBrandingStore();

const props = defineProps<{
  modelValue: string;
  placeholder?: string;
  agent?: { label?: string; color?: string | null; model?: string } | null;
  streaming?: boolean;
  autofocus?: boolean;
  conversationId?: string;
  profileId?: string;
  profileLocked?: boolean;
  knowledgeItems?: { id: string; name: string }[];
  isGroup?: boolean;
  groupAgents?: { agent_id: string; profile_id?: string | null; name: string; color: string; icon: string }[];
  groupMembers?: { id: string; user_id: string | null; user_name?: string; agent_id: string | null }[];
  replyTo?: { id: string; label: string; snippet: string } | null;
}>();
export interface SendOptions {
  profileId?: string;
  stagedFiles?: File[];
  knowledgeIds?: string[];
  attachedFileIds?: string[];
  mentions?: string[];
  replyToId?: string;
}

export interface SendPayload {
  profileId?: string;
  stagedFiles?: File[];
  knowledgeIds?: string[];
  attachedFileIds?: string[];
  mentions?: string[];
  replyToId?: string;
}

const emit = defineEmits<{ "update:modelValue": [string]; send: [SendPayload]; cancel: []; command: [string]; typing: []; "cancel-reply": [] }>();

const ta = ref<HTMLTextAreaElement | null>(null);
const wrap = ref<HTMLElement | null>(null);
const fileInput = ref<HTMLInputElement | null>(null);
const showProfile = ref(false);
const showAttach = ref(false);
const showKnowledgePicker = ref(false);
const showFilePicker = ref(false);
const standaloneFiles = ref<FileItem[]>([]);
const stagedFileRefs = ref<{ id: string; name: string }[]>([]);
const pickerFolder = ref("/");
const pickerBreadcrumbs = computed(() => {
  const parts = pickerFolder.value.split("/").filter(Boolean);
  const result = [{ path: "/", label: "根目录" }];
  let path = "";
  for (const p of parts) { path += "/" + p; result.push({ path, label: p }); }
  return result;
});
const profiles = ref<Profile[]>([]);
const selected = ref<Profile | null>(null);
const stagedFiles = ref<File[]>([]);
const stagedKnowledge = ref<{ id: string; name: string }[]>([]);
const stagedPreviews = ref<Map<number, string>>(new Map());

// ── Draft auto-save ──
let _draftTimer: ReturnType<typeof setTimeout> | null = null;
watch(() => props.conversationId, (newId, oldId) => {
  if (oldId && props.modelValue.trim()) {
    localStorage.setItem(`draft:${oldId}`, props.modelValue);
  } else if (oldId) {
    localStorage.removeItem(`draft:${oldId}`);
  }
  if (newId) {
    const saved = localStorage.getItem(`draft:${newId}`) || "";
    emit("update:modelValue", saved);
    nextTick(() => autoresize());
  }
});
watch(() => props.modelValue, (val) => {
  if (!props.conversationId) return;
  if (_draftTimer) clearTimeout(_draftTimer);
  _draftTimer = setTimeout(() => {
    if (val.trim()) {
      localStorage.setItem(`draft:${props.conversationId}`, val);
    } else {
      localStorage.removeItem(`draft:${props.conversationId}`);
    }
  }, 600);
});

// ── Slash commands ──
const SLASH_CMDS = [
  { cmd: "new",    label: "新建对话",   desc: "开始一个全新的对话" },
  { cmd: "export", label: "导出对话",   desc: "导出为 Markdown / JSON" },
  { cmd: "clear",  label: "清空草稿",   desc: "清空当前输入内容" },
];
const showSlashMenu = ref(false);
const slashIdx = ref(0);
const slashFiltered = computed(() => {
  if (!props.modelValue.startsWith("/")) return [];
  const q = props.modelValue.slice(1).toLowerCase();
  return q === "" ? SLASH_CMDS : SLASH_CMDS.filter(c => c.cmd.includes(q) || c.label.includes(q));
});

// ── Draggable resize ──
const composerHeight = ref(parseInt(localStorage.getItem("composer-height") || "84"));
let _resizing = false;
let _resizeStartY = 0;
let _resizeStartH = 0;
function onResizeStart(e: MouseEvent) {
  _resizing = true;
  _resizeStartY = e.clientY;
  _resizeStartH = composerHeight.value;
  window.addEventListener("mousemove", onResizeMove);
  window.addEventListener("mouseup", onResizeEnd);
  e.preventDefault();
}
function onResizeMove(e: MouseEvent) {
  if (!_resizing) return;
  const delta = _resizeStartY - e.clientY;
  composerHeight.value = Math.max(84, Math.min(400, _resizeStartH + delta));
}
function onResizeEnd() {
  _resizing = false;
  localStorage.setItem("composer-height", String(composerHeight.value));
  window.removeEventListener("mousemove", onResizeMove);
  window.removeEventListener("mouseup", onResizeEnd);
}



// @mention state
const showMentionPicker = ref(false);
const mentionQuery = ref("");
const mentionIdx = ref(0); // keyboard-highlighted row in the mention picker
const mentionMentions = ref<{ key: string; name: string }[]>([]); // collected mentions, keyed by stable id + the display name inserted into the text

const filteredAgents = computed(() => {
  if (!props.groupAgents && !props.groupMembers) return [];
  const q = mentionQuery.value.toLowerCase();
  // AI agents
  const agentItems = (props.groupAgents || []).map((a) => ({ ...a, _type: "agent" as const }));
  // Human members (only those with user_id)
  const humanItems = (props.groupMembers || [])
    .filter((m) => m.user_id && !m.agent_id)
    .map((m) => ({
      agent_id: `user:${m.user_id}`,
      name: m.user_name || m.user_id!.substring(0, 8),
      color: "#4a9eff",
      icon: "user",
      _type: "human" as const,
    }));
  const all = [
    { agent_id: "__all_agents__", name: "所有AI", color: "#888", icon: "users", _type: "all" as const },
    { agent_id: "__all_humans__", name: "所有真人", color: "#4a9eff", icon: "user-check", _type: "all_humans" as const },
    ...agentItems,
    ...humanItems,
  ];
  if (!q) return all;
  return all.filter(
    (a) => a.name.toLowerCase().includes(q) || a.agent_id.toLowerCase().includes(q)
  );
});

onMounted(async () => {
  document.addEventListener("mousedown", onDocClick);
  try {
    profiles.value = await profilesApi.list();
    // Use profileId prop if provided, otherwise default to first
    if (props.profileId) {
      selected.value = profiles.value.find((p) => p.id === props.profileId) || profiles.value[0] || null;
    } else {
      selected.value = profiles.value[0] || null;
    }
  } catch {
    /* ignore */
  }
});
// Sync selected profile when profileId prop changes (e.g., conversation switch)
watch(() => props.profileId, (newId) => {
  if (newId && profiles.value.length) {
    const found = profiles.value.find((p) => p.id === newId);
    if (found) selected.value = found;
  }
});
onBeforeUnmount(() => {
  document.removeEventListener("mousedown", onDocClick);
  window.removeEventListener("mousemove", onResizeMove);
  window.removeEventListener("mouseup", onResizeEnd);
});
function onDocClick(e: MouseEvent) {
  if (wrap.value && !wrap.value.contains(e.target as Node)) {
    showProfile.value = false;
    showAttach.value = false;
    showKnowledgePicker.value = false;
    showFilePicker.value = false;
  }
}

const personal = computed(() => profiles.value.filter((p) => p.scope === "personal"));
const team = computed(() => profiles.value.filter((p) => p.scope === "team"));
const globalProfiles = computed(() => profiles.value.filter((p) => p.scope === "global"));
const pillLabel = computed(() => selected.value?.name || props.agent?.label || branding.shortName);
const pillColor = computed(() => selected.value?.color || props.agent?.color || branding.accent);
const pillModel = computed(() => selected.value?.default_model || props.agent?.model || "ACP");

function autoresize() {
  const el = ta.value;
  if (!el) return;
  el.style.height = "auto";
  el.style.height = Math.min(220, Math.max(el.scrollHeight, 84)) + "px";
}
function onInput(e: Event) {
  const val = (e.target as HTMLTextAreaElement).value;
  emit("update:modelValue", val);
  autoresize();

  // Detect slash commands (only at start, single line)
  if (val.startsWith("/") && !val.includes("\n")) {
    showSlashMenu.value = true;
    slashIdx.value = 0;
  } else {
    showSlashMenu.value = false;
  }

  // Detect @mention trigger (works in any group, even with zero AI agents)
  if (props.isGroup) {
    const cursor = (e.target as HTMLTextAreaElement).selectionStart || val.length;
    const beforeCursor = val.slice(0, cursor);
    const atMatch = beforeCursor.match(/@(\S*)$/);
    if (atMatch) {
      showMentionPicker.value = true;
      mentionQuery.value = atMatch[1];
      mentionIdx.value = 0;
    } else {
      showMentionPicker.value = false;
      mentionQuery.value = "";
    }
    // Throttled typing ping so other members see "正在输入…".
    const now = Date.now();
    if (now - _lastTyping > 2000) {
      _lastTyping = now;
      emit("typing");
    }
  }
}

let _lastTyping = 0;
function onKey(e: KeyboardEvent) {
  // @mention picker navigation — must win over the plain-Enter-sends-message
  // path below, otherwise pressing Enter right after typing "@name" sends the
  // message with unregistered "@name" text instead of confirming the mention.
  if (showMentionPicker.value && filteredAgents.value.length) {
    if (e.key === "ArrowDown") { e.preventDefault(); mentionIdx.value = (mentionIdx.value + 1) % filteredAgents.value.length; return; }
    if (e.key === "ArrowUp") { e.preventDefault(); mentionIdx.value = (mentionIdx.value - 1 + filteredAgents.value.length) % filteredAgents.value.length; return; }
    if (e.key === "Tab" || (e.key === "Enter" && !e.isComposing)) { e.preventDefault(); selectMention(filteredAgents.value[mentionIdx.value]); return; }
    if (e.key === "Escape") { e.preventDefault(); showMentionPicker.value = false; return; }
  }
  // Slash command navigation
  if (showSlashMenu.value && slashFiltered.value.length) {
    if (e.key === "ArrowDown") { e.preventDefault(); slashIdx.value = (slashIdx.value + 1) % slashFiltered.value.length; return; }
    if (e.key === "ArrowUp") { e.preventDefault(); slashIdx.value = (slashIdx.value - 1 + slashFiltered.value.length) % slashFiltered.value.length; return; }
    if (e.key === "Tab" || (e.key === "Enter" && !e.isComposing)) { e.preventDefault(); selectSlashCmd(slashFiltered.value[slashIdx.value]); return; }
    if (e.key === "Escape") { showSlashMenu.value = false; return; }
  }
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    if (props.modelValue.trim() && !props.streaming) doSend();
  }
}
function selectSlashCmd(item: { cmd: string }) {
  showSlashMenu.value = false;
  emit("update:modelValue", "");
  emit("command", item.cmd);
}
function doSend() {
  const files = stagedFiles.value.length ? [...stagedFiles.value] : undefined;
  const kIds = stagedKnowledge.value.length ? stagedKnowledge.value.map((k) => k.id) : undefined;
  const fIds = stagedFileRefs.value.length ? stagedFileRefs.value.map((f) => f.id) : undefined;

  // Only keep mentions whose "@name" text the user hasn't since deleted —
  // selecting a mention and then backspacing it out of the visible text must
  // not silently keep routing to that assistant.
  const mentions = mentionMentions.value
    .filter((m) => props.modelValue.includes(`@${m.name}`))
    .map((m) => m.key);
  mentionMentions.value = [];

  stagedFiles.value = [];
  stagedPreviews.value = new Map();
  stagedKnowledge.value = [];
  stagedFileRefs.value = [];

  // Clear saved draft on send
  if (props.conversationId) localStorage.removeItem(`draft:${props.conversationId}`);

  // The profile pill is a personal-chat concept (the conversation's default
  // assistant). In group chat, who answers is decided purely by @mentions /
  // auto_reply — never forward it there, or it silently overrides @mentions.
  const profileId = props.isGroup ? undefined : selected.value?.id;
  emit("send", { profileId, stagedFiles: files, knowledgeIds: kIds, attachedFileIds: fIds, mentions, replyToId: props.replyTo?.id });
}

function selectMention(agent: { agent_id: string; name: string; profile_id?: string | null }) {
  // Replace the @query in the text with @name
  const taEl = ta.value;
  if (!taEl) return;
  const val = props.modelValue;
  const cursor = taEl.selectionStart || val.length;
  const beforeCursor = val.slice(0, cursor);
  const afterCursor = val.slice(cursor);
  const newBefore = beforeCursor.replace(/@\S*$/, `@${agent.name} `);
  const newVal = newBefore + afterCursor;
  emit("update:modelValue", newVal);
  showMentionPicker.value = false;
  mentionQuery.value = "";

  // Track the mention. Prefer profile:{id} over the bare agent_id — two
  // Profiles can share one underlying agent_id, and only the profile id
  // disambiguates which one is actually being addressed.
  const mentionKey = agent.profile_id ? `profile:${agent.profile_id}` : agent.agent_id;
  if (agent.agent_id === "__all_agents__") {
    mentionMentions.value = [{ key: "__all_agents__", name: agent.name }];
  } else if (agent.agent_id === "__all_humans__") {
    mentionMentions.value = [{ key: "__all_humans__", name: agent.name }];
  } else if (!mentionMentions.value.some((m) => m.key === mentionKey)) {
    mentionMentions.value.push({ key: mentionKey, name: agent.name });
  }

  // Restore focus
  nextTick(() => {
    taEl.focus();
    const newCursor = newBefore.length;
    taEl.setSelectionRange(newCursor, newCursor);
  });
}
function pickProfile(p: Profile) {
  selected.value = p;
  showProfile.value = false;
}
function removeStagedFile(idx: number) {
  stagedFiles.value.splice(idx, 1);
  // Rebuild preview map with shifted indices
  const newPreviews = new Map<number, string>();
  stagedPreviews.value.forEach((url, i) => {
    if (i < idx) newPreviews.set(i, url);
    else if (i > idx) newPreviews.set(i - 1, url);
  });
  stagedPreviews.value = newPreviews;
}
function removeStagedKnowledge(idx: number) {
  stagedKnowledge.value.splice(idx, 1);
}

function triggerUpload() {
  showAttach.value = false;
  fileInput.value?.click();
}

function openKnowledgePicker() {
  showAttach.value = false;
  showKnowledgePicker.value = true;
}

async function openFilePicker() {
  showAttach.value = false;
  pickerFolder.value = "/";
  await loadPickerFiles("/");
  showFilePicker.value = true;
}

async function loadPickerFiles(folder: string) {
  pickerFolder.value = folder;
  try {
    standaloneFiles.value = await filesApi.listStandalone(folder);
  } catch {
    standaloneFiles.value = [];
  }
}

function navigatePickerFolder(item: FileItem) {
  if (!item.is_folder) return;
  // Real DB folders have UUID ids and folder_path == parent dir.
  // Virtual inferred folders have ids like "folder:/path" and folder_path == full path.
  const isVirtual = item.id.startsWith("folder:");
  if (isVirtual) {
    loadPickerFiles(item.folder_path || "/");
  } else {
    const parent = item.folder_path === "/" ? "" : (item.folder_path || "");
    loadPickerFiles(`${parent}/${item.name}`);
  }
}

function toggleFileRef(item: FileItem) {
  const idx = stagedFileRefs.value.findIndex((f) => f.id === item.id);
  if (idx >= 0) {
    stagedFileRefs.value.splice(idx, 1);
  } else {
    stagedFileRefs.value.push({ id: item.id, name: item.name });
  }
}

function isFileSelected(id: string) {
  return stagedFileRefs.value.some((f) => f.id === id);
}

function removeStagedFileRef(idx: number) {
  stagedFileRefs.value.splice(idx, 1);
}

function toggleKnowledge(item: { id: string; name: string }) {
  const idx = stagedKnowledge.value.findIndex((k) => k.id === item.id);
  if (idx >= 0) {
    stagedKnowledge.value.splice(idx, 1);
  } else {
    stagedKnowledge.value.push({ id: item.id, name: item.name });
  }
}

function isKnowledgeSelected(id: string) {
  return stagedKnowledge.value.some((k) => k.id === id);
}

function onFileSelected(e: Event) {
  const file = (e.target as HTMLInputElement).files?.[0];
  if (!file) return;
  const idx = stagedFiles.value.length;
  stagedFiles.value.push(file);
  if (file.type.startsWith("image/")) {
    const reader = new FileReader();
    reader.onload = () => stagedPreviews.value.set(idx, reader.result as string);
    reader.readAsDataURL(file);
  }
  const sizeKB = (file.size / 1024).toFixed(1);
  if (file.size > 100_000) {
    ns.toast(`已添加 ${file.name} (${sizeKB}KB) — 文件较大，AI 将通过工具分段读取`);
  } else {
    ns.toast(`已添加 ${file.name} (${sizeKB}KB)`);
  }
  if (fileInput.value) fileInput.value.value = "";
}

function onPaste(e: ClipboardEvent) {
  const items = e.clipboardData?.items;
  if (!items) return;
  // Only intercept pure image paste; mixed content (text+image) lets text through
  const hasText = Array.from(items).some(it => it.type.startsWith("text/"));
  if (hasText) return; // let default paste handle text
  for (const item of items) {
    if (item.type.startsWith("image/")) {
      e.preventDefault();
      const file = item.getAsFile();
      if (file) {
        const idx = stagedFiles.value.length;
        stagedFiles.value.push(file);
        const reader = new FileReader();
        reader.onload = () => stagedPreviews.value.set(idx, reader.result as string);
        reader.readAsDataURL(file);
        ns.toast("已粘贴图片");
      }
    }
  }
}

function isImageFile(f: File) {
  return f.type.startsWith("image/");
}
</script>

<template>
  <div class="composer-wrap" ref="wrap" :style="{ minHeight: composerHeight + 'px' }">
    <!-- Drag handle for resizing composer height -->
    <div class="composer-resize-handle" @mousedown="onResizeStart" title="拖拽调整高度"></div>
    <input ref="fileInput" type="file" accept="image/*,.pdf,.md,.txt,.json,.csv,.html,.js,.ts,.py,.go,.rs,.yaml,.yml,.toml,.sh,.xml,.css,.diff" style="display:none" @change="onFileSelected" />
    <!-- reply quote bar -->
    <div v-if="replyTo" class="reply-bar">
      <Icon name="corner-up-left" :size="13" />
      <span class="reply-label">回复 {{ replyTo.label }}：</span>
      <span class="reply-snippet">{{ replyTo.snippet }}</span>
      <button class="reply-cancel" @click="emit('cancel-reply')" title="取消回复">&times;</button>
    </div>
    <!-- staged file chips -->
    <div v-if="stagedFiles.length || stagedKnowledge.length || stagedFileRefs.length" class="staged-files">
      <span v-for="(f, i) in stagedFiles" :key="'f'+i" class="staged-chip" :class="{ 'staged-chip-image': isImageFile(f) }">
        <img v-if="stagedPreviews.has(i)" :src="stagedPreviews.get(i)" class="staged-preview" />
        <Icon v-else name="paperclip" :size="11" />
        <span class="staged-name">{{ f.name }}</span>
        <button class="staged-rm" @click="removeStagedFile(i)">&times;</button>
      </span>
      <span v-for="(k, i) in stagedKnowledge" :key="'k'+i" class="staged-chip" style="background: var(--accent-soft, rgba(184,133,42,0.12));">
        <Icon name="doc" :size="11" />
        <span class="staged-name">{{ k.name }}</span>
        <button class="staged-rm" @click="removeStagedKnowledge(i)">&times;</button>
      </span>
      <span v-for="(f, i) in stagedFileRefs" :key="'fr'+i" class="staged-chip" style="background: rgba(58,109,161,0.12);">
        <Icon name="folder" :size="11" />
        <span class="staged-name">{{ f.name }}</span>
        <button class="staged-rm" @click="removeStagedFileRef(i)">&times;</button>
      </span>
    </div>
    <div class="composer">
      <!-- Slash command picker -->
      <div v-if="showSlashMenu && slashFiltered.length" class="slash-menu">
        <button
          v-for="(item, i) in slashFiltered"
          :key="item.cmd"
          class="slash-item"
          :class="{ active: i === slashIdx }"
          @mousedown.prevent="selectSlashCmd(item)"
        >
          <span class="slash-cmd">/{{ item.cmd }}</span>
          <span class="slash-label">{{ item.label }}</span>
          <span class="slash-desc">{{ item.desc }}</span>
        </button>
      </div>

      <!-- @mention picker -->
      <div v-if="showMentionPicker && filteredAgents.length" class="mention-picker">
        <button
          v-for="(a, i) in filteredAgents"
          :key="a.agent_id"
          class="mention-item"
          :class="{ active: i === mentionIdx }"
          @mousedown.prevent="selectMention(a)"
          @mouseenter="mentionIdx = i"
        >
          <span class="mention-avatar" :style="{ background: a.color }"><Icon :name="a.icon" :size="11" /></span>
          <span class="mention-name">{{ a.name }}</span>
          <span v-if="a.agent_id === '__all_agents__'" class="mention-tag">圆桌</span>
          <span v-else-if="a.agent_id === '__all_humans__'" class="mention-tag">全员</span>
          <span v-else-if="a._type === 'human'" class="mention-tag">成员</span>
        </button>
      </div>
      <textarea
        ref="ta"
        class="composer-input"
        :placeholder="placeholder"
        :value="modelValue"
        :autofocus="autofocus"
        rows="1"
        @input="onInput"
        @keydown="onKey"
        @paste="onPaste"
      ></textarea>
      <div class="composer-toolbar">
        <div style="position: relative">
          <button class="composer-tool" :class="{ active: showAttach }" title="附件" @click="showAttach = !showAttach; showKnowledgePicker = false"><Icon name="paperclip" /></button>
          <div v-if="showAttach" class="menu" style="bottom: 120%; left: 0; min-width: 220px">
            <div class="menu-label">添加到会话</div>
            <button class="menu-item" @click="triggerUpload">
              <Icon name="paperclip" /><span class="m-name">上传本地文件</span>
            </button>
            <button class="menu-item" @click="openFilePicker">
              <Icon name="folder" /><span class="m-name">引用文件管理文件</span>
            </button>
            <button class="menu-item" @click="openKnowledgePicker" :disabled="!knowledgeItems?.length">
              <Icon name="doc" /><span class="m-name">引用知识库</span>
              <span v-if="knowledgeItems?.length" style="margin-left:auto;font-size:11px;color:var(--ink-mute)">{{ knowledgeItems.length }}</span>
            </button>
          </div>
          <!-- file picker -->
          <div v-if="showFilePicker" class="menu" style="bottom: 120%; left: 0; min-width: 300px; max-height: 280px; overflow-y: auto;">
            <div class="menu-label" style="display:flex;align-items:center;gap:4px;flex-wrap:wrap;">
              <span v-for="(bc, i) in pickerBreadcrumbs" :key="bc.path">
                <span v-if="i > 0" style="color:var(--ink-faint)">/</span>
                <button style="background:none;border:none;cursor:pointer;color:var(--accent);font-size:11px;padding:1px 2px" @click="loadPickerFiles(bc.path)">{{ bc.label }}</button>
              </span>
            </div>
            <div v-if="!standaloneFiles.length" style="padding: 12px; font-size: 12px; color: var(--ink-mute); text-align: center;">
              暂无文件
            </div>
            <button
              v-for="item in standaloneFiles"
              :key="item.id"
              class="menu-item"
              :class="{ active: isFileSelected(item.id) }"
              @click="item.is_folder ? navigatePickerFolder(item) : toggleFileRef(item)"
            >
              <Icon :name="item.is_folder ? 'folder' : 'paperclip'" :size="13" />
              <span class="m-name">{{ item.name }}</span>
              <span v-if="!item.is_folder && item.size" style="margin-left:auto;font-size:11px;color:var(--ink-mute)">{{ item.size < 1024 ? item.size + 'B' : (item.size/1024).toFixed(1) + 'KB' }}</span>
              <Icon v-if="!item.is_folder && isFileSelected(item.id)" name="check" :size="12" style="margin-left:4px;color:var(--accent)" />
            </button>
            <div class="menu-sep"></div>
            <button class="menu-item" style="color:var(--accent)" @click="showFilePicker = false">确定 ({{ stagedFileRefs.length }} 已选)</button>
          </div>
          <!-- knowledge picker -->
          <div v-if="showKnowledgePicker && knowledgeItems?.length" class="menu" style="bottom: 120%; left: 0; min-width: 260px; max-height: 240px; overflow-y: auto;">
            <div class="menu-label">选择知识库条目</div>
            <button
              v-for="item in knowledgeItems"
              :key="item.id"
              class="menu-item"
              :class="{ active: isKnowledgeSelected(item.id) }"
              @click="toggleKnowledge(item)"
            >
              <Icon name="doc" :size="13" />
              <span class="m-name">{{ item.name }}</span>
              <Icon v-if="isKnowledgeSelected(item.id)" name="check" :size="12" style="margin-left:auto;color:var(--accent)" />
            </button>
            <div class="menu-sep"></div>
            <button class="menu-item" style="color:var(--accent)" @click="showKnowledgePicker = false">确定 ({{ stagedKnowledge.length }} 已选)</button>
          </div>
        </div>
        <span class="composer-spacer"></span>
        <!-- 群聊模式下不显示 profile 选择器，回复者由 @mention 决定 -->
        <div v-if="!isGroup" style="position: relative">
          <button class="model-pick" :class="{ locked: profileLocked }" :title="profileLocked ? '助手已锁定，创建新会话可切换' : '切换助手'" @click="!profileLocked && (showProfile = !showProfile)">
            <span class="profile-dot" :style="{ background: pillColor }"></span>
            {{ pillLabel }}
            <span class="profile-model">{{ pillModel }}</span>
            <Icon v-if="profileLocked" name="lock" :size="12" />
            <Icon v-else name="chevron_down" />
          </button>
          <div v-if="showProfile && !profileLocked" class="menu" style="bottom: 110%; right: 0; min-width: 320px">
            <div class="menu-label">个人 Profile</div>
            <ProfileListItem v-for="p in personal" :key="p.id" :profile="p" :active="selected?.id === p.id" @pick="pickProfile" />
            <div class="menu-sep"></div>
            <div class="menu-label">团队 Profile · 共享记忆</div>
            <ProfileListItem v-for="p in team" :key="p.id" :profile="p" :active="selected?.id === p.id" @pick="pickProfile" />
            <template v-if="globalProfiles.length">
              <div class="menu-sep"></div>
              <div class="menu-label">全局 Profile</div>
              <ProfileListItem v-for="p in globalProfiles" :key="p.id" :profile="p" :active="selected?.id === p.id" @pick="pickProfile" />
            </template>
            <div class="menu-foot-acp"><Icon name="bolt" /> 通过 ACP · v1 协议建立会话</div>
          </div>
        </div>
        <button
          v-if="streaming"
          class="send-btn cancel"
          title="中断"
          @click="emit('cancel')"
        >
          <Icon name="stop" />
        </button>
        <button
          v-else
          class="send-btn"
          :class="{ armed: modelValue.trim(), disabled: !modelValue.trim() }"
          title="发送"
          @click="modelValue.trim() && doSend()"
        >
          <Icon name="arrow_up" />
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* ── Resize handle ── */
.composer-resize-handle {
  height: 6px;
  cursor: row-resize;
  border-radius: 3px 3px 0 0;
  background: transparent;
  transition: background 120ms;
  display: flex;
  align-items: center;
  justify-content: center;
}
.composer-resize-handle::after {
  content: "";
  display: block;
  width: 32px;
  height: 2px;
  border-radius: 1px;
  background: var(--rule);
  transition: background 120ms;
}
.composer-resize-handle:hover::after {
  background: var(--accent);
}

/* ── Slash command menu ── */
.slash-menu {
  position: absolute;
  bottom: 100%;
  left: 0;
  right: 0;
  max-height: 200px;
  overflow-y: auto;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  box-shadow: var(--shadow-md);
  padding: 4px;
  margin-bottom: 4px;
  z-index: 10;
}
.slash-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border-radius: 6px;
  border: none;
  background: transparent;
  cursor: pointer;
  width: 100%;
  text-align: left;
  transition: background 120ms;
}
.slash-item:hover,
.slash-item.active {
  background: var(--accent-tint);
}
.slash-cmd {
  font-family: "JetBrains Mono", monospace;
  font-size: 12px;
  font-weight: 600;
  color: var(--accent);
  min-width: 70px;
}
.slash-label {
  font-size: 12.5px;
  font-weight: 500;
  color: var(--ink);
  flex: 1;
}
.slash-desc {
  font-size: 11px;
  color: var(--ink-mute);
}

/* ── Reply quote bar ── */
.reply-bar {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 5px 10px;
  margin-bottom: 4px;
  background: var(--accent-tint);
  border-left: 2px solid var(--accent);
  border-radius: 6px;
  font-size: 12px;
  color: var(--ink-mute);
}
.reply-label { font-weight: 600; color: var(--ink); flex-shrink: 0; }
.reply-snippet { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; }
.reply-cancel {
  border: none; background: transparent; cursor: pointer;
  font-size: 16px; line-height: 1; color: var(--ink-faint); padding: 0 2px;
}
.reply-cancel:hover { color: var(--ink); }

/* ── Mention picker (existing) ── */
.mention-picker {
  position: absolute;
  bottom: 100%;
  left: 0;
  right: 0;
  max-height: 200px;
  overflow-y: auto;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  box-shadow: var(--shadow-md);
  padding: 4px;
  margin-bottom: 4px;
  z-index: 10;
}
.mention-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border-radius: 6px;
  border: none;
  background: transparent;
  cursor: pointer;
  width: 100%;
  text-align: left;
  transition: background 120ms;
}
.mention-item:hover,
.mention-item.active {
  background: var(--accent-tint);
}
.mention-avatar {
  width: 22px;
  height: 22px;
  border-radius: 4px;
  display: grid;
  place-items: center;
  color: #fff;
  flex-shrink: 0;
}
.mention-name {
  font-size: 12.5px;
  font-weight: 500;
  color: var(--ink);
  flex: 1;
}
.mention-tag {
  font-size: 10px;
  color: var(--accent);
  background: var(--accent-tint);
  padding: 1px 6px;
  border-radius: 4px;
  font-weight: 600;
}
</style>
