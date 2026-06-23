<script setup lang="ts">
/* 1:1 port of the prototype sidebar (hermes-app.js Sidebar). Persistent across
   all main screens. Wired to the real chat store + teams + router. */
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import Icon from "@/components/Icon.vue";
import NewTeamModal from "@/components/NewTeamModal.vue";
import NewGroupModal from "@/components/NewGroupModal.vue";
import { useAuthStore } from "@/stores/auth";
import { useBrandingStore } from "@/stores/branding";
import { useChatStore } from "@/stores/chat";
import { useNotificationStore } from "@/stores/notifications";
import { useTheme } from "@/composables/useTheme";
import { conversationsApi } from "@/api/conversations";
import type { Conversation } from "@/types";

const auth = useAuthStore();
const chat = useChatStore();
const branding = useBrandingStore();
const ns = useNotificationStore();
const router = useRouter();
const route = useRoute();
const { theme, toggleTheme } = useTheme();
const { t } = useI18n();
const showNewTeam = ref(false);
const showNewGroup = ref(false);

// Infinite scroll: load the next conversation page when the sentinel appears.
const loadMoreSentinel = ref<HTMLElement | null>(null);
let convoObserver: IntersectionObserver | null = null;
onMounted(() => {
  convoObserver = new IntersectionObserver((entries) => {
    if (entries.some((e) => e.isIntersecting)) chat.loadMoreConversations();
  });
  watch(loadMoreSentinel, (el, prev) => {
    if (prev) convoObserver?.unobserve(prev);
    if (el) convoObserver?.observe(el);
  }, { immediate: true });
  void chat.loadFolders();
});
onBeforeUnmount(() => convoObserver?.disconnect());

const groupConversations = computed(() => chat.conversations.filter((c) => c.type === "group"));
const personalConversations = computed(() => chat.conversations.filter((c) => c.type !== "group"));

// ── Date grouping (borrowed from hermes-web-ui accordion pattern) ──
function dateGroup(dateStr: string): "today" | "yesterday" | "week" | "earlier" {
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const d = new Date(dateStr);
  const day = new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const diff = today - day;
  if (diff <= 0) return "today";
  if (diff <= 86400000) return "yesterday";
  if (diff <= 6 * 86400000) return "week";
  return "earlier";
}

const DATE_LABELS: Record<string, string> = {
  pinned: "置顶",
  today: "今天",
  yesterday: "昨天",
  week: "最近 7 天",
  earlier: "更早",
};

interface ConvoGroup { key: string; label: string; items: Conversation[] }

const groupedConversations = computed((): ConvoGroup[] => {
  const pinned = personalConversations.value.filter((c) => c.pinned);
  const unpinned = personalConversations.value.filter((c) => !c.pinned);
  const unfiled = unpinned.filter((c) => !c.folder_id);

  const buckets: Record<string, Conversation[]> = { today: [], yesterday: [], week: [], earlier: [] };
  for (const c of unfiled) buckets[dateGroup(c.updated_at || c.created_at)].push(c);

  const groups: ConvoGroup[] = [];
  if (pinned.length) groups.push({ key: "pinned", label: DATE_LABELS.pinned, items: pinned });
  (["today", "yesterday", "week", "earlier"] as const).forEach((k) => {
    if (buckets[k].length) groups.push({ key: k, label: DATE_LABELS[k], items: buckets[k] });
  });
  return groups;
});

/** Conversations grouped by folder (non-pinned conversations, assigned to a folder).
 *  Empty folders are shown too — otherwise a freshly-created folder vanishes
 *  until a conversation is moved into it, which is confusing. */
const folderGroups = computed(() => {
  return chat.folders.map((f) => ({
    folder: f,
    items: personalConversations.value.filter((c) => !c.pinned && c.folder_id === f.id),
  }));
});

/** Pinned folders shown above the unfiled date buckets. */
const pinnedFolderGroups = computed(() => folderGroups.value.filter((g) => g.folder.pinned));
/** Unpinned folders shown below the date buckets. */
const unpinnedFolderGroups = computed(() => folderGroups.value.filter((g) => !g.folder.pinned));

// Collapsed state for folders (persisted to localStorage).
const collapsedFolders = ref<Set<string>>(new Set(
  (() => { try { return JSON.parse(localStorage.getItem("hermes.collapsedFolders") || "[]"); } catch { return []; } })()
));
function toggleFolderCollapse(folderId: string) {
  const s = new Set(collapsedFolders.value);
  if (s.has(folderId)) s.delete(folderId); else s.add(folderId);
  collapsedFolders.value = s;
  localStorage.setItem("hermes.collapsedFolders", JSON.stringify([...s]));
}

