<script setup lang="ts">
/* 1:1 port of the prototype composer (hermes-app.js Composer): rich toolbar +
   profile dropdown (ACP) + circular send button. */
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from "vue";
import Icon from "@/components/Icon.vue";
import { agentsApi, type Profile } from "@/api/agents";
import { useNotificationStore } from "@/stores/notifications";

const ns = useNotificationStore();

const props = defineProps<{
  modelValue: string;
  placeholder?: string;
  agent?: { label?: string; color?: string | null; model?: string } | null;
  streaming?: boolean;
  autofocus?: boolean;
  conversationId?: string;
  knowledgeItems?: { id: string; name: string }[];
  isGroup?: boolean;
  groupAgents?: { agent_id: string; name: string; color: string; icon: string }[];
}>();
export interface SendOptions {
  profileId?: string;
  webSearch?: boolean;
  deepThink?: boolean;
  stagedFiles?: File[];
  knowledgeIds?: string[];
  mentions?: string[];
}

const emit = defineEmits<{ "update:modelValue": [string]; send: [SendOptions]; cancel: [] }>();

const ta = ref<HTMLTextAreaElement | null>(null);
const wrap = ref<HTMLElement | null>(null);
const fileInput = ref<HTMLInputElement | null>(null);
const showProfile = ref(false);
const showAttach = ref(false);
const showKnowledgePicker = ref(false);
const webSearch = ref(false);
const deepThink = ref(false);
const profiles = ref<Profile[]>([]);
const selected = ref<Profile | null>(null);
const stagedFiles = ref<File[]>([]);
const stagedKnowledge = ref<{ id: string; name: string }[]>([]);
const stagedPreviews = ref<Map<number, string>>(new Map());

// @mention state
const showMentionPicker = ref(false);
const mentionQuery = ref("");
const mentionMentions = ref<string[]>([]); // collected agent_ids from @mentions

const filteredAgents = computed(() => {
  if (!props.groupAgents) return [];
  const q = mentionQuery.value.toLowerCase();
  const all = [
    { agent_id: "__all__", name: "所有人", color: "#888", icon: "users" },
    ...props.groupAgents,
  ];
  if (!q) return all;
  return all.filter(
    (a) => a.name.toLowerCase().includes(q) || a.agent_id.toLowerCase().includes(q)
  );
});

onMounted(async () => {
  document.addEventListener("mousedown", onDocClick);
  try {
    profiles.value = await agentsApi.profiles();
    selected.value = profiles.value[0] || null;
  } catch {
    /* ignore */
  }
});
onBeforeUnmount(() => document.removeEventListener("mousedown", onDocClick));
function onDocClick(e: MouseEvent) {
  if (wrap.value && !wrap.value.contains(e.target as Node)) {
    showProfile.value = false;
    showAttach.value = false;
    showKnowledgePicker.value = false;
  }
}

const personal = computed(() => profiles.value.filter((p) => p.scope === "personal"));
const team = computed(() => profiles.value.filter((p) => p.scope === "team"));
const globalProfiles = computed(() => profiles.value.filter((p) => p.scope === "global"));
const pillLabel = computed(() => selected.value?.name || props.agent?.label || "Hermes");
const pillColor = computed(() => selected.value?.color || props.agent?.color || "#b8852a");
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

  // Detect @mention trigger
  if (props.isGroup && props.groupAgents?.length) {
    const cursor = (e.target as HTMLTextAreaElement).selectionStart || val.length;
    const beforeCursor = val.slice(0, cursor);
    const atMatch = beforeCursor.match(/@(\S*)$/);
    if (atMatch) {
      showMentionPicker.value = true;
      mentionQuery.value = atMatch[1];
    } else {
      showMentionPicker.value = false;
      mentionQuery.value = "";
    }
  }
}
function onKey(e: KeyboardEvent) {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    if (props.modelValue.trim() && !props.streaming) doSend();
  }
}
function doSend() {
  const files = stagedFiles.value.length ? [...stagedFiles.value] : undefined;
  const kIds = stagedKnowledge.value.length ? stagedKnowledge.value.map((k) => k.id) : undefined;

  // Extract @mentions from text
  const mentions = [...mentionMentions.value];
  mentionMentions.value = [];

  stagedFiles.value = [];
  stagedPreviews.value = new Map();
  stagedKnowledge.value = [];
  emit("send", { profileId: selected.value?.id, webSearch: webSearch.value, deepThink: deepThink.value, stagedFiles: files, knowledgeIds: kIds, mentions });
}

