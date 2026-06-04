<script setup lang="ts">
/* 1:1 port of the prototype sidebar (hermes-app.js Sidebar). Persistent across
   all main screens. Wired to the real chat store + teams + router. */
import { computed, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import Icon from "@/components/Icon.vue";
import ConfirmModal from "@/components/ConfirmModal.vue";
import NewTeamModal from "@/components/NewTeamModal.vue";
import { useAuthStore } from "@/stores/auth";
import { useChatStore } from "@/stores/chat";
import { useNotificationStore } from "@/stores/notifications";
import { useTheme } from "@/composables/useTheme";
import { conversationsApi } from "@/api/conversations";

const auth = useAuthStore();
const chat = useChatStore();
const ns = useNotificationStore();
const router = useRouter();
const route = useRoute();
const { theme, toggleTheme } = useTheme();
const { t } = useI18n();
const showNewTeam = ref(false);

const isAdmin = computed(() => auth.user?.role === "super_admin" || auth.user?.role === "admin");
const onChat = computed(() => route.name === "home");

// ── context menu state ──
const ctxMenu = ref<{ id: string; x: number; y: number } | null>(null);
const renamingId = ref<string | null>(null);
const renameVal = ref("");
const deleteTarget = ref<{ id: string; title: string } | null>(null);

async function newChat() {
  // Skip modal, use first active profile directly
  const p = chat.profiles.find((pp) => pp.is_active);
  if (p) {
    await chat.newConversationWithProfile(p.id);
    if (!onChat.value) router.push("/");
  }
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
    await navigator.clipboard.writeText(url);
    ns.toast("分享链接已复制到剪贴板");
  } catch (e: unknown) {
    ns.toast("分享失败：" + ((e as Error).message || "未知错误"), "error");
  }
}
</script>

<template>
  <aside class="side" @click="closeCtx">
    <div class="side-inner">
      <div class="brand">
        <div class="brand-mark"><Icon name="brand" :size="22" /></div>
        <div>
          <div class="brand-name">Hermes</div>
          <div class="brand-tag">信使 · MESSENGER</div>
        </div>
      </div>

      <div class="side-section" style="margin-top: 4px">
        <div class="side-row" :class="{ active: onChat && !chat.activeId }" @click="newChat">
          <Icon name="plus" class="ico" /> {{ t('nav.newChat') }}
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

      <div class="side-label">{{ t('nav.conversations') }}</div>
      <div class="convo-list">
        <div
          v-for="c in chat.conversations"
          :key="c.id"
          class="convo"
          :class="{ active: onChat && c.id === chat.activeId }"
          @click="openConvo(c.id)"
          @contextmenu="onCtxMenu($event, c.id)"
        >
          <div class="convo-ico">
            <span v-if="c.id === chat.streamingConvoId" class="convo-live-ring"></span>
            <Icon v-else :name="c.icon || 'chat'" />
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
          <Icon v-else-if="c.pinned" name="pin" style="width: 11px; height: 11px; color: var(--accent-deep); flex-shrink: 0" />
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
        <div class="menu-sep"></div>
        <button class="menu-item danger" @click="askDelete(ctxMenu!.id, chat.conversations.find(c => c.id === ctxMenu!.id)?.title || '')">
          <Icon name="close" /> <span class="m-name">删除</span>
        </button>
      </div>
    </div>
  </Teleport>

  <!-- Delete confirmation -->
  <ConfirmModal
    v-if="deleteTarget"
    title="删除会话"
    :message="`确认删除「${deleteTarget.title}」？此操作不可恢复。`"
    confirm-text="删除"
    :danger="true"
    @close="deleteTarget = null"
    @confirm="doDelete"
  />

  <NewTeamModal v-if="showNewTeam" @close="showNewTeam = false" @created="onTeamCreated" />
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
</style>