// Profile color dot — looks up the profile associated with a conversation
function profileColor(c: Conversation): string | null {
  if (!c.profile_id) return null;
  return chat.profiles.find((p) => p.id === c.profile_id)?.color ?? null;
}

const isAdmin = computed(() => auth.user?.role === "super_admin" || auth.user?.role === "admin");
const onChat = computed(() => route.name === "home");

// ── context menu state ──
const ctxMenu = ref<{ id: string; x: number; y: number } | null>(null);
const renamingId = ref<string | null>(null);
const renameVal = ref("");
const deleteTarget = ref<{ id: string; title: string } | null>(null);

async function newChat() {
  chat.landing();
  if (!onChat.value) router.push("/");
}
async function openConvo(id: string) {
  await chat.openConversation(id);
  if (!onChat.value) router.push("/");
}
function onTeamCreated(team: { id: string }) {
  showNewTeam.value = false;
  router.push(`/teams/${team.id}`);
}
function openSearch() {
  window.dispatchEvent(new CustomEvent("hermes:search"));
}
async function onLogout() {
  await auth.logout();
  router.replace({ name: "login" });
}

// ── context menu actions ──
function onCtxMenu(e: MouseEvent, id: string) {
  e.preventDefault();
  ctxMenu.value = { id, x: e.clientX, y: e.clientY };
}
function closeCtx() {
  ctxMenu.value = null;
}
function startRename(id: string, title: string) {
  renamingId.value = id;
  renameVal.value = title;
  closeCtx();
}
async function confirmRename() {
  if (!renamingId.value || !renameVal.value.trim()) { renamingId.value = null; return; }
  await conversationsApi.update(renamingId.value, { title: renameVal.value.trim() });
  renamingId.value = null;
  await chat.loadConversations();
}
async function togglePin(id: string, pinned: boolean) {
  await conversationsApi.update(id, { pinned: !pinned });
  closeCtx();
  await chat.loadConversations();
}
function askDelete(id: string, title: string) {
  deleteTarget.value = { id, title };
  closeCtx();
}
async function doDelete() {
  if (!deleteTarget.value) return;
  await conversationsApi.remove(deleteTarget.value.id);
  deleteTarget.value = null;
  await chat.loadConversations();
}
async function shareConvo(id: string) {
  closeCtx();
  try {
    const res = await conversationsApi.share(id);
    const url = window.location.origin + res.share_url;
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(url);
    } else {
      const ta = document.createElement("textarea");
      ta.value = url;
      ta.style.cssText = "position:fixed;left:-9999px;top:-9999px";
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      document.body.removeChild(ta);
    }
    ns.toast("分享链接已复制到剪贴板");
  } catch (e: unknown) {
    ns.toast("分享失败：" + ((e as Error).message || "未知错误"), "error");
  }
}

// ── folder management ──
const showMoveMenu = ref<string | null>(null); // conversation id whose move-submenu is open
const newFolderName = ref("");
const showNewFolderInput = ref(false);

async function createFolder() {
  const name = newFolderName.value.trim();
  if (!name) return;
  try {
    await chat.createFolder(name);
    newFolderName.value = "";
    showNewFolderInput.value = false;
    ns.toast("文件夹已创建");
  } catch {
    ns.toast("创建失败（可能同名）", "error");
  }
}

async function moveToFolder(conversationId: string, folderId: string | null) {
  showMoveMenu.value = null;
  closeCtx();
  try {
    await chat.moveConversationToFolder(conversationId, folderId);
    ns.toast(folderId ? "已移入文件夹" : "已移出文件夹");
  } catch {
    ns.toast("移动失败", "error");
  }
}

async function renameFolderPrompt(folderId: string, currentName: string) {
  const name = window.prompt("文件夹名称", currentName);
  if (name === null) return;
  const trimmed = name.trim();
  if (!trimmed || trimmed === currentName) return;
  try {
    await chat.renameFolder(folderId, trimmed);
  } catch {
    ns.toast("重命名失败（可能同名）", "error");
  }
}

async function deleteFolderPrompt(folderId: string, name: string) {
  if (!window.confirm(`删除文件夹「${name}」？其中的会话将移至未分组，不会被删除。`)) return;
  try {
    await chat.deleteFolder(folderId);
    ns.toast("文件夹已删除");
  } catch {
    ns.toast("删除失败", "error");
  }
}