function selectMention(agent: { agent_id: string; name: string }) {
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

  // Track the mention
  if (agent.agent_id === "__all__") {
    mentionMentions.value = ["__all__"];
  } else if (!mentionMentions.value.includes(agent.agent_id)) {
    mentionMentions.value.push(agent.agent_id);
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
  ns.toast(`已添加 ${file.name}`);
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
  <div class="composer-wrap" ref="wrap">
    <input ref="fileInput" type="file" accept="image/*,.pdf,.md,.txt,.json,.csv,.html,.js,.ts,.py,.go,.rs,.yaml,.yml,.toml,.sh,.xml,.css,.diff" style="display:none" @change="onFileSelected" />
    <!-- staged file chips -->
    <div v-if="stagedFiles.length || stagedKnowledge.length" class="staged-files">
      <span v-for="(f, i) in stagedFiles" :key="'f'+i" class="staged-chip" :class="{ 'staged-chip-image': isImageFile(f) }">
        <img v-if="stagedPreviews.has(i)" :src="stagedPreviews.get(i)" class="staged-preview" />
        <Icon v-else name="paperclip" :size="11" />
        <span class="staged-name">{{ f.name }}</span>
        <button class="staged-rm" @click="removeStagedFile(i)">&times;</button>
      </span>
      <span v-for="(k, i) in stagedKnowledge" :key="'k'+i" class="staged-chip" style="background: var(--accent-soft, rgba(184,133,42,0.12));">
        <Icon name="doc" :size="11" />
        <span class="staged-name">{{ k.name }}</span>
        <button class="staged-rm" @click="removeStagedKnowledge(i)">×</button>
      </span>
    </div>
    <div class="composer">
      <!-- @mention picker -->
      <div v-if="showMentionPicker && filteredAgents.length" class="mention-picker">
        <button
          v-for="a in filteredAgents"
          :key="a.agent_id"
          class="mention-item"
          @mousedown.prevent="selectMention(a)"
        >
          <span class="mention-avatar" :style="{ background: a.color }"><Icon :name="a.icon" :size="11" /></span>
          <span class="mention-name">{{ a.name }}</span>
          <span v-if="a.agent_id === '__all__'" class="mention-tag">圆桌</span>
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
            <button class="menu-item" @click="openKnowledgePicker" :disabled="!knowledgeItems?.length">
              <Icon name="doc" /><span class="m-name">引用知识库</span>
              <span v-if="knowledgeItems?.length" style="margin-left:auto;font-size:11px;color:var(--ink-mute)">{{ knowledgeItems.length }}</span>
            </button>
            <button class="menu-item" @click="showAttach = false"><Icon name="globe" /><span class="m-name">粘贴网页链接</span></button>
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
        <button class="composer-tool" :class="{ active: webSearch }" title="网页搜索" @click="webSearch = !webSearch"><Icon name="globe" /> 联网</button>
        <button class="composer-tool" :class="{ active: deepThink }" title="思考模式" @click="deepThink = !deepThink"><Icon name="sparkle" /> 深思</button>
        <span class="composer-spacer"></span>
        <div style="position: relative">
          <button class="model-pick" title="切换 Hermes Profile · ACP 会话" @click="showProfile = !showProfile">
            <span class="profile-dot" :style="{ background: pillColor }"></span>
            {{ pillLabel }}
            <span class="profile-model">{{ pillModel }}</span>
            <Icon name="chevron_down" />
          </button>
          <div v-if="showProfile" class="menu" style="bottom: 110%; right: 0; min-width: 320px">
            <div class="menu-label">个人 Profile</div>
            <button v-for="p in personal" :key="p.id" class="menu-item profile-item" :class="{ active: selected?.id === p.id }" @click="pickProfile(p)">
              <span class="profile-avatar" :style="{ background: p.color }"><Icon :name="p.icon" /></span>
              <div class="profile-meta"><div class="profile-name">{{ p.name }} <span class="profile-handle">@{{ p.handle }}</span></div><div class="profile-desc">{{ p.desc }}</div></div>
              <span class="profile-tag">{{ p.default_model }}</span>
            </button>
            <div class="menu-sep"></div>
            <div class="menu-label">团队 Profile · 共享记忆</div>
            <button v-for="p in team" :key="p.id" class="menu-item profile-item" :class="{ active: selected?.id === p.id }" @click="pickProfile(p)">
              <span class="profile-avatar" :style="{ background: p.color }"><Icon :name="p.icon" /></span>
              <div class="profile-meta"><div class="profile-name">{{ p.name }} <span class="profile-handle">@{{ p.handle }}</span></div><div class="profile-desc">{{ p.desc }}</div></div>
              <span class="profile-tag">{{ p.default_model }}</span>
            </button>
            <template v-if="globalProfiles.length">
              <div class="menu-sep"></div>
              <div class="menu-label">全局 Profile</div>
              <button v-for="p in globalProfiles" :key="p.id" class="menu-item profile-item" :class="{ active: selected?.id === p.id }" @click="pickProfile(p)">
                <span class="profile-avatar" :style="{ background: p.color }"><Icon :name="p.icon" /></span>
                <div class="profile-meta"><div class="profile-name">{{ p.name }} <span class="profile-handle">@{{ p.handle }}</span></div><div class="profile-desc">{{ p.desc }}</div></div>
                <span class="profile-tag">{{ p.default_model }}</span>
              </button>
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
.mention-item:hover {
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