async function toggleFolderPin(folderId: string, currentlyPinned: boolean) {
  try {
    await chat.toggleFolderPinned(folderId, !currentlyPinned);
    ns.toast(currentlyPinned ? "已取消置顶" : "已置顶");
  } catch {
    ns.toast("操作失败", "error");
  }
}

// ── folder drag-to-reorder ──
const draggingFolderId = ref<string | null>(null);
const dropTargetFolderId = ref<string | null>(null);

function onFolderSortDragStart(e: DragEvent, folderId: string) {
  // Only start folder sort-drag if NOT dragging a conversation.
  if (draggingConvoId.value) return;
  draggingFolderId.value = folderId;
  if (e.dataTransfer) {
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/folder", folderId);
  }
}

function onFolderSortDragEnd() {
  draggingFolderId.value = null;
  dropTargetFolderId.value = null;
}

/** Unified dragover for folder headers: handles both conversation-drop (move
 *  convo into folder) and folder-sort (reorder folders). */
function onFolderHeaderDragOver(e: DragEvent, folderId: string) {
  e.preventDefault();
  if (draggingConvoId.value) {
    // A conversation is being dragged — highlight as a drop target.
    dragOverFolderId.value = folderId;
  } else if (draggingFolderId.value && draggingFolderId.value !== folderId) {
    // A folder is being dragged — highlight as a sort target.
    dropTargetFolderId.value = folderId;
  }
}

function onFolderHeaderDragLeave(folderId: string) {
  if (dragOverFolderId.value === folderId) dragOverFolderId.value = null;
  if (dropTargetFolderId.value === folderId) dropTargetFolderId.value = null;
}

/** Unified drop for folder headers: dispatch to conversation-move or folder-sort. */
async function onFolderHeaderDrop(e: DragEvent, folderId: string) {
  e.preventDefault();
  e.stopPropagation();
  if (draggingConvoId.value) {
    // Conversation drop — move into folder.
    const convoId = draggingConvoId.value;
    dragOverFolderId.value = null;
    draggingConvoId.value = null;
    try {
      await chat.moveConversationToFolder(convoId, folderId);
      ns.toast("已移入文件夹");
    } catch {
      ns.toast("移动失败", "error");
    }
  } else if (draggingFolderId.value && draggingFolderId.value !== folderId) {
    // Folder sort drop — reorder.
    const sourceId = draggingFolderId.value;
    dropTargetFolderId.value = null;
    draggingFolderId.value = null;

    const targetFolder = chat.folders.find((x) => x.id === folderId);
    if (!targetFolder) return;
    // Reorder within the same pinned/unpinned group.
    const list = chat.folders.filter((f) => f.pinned === targetFolder.pinned);
    const sourceIdx = list.findIndex((f) => f.id === sourceId);
    const targetIdx = list.findIndex((f) => f.id === folderId);
    if (sourceIdx < 0 || targetIdx < 0) return;

    const reordered = [...list];
    const [moved] = reordered.splice(sourceIdx, 1);
    reordered.splice(targetIdx, 0, moved);

    try {
      await chat.reorderFolders(reordered.map((f, i) => ({ id: f.id, sort_order: i + 1 })));
    } catch {
      ns.toast("排序失败", "error");
    }
  }
}

const folderCtx = ref<{ id: string; name: string; x: number; y: number } | null>(null);

function onFolderCtxMenu(e: MouseEvent, folderId: string, name: string) {
  e.preventDefault();
  e.stopPropagation();
  folderCtx.value = { id: folderId, name, x: e.clientX, y: e.clientY };
}

// ── drag & drop: move conversations between folders ──
const draggingConvoId = ref<string | null>(null);
const dragOverFolderId = ref<string | null>(null);
const dragOverUnfiled = ref(false);
const dragOverId = ref<string | null>(null);

function onDragStart(e: DragEvent, convoId: string) {
  draggingConvoId.value = convoId;
  if (e.dataTransfer) {
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", convoId);
  }
}

function onDragEnd() {
  draggingConvoId.value = null;
  dragOverFolderId.value = null;
  dragOverUnfiled.value = false;
  dragOverId.value = null;
}

async function onUnfiledDrop(e: DragEvent) {
  e.preventDefault();
  const convoId = draggingConvoId.value || e.dataTransfer?.getData("text/plain");
  dragOverUnfiled.value = false;
  draggingConvoId.value = null;
  if (!convoId) return;
  try {
    await chat.moveConversationToFolder(convoId, null);
    ns.toast("已移出文件夹");
  } catch {
    ns.toast("移动失败", "error");
  }
}
function closeFolderCtx() {
  folderCtx.value = null;
}
function doRenameFolder() {
  if (!folderCtx.value) return;
  const { id, name } = folderCtx.value;
  closeFolderCtx();
  void renameFolderPrompt(id, name);
}
function doDeleteFolder() {
  if (!folderCtx.value) return;
  const { id, name } = folderCtx.value;
  closeFolderCtx();
  void deleteFolderPrompt(id, name);
}
</script>

<template>
  <aside class="side" @click="closeCtx">
    <div class="side-inner">
      <div class="brand">
        <div class="brand-mark">
          <img v-if="branding.logoUrl" :src="branding.logoUrl" alt="" class="brand-mark-img" />
          <Icon v-else name="brand" :size="22" />
        </div>
        <div>
          <div class="brand-name">{{ branding.shortName }}</div>
          <div class="brand-tag">{{ branding.tagline }}</div>
        </div>
      </div>

      <div class="side-section" style="margin-top: 4px">
        <div class="side-row" :class="{ active: onChat && !chat.activeId }" @click="newChat">
          <Icon name="home" class="ico" /> {{ t('nav.home') || '首页' }}
          <span class="kbd">⌘N</span>
        </div>
        <div class="side-row" :class="{ active: route.name === 'history' }" @click="router.push('/history')" style="margin-top: -2px">
          <Icon name="list" class="ico" /> {{ t('nav.allChats') }}
          <span class="badge">{{ chat.conversations.length }}</span>
        </div>
      </div>

      <div class="side-section">
        <div class="side-row" @click="openSearch">
          <Icon name="search" class="ico" /> {{ t('nav.search') }}
          <span class="kbd">⌘K</span>
        </div>
        <div class="side-row" :class="{ active: route.name === 'schedule' }" @click="router.push('/schedule')">
          <Icon name="clock" class="ico" /> {{ t('nav.schedule') }}
        </div>
        <div class="side-row" :class="{ active: route.name === 'files' }" @click="router.push('/files')">
          <Icon name="folder" class="ico" /> {{ t('nav.files') }}
        </div>
        <div v-if="isAdmin" class="side-row" :class="{ active: route.name === 'terminal' }" @click="router.push('/terminal')">
          <Icon name="sparkle" class="ico" /> {{ t('nav.terminal') }}
        </div>
      </div>

      <div class="side-label">
        {{ t('nav.teams') }}
        <button :title="t('nav.newTeam')" @click="showNewTeam = true">+</button>
      </div>
      <div class="side-section" style="padding-top: 0">
        <div
          v-for="t in chat.teams"
          :key="t.id"
          class="team-row"
          :class="{ active: route.name === 'team' && route.params.id === t.id }"
          @click="router.push(`/teams/${t.id}`)"
        >
          <div class="team-avatar" :style="{ background: t.color || '#b8852a' }">{{ t.name.slice(0, 1) }}</div>
          {{ t.name }}
        </div>
        <div v-if="!chat.teams.length" style="padding: 4px 12px; font-size: 12px; color: var(--ink-mute)">还没有团队</div>
      </div>

      <div class="side-label">
        群聊
        <button title="创建群聊" @click="showNewGroup = true">+</button>
      </div>
      <div class="convo-list" style="margin-bottom: 8px">
        <div
          v-for="c in groupConversations"
          :key="c.id"
          class="convo"
          :class="{ active: onChat && c.id === chat.activeId }"
          @click="openConvo(c.id)"
          @contextmenu="onCtxMenu($event, c.id)"
        >
          <div class="convo-ico group-ico">
            <span v-if="c.id === chat.streamingConvoId" class="convo-live-ring"></span>
            <Icon v-else name="users" />
          </div>
          <template v-if="renamingId === c.id">
            <input
              v-model="renameVal"
              class="convo-rename-input"
              @keydown.enter="confirmRename"
              @keydown.escape="renamingId = null"
              @blur="confirmRename"
              autofocus
            />
          </template>
          <template v-else>
            <div class="convo-title">{{ c.title }}</div>
          </template>
          <span v-if="c.id === chat.streamingConvoId" class="convo-live-label">生成中</span>
        </div>
        <div v-if="!groupConversations.length" style="padding: 4px 12px; font-size: 11px; color: var(--ink-mute)">暂无群聊</div>
      </div>

      <div class="side-label">
        {{ t('nav.conversations') }}
        <button class="side-label-btn" title="新建文件夹" @click.stop="showNewFolderInput = !showNewFolderInput">+ 文件夹</button>
      </div>
      <div v-if="showNewFolderInput" class="new-folder-row">
        <input
          v-model="newFolderName"
          class="convo-rename-input"
          placeholder="文件夹名称"
          @keydown.enter="createFolder"
          @keydown.escape="showNewFolderInput = false"
        />
        <button class="btn mini" @click="createFolder">确定</button>
      </div>
      <div class="convo-list">
        <!-- Pinned folders (above date buckets) -->
        <template v-for="fg in pinnedFolderGroups" :key="fg.folder.id">
          <div
            class="convo-date-sep folder-sep"
            :class="{ 'folder-drop-target': dragOverFolderId === fg.folder.id, 'folder-sort-target': dropTargetFolderId === fg.folder.id, dragging: draggingFolderId === fg.folder.id }"
            draggable="true"
            @contextmenu="onFolderCtxMenu($event, fg.folder.id, fg.folder.name)"
            @dragover="onFolderHeaderDragOver($event, fg.folder.id)"
            @dragleave="onFolderHeaderDragLeave(fg.folder.id)"
            @drop="onFolderHeaderDrop($event, fg.folder.id)"
            @dragstart="onFolderSortDragStart($event, fg.folder.id)"
            @dragend="onFolderSortDragEnd"
          >
            <Icon v-if="fg.folder.pinned" name="pin" :size="9" style="color:var(--accent-deep)" />
            <Icon
              :name="collapsedFolders.has(fg.folder.id) ? 'chevron_right' : 'chevron_down'"
              :size="9"
              style="cursor:pointer"
              @click.stop="toggleFolderCollapse(fg.folder.id)"
            />
            <span class="folder-name" @click.stop="toggleFolderCollapse(fg.folder.id)">{{ fg.folder.name }}</span>
            <span class="folder-count">{{ fg.items.length }}</span>
          </div>
          <template v-if="!collapsedFolders.has(fg.folder.id)">
            <div
              v-for="c in fg.items"
              :key="c.id"
              class="convo"
              :class="{ active: onChat && c.id === chat.activeId, 'drag-over': dragOverId === c.id, dragging: draggingConvoId === c.id }"
              draggable="true"
              @click="openConvo(c.id)"
              @contextmenu="onCtxMenu($event, c.id)"
              @dragstart="onDragStart($event, c.id)"
              @dragend="onDragEnd"
            >
              <div class="convo-ico" style="position:relative">
                <span v-if="c.id === chat.streamingConvoId" class="convo-live-ring"></span>
                <Icon v-else :name="c.icon || 'chat'" />
                <span v-if="profileColor(c)" class="convo-profile-dot" :style="{ background: profileColor(c) as string }"></span>
              </div>
              <div class="convo-title">{{ c.title }}</div>
              <span v-if="c.id === chat.streamingConvoId" class="convo-live-label">生成中</span>
            </div>
          </template>
        </template>

        <!-- Date buckets (unfiled conversations) -->
        <template v-for="group in groupedConversations" :key="group.key">
          <div class="convo-date-sep" :class="{ 'folder-drop-target': dragOverUnfiled }" @dragover.prevent="dragOverUnfiled = true" @dragleave="dragOverUnfiled = false" @drop="onUnfiledDrop">
            <Icon v-if="group.key === 'pinned'" name="pin" :size="9" style="color:var(--accent-deep)" />
            {{ group.label }}
          </div>
          <div
            v-for="c in group.items"
            :key="c.id"
            class="convo"
            :class="{ active: onChat && c.id === chat.activeId, 'drag-over': dragOverId === c.id, dragging: draggingConvoId === c.id }"
            draggable="true"
            @click="openConvo(c.id)"
            @contextmenu="onCtxMenu($event, c.id)"
            @dragstart="onDragStart($event, c.id)"
            @dragend="onDragEnd"
          >
            <div class="convo-ico" style="position:relative">
              <span v-if="c.id === chat.streamingConvoId" class="convo-live-ring"></span>
              <Icon v-else :name="c.icon || 'chat'" />
              <span v-if="profileColor(c)" class="convo-profile-dot" :style="{ background: profileColor(c) as string }"></span>
            </div>
            <template v-if="renamingId === c.id">
              <input v-model="renameVal" class="convo-rename-input" @keydown.enter="confirmRename" @keydown.escape="renamingId = null" @blur="confirmRename" autofocus />
            </template>
            <template v-else>
              <div class="convo-title">{{ c.title }}</div>
            </template>
            <span v-if="c.id === chat.streamingConvoId" class="convo-live-label">生成中</span>
          </div>
        </template>

        <!-- Unpinned folders (below date buckets) -->
        <template v-for="fg in unpinnedFolderGroups" :key="fg.folder.id">
          <div
            class="convo-date-sep folder-sep"
            :class="{ 'folder-drop-target': dragOverFolderId === fg.folder.id, 'folder-sort-target': dropTargetFolderId === fg.folder.id, dragging: draggingFolderId === fg.folder.id }"
            draggable="true"
            @contextmenu="onFolderCtxMenu($event, fg.folder.id, fg.folder.name)"
            @dragover="onFolderHeaderDragOver($event, fg.folder.id)"
            @dragleave="onFolderHeaderDragLeave(fg.folder.id)"
            @drop="onFolderHeaderDrop($event, fg.folder.id)"
            @dragstart="onFolderSortDragStart($event, fg.folder.id)"
            @dragend="onFolderSortDragEnd"
          >
            <Icon
              :name="collapsedFolders.has(fg.folder.id) ? 'chevron_right' : 'chevron_down'"
              :size="9"
              style="cursor:pointer"
              @click.stop="toggleFolderCollapse(fg.folder.id)"
            />
            <span class="folder-name" @click.stop="toggleFolderCollapse(fg.folder.id)">{{ fg.folder.name }}</span>
            <span class="folder-count">{{ fg.items.length }}</span>
          </div>
          <template v-if="!collapsedFolders.has(fg.folder.id)">
            <div
              v-for="c in fg.items"
              :key="c.id"
              class="convo"
              :class="{ active: onChat && c.id === chat.activeId, 'drag-over': dragOverId === c.id, dragging: draggingConvoId === c.id }"
              draggable="true"
              @click="openConvo(c.id)"
              @contextmenu="onCtxMenu($event, c.id)"
              @dragstart="onDragStart($event, c.id)"
              @dragend="onDragEnd"
            >
              <div class="convo-ico" style="position:relative">
                <span v-if="c.id === chat.streamingConvoId" class="convo-live-ring"></span>
                <Icon v-else :name="c.icon || 'chat'" />
                <span v-if="profileColor(c)" class="convo-profile-dot" :style="{ background: profileColor(c) as string }"></span>
              </div>
              <div class="convo-title">{{ c.title }}</div>
              <span v-if="c.id === chat.streamingConvoId" class="convo-live-label">生成中</span>
            </div>
          </template>
        </template>

        <div v-if="!personalConversations.length" class="convo-empty">
          <Icon name="chat" :size="22" style="color:var(--ink-faint);margin-bottom:6px" />
          <div>还没有会话</div>
          <div style="font-size:10.5px;margin-top:2px">点击「新建会话」开始</div>
        </div>
        <div
          v-if="chat.hasMoreConversations && personalConversations.length"
          ref="loadMoreSentinel"
          class="convo-load-more"
        >
          {{ chat.loadingMoreConvos ? '加载中…' : '' }}
        </div>
      </div>

      <div class="side-foot" v-if="auth.user">
        <div class="side-row" @click="toggleTheme" :title="theme === 'dark' ? t('nav.lightMode') : t('nav.darkMode')">
          <Icon :name="theme === 'dark' ? 'sun' : 'moon'" class="ico" />
          {{ theme === 'dark' ? t('nav.lightMode') : t('nav.darkMode') }}
        </div>
        <div v-if="isAdmin" class="side-row" :class="{ active: route.name === 'admin' }" @click="router.push('/admin')">
          <Icon name="settings" class="ico" /> {{ t('nav.admin') }}
          <span class="badge" style="background: var(--accent-tint); color: var(--accent-deep); font-weight: 600">ADMIN</span>
        </div>
        <div class="side-row" :class="{ active: route.name === 'settings' }" @click="router.push('/settings')">
          <Icon name="user" class="ico" /> {{ t('nav.settings') }}
        </div>
        <div class="side-row" :class="{ active: route.name === 'settings' }" @click="router.push('/settings')" :title="t('nav.settings')">
          <div class="mem-avatar" :style="{ background: auth.user.color || '#b8852a', width: '20px', height: '20px', fontSize: '10px', marginLeft: '-2px', marginRight: '-2px' }">
            {{ auth.user.initials || auth.user.name.slice(0, 1) }}
          </div>
          {{ auth.user.name }}
          <button class="side-logout" :title="t('nav.logout')" @click.stop="onLogout"><Icon name="logout" /></button>
        </div>
      </div>
    </div>
  </aside>

  <!-- Context menu -->
  <Teleport to="body">
    <div v-if="ctxMenu" class="ctx-menu-scrim" @click="closeCtx">
      <div class="ctx-menu" :style="{ left: ctxMenu.x + 'px', top: ctxMenu.y + 'px' }" @click.stop>
        <button class="menu-item" @click="startRename(ctxMenu!.id, chat.conversations.find(c => c.id === ctxMenu!.id)?.title || '')">
          <Icon name="edit" /> <span class="m-name">重命名</span>
        </button>
        <button class="menu-item" @click="togglePin(ctxMenu!.id, !!chat.conversations.find(c => c.id === ctxMenu!.id)?.pinned)">
          <Icon name="pin" /> <span class="m-name">{{ chat.conversations.find(c => c.id === ctxMenu!.id)?.pinned ? '取消置顶' : '置顶' }}</span>
        </button>
        <button class="menu-item" @click="shareConvo(ctxMenu!.id)">
          <Icon name="share" /> <span class="m-name">分享</span>
        </button>
        <div class="menu-item has-sub" @mouseenter="showMoveMenu = ctxMenu!.id" @mouseleave="showMoveMenu = null">
          <Icon name="folder" /> <span class="m-name">移入文件夹</span>
          <Icon name="chevron_right" :size="9" style="margin-left:auto;opacity:.5" />
          <div v-if="showMoveMenu === ctxMenu!.id" class="sub-menu">
            <button
              class="menu-item"
              @click="moveToFolder(ctxMenu!.id, null)"
            >
              <span class="m-name">未分组</span>
            </button>
            <button
              v-for="f in chat.folders"
              :key="f.id"
              class="menu-item"
              @click="moveToFolder(ctxMenu!.id, f.id)"
            >
              <Icon name="folder" :size="11" />
              <span class="m-name">{{ f.name }}</span>
            </button>
            <div v-if="!chat.folders.length" class="sub-menu-empty">暂无文件夹</div>
          </div>
        </div>
        <div class="menu-sep"></div>
        <button class="menu-item danger" @click="askDelete(ctxMenu!.id, chat.conversations.find(c => c.id === ctxMenu!.id)?.title || '')">
          <Icon name="close" /> <span class="m-name">删除</span>
        </button>
      </div>
    </div>
  </Teleport>

  <!-- Folder context menu -->
  <Teleport to="body">
    <div v-if="folderCtx" class="ctx-menu-scrim" @click="closeFolderCtx">
      <div class="ctx-menu" :style="{ left: folderCtx.x + 'px', top: folderCtx.y + 'px' }" @click.stop>
        <button class="menu-item" @click="doRenameFolder">
          <Icon name="edit" /> <span class="m-name">重命名文件夹</span>
        </button>
        <button
          class="menu-item"
          @click="() => { const f = chat.folders.find(x => x.id === folderCtx!.id); if (f) toggleFolderPin(f.id, f.pinned); closeFolderCtx(); }"
        >
          <Icon name="pin" /> <span class="m-name">{{ chat.folders.find(x => x.id === folderCtx?.id)?.pinned ? '取消置顶' : '置顶文件夹' }}</span>
        </button>
        <div class="menu-sep"></div>
        <button class="menu-item danger" @click="doDeleteFolder">
          <Icon name="close" /> <span class="m-name">删除文件夹</span>
        </button>
      </div>
    </div>
  </Teleport>

  <!-- Delete confirmation -->
  <Teleport to="body">
    <div v-if="deleteTarget" class="ctx-menu-scrim" @click="deleteTarget = null">
      <div class="delete-confirm" @click.stop>
        <div class="delete-confirm-title">删除会话</div>
        <div class="delete-confirm-msg">确认删除「{{ deleteTarget.title }}」？此操作不可恢复。</div>
        <div class="delete-confirm-actions">
          <button class="btn" @click="deleteTarget = null">取消</button>
          <button class="btn danger-btn" @click="doDelete">删除</button>
        </div>
      </div>
    </div>
  </Teleport>

  <NewTeamModal v-if="showNewTeam" @close="showNewTeam = false" @created="onTeamCreated" />
  <NewGroupModal v-if="showNewGroup" @close="showNewGroup = false" @created="(id: string) => { showNewGroup = false; openConvo(id); }" />
</template>

<style scoped>
.convo-rename-input {
  flex: 1;
  min-width: 0;
  border: 1px solid var(--accent);
  border-radius: 4px;
  padding: 1px 6px;
  font-size: 12px;
  background: var(--surface);
  color: var(--ink);
  outline: none;
}
.ctx-menu-scrim {
  position: fixed;
  inset: 0;
  z-index: 999;
}
.ctx-menu {
  position: fixed;
  z-index: 1000;
  min-width: 160px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  box-shadow: var(--shadow-md);
  padding: 4px;
}
.delete-confirm {
  position: fixed;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  z-index: 1000;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 14px;
  box-shadow: var(--shadow-lg);
  padding: 24px;
  min-width: 320px;
  max-width: 420px;
}
.delete-confirm-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--ink);
  margin-bottom: 8px;
}
.delete-confirm-msg {
  font-size: 14px;
  color: var(--ink-soft);
  margin-bottom: 20px;
  line-height: 1.5;
}
.delete-confirm-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
.danger-btn {
  background: #dc3545 !important;
  border-color: #dc3545 !important;
  color: #fff !important;
}
.danger-btn:hover {
  background: #c82333 !important;
}
.group-ico {
  color: var(--accent) !important;
}
.convo-date-sep {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 8px 14px 3px;
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.09em;
  text-transform: uppercase;
  color: var(--ink-faint);
  user-select: none;
}
.convo-date-sep:first-child {
  padding-top: 2px;
}
.side-label-btn {
  margin-left: auto;
  font-size: 10px;
  font-weight: 500;
  letter-spacing: 0.02em;
  text-transform: none;
  color: var(--ink-mute);
  background: transparent;
  border: none;
  cursor: pointer;
  padding: 1px 6px;
  border-radius: 5px;
}
.side-label-btn:hover {
  background: var(--bg-hover);
  color: var(--ink);
}
.new-folder-row {
  display: flex;
  gap: 6px;
  padding: 4px 10px 6px;
}
.new-folder-row .convo-rename-input {
  flex: 1;
  min-width: 0;
}
.btn.mini {
  font-size: 11px;
  padding: 3px 10px;
  border-radius: 6px;
}
.folder-sep {
  cursor: default;
}
.folder-name {
  cursor: pointer;
  flex: 1;
}
.folder-name:hover {
  color: var(--ink-mute);
}
.folder-count {
  font-size: 9px;
  color: var(--ink-faint);
  background: var(--bg-hover);
  padding: 0 5px;
  border-radius: 999px;
  line-height: 14px;
}
.menu-item.has-sub {
  position: relative;
  display: flex;
  align-items: center;
  gap: 8px;
}
.sub-menu {
  position: absolute;
  left: 100%;
  top: -4px;
  min-width: 150px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  box-shadow: var(--shadow-md);
  padding: 4px;
  margin-left: 2px;
}
.sub-menu-empty {
  padding: 8px 12px;
  font-size: 11px;
  color: var(--ink-faint);
  font-style: italic;
}
.convo.dragging {
  opacity: 0.4;
}
.convo.drag-over {
  outline: 1.5px dashed var(--accent);
  outline-offset: -2px;
}
.folder-sep.dragging {
  opacity: 0.4;
}
.folder-sep.folder-sort-target {
  border-top: 2px solid var(--accent);
  padding-top: 2px;
}
.folder-sep.folder-drop-target {
  background: var(--accent-tint);
  color: var(--accent-deep);
  border-radius: 6px;
}
.convo-date-sep.folder-drop-target {
  background: var(--accent-tint);
  color: var(--accent-deep);
  border-radius: 6px;
}
.convo-profile-dot {
  position: absolute;
  bottom: -1px;
  right: -1px;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  border: 1.5px solid var(--bg-side);
}
.convo-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 28px 16px;
  font-size: 12px;
  color: var(--ink-mute);
  text-align: center;
}
.convo-load-more {
  min-height: 24px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 11px;
  color: var(--ink-faint);
}
</style>
