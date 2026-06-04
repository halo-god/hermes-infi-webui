<script setup lang="ts">
/* Persistent app shell — matches the prototype structure: a fixed left sidebar
   and a main area whose content swaps. Hosts the global Tweaks panel + ⌘K
   search palette. */
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import Icon from "@/components/Icon.vue";
import Sidebar from "@/components/Sidebar.vue";
import TweaksPanel from "@/components/TweaksPanel.vue";
import SearchPalette from "@/components/SearchPalette.vue";
import NotificationPanel from "@/components/NotificationPanel.vue";
import ToastContainer from "@/components/ToastContainer.vue";
import { useChatStore } from "@/stores/chat";

const chat = useChatStore();
const collapsed = ref(false);
const showTweaks = ref(false);
const showSearch = ref(false);

function onKey(e: KeyboardEvent) {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
    e.preventDefault();
    showSearch.value = true;
  }
  if ((e.metaKey || e.ctrlKey) && e.key === "\\") {
    e.preventDefault();
    collapsed.value = !collapsed.value;
  }
}

onMounted(() => {
  chat.loadConversations();
  chat.loadProfiles();
  chat.loadTeams();
  chat.loadConfig();
  window.addEventListener("keydown", onKey);
  window.addEventListener("hermes:search", openSearch);
});
onBeforeUnmount(() => {
  window.removeEventListener("keydown", onKey);
  window.removeEventListener("hermes:search", openSearch);
});
function openSearch() {
  showSearch.value = true;
}

const hermes = computed(() => chat.profiles.find((p) => p.default_agent_id === "hermes") || chat.profiles.find((p) => p.handle === "hermes"));
</script>

<template>
  <div class="app" :class="{ 'side-collapsed': collapsed }">
    <Sidebar />
    <main class="main">
      <div class="topbar">
        <button class="icon-btn" title="折叠侧栏 (⌘\)" @click="collapsed = !collapsed"><Icon name="sidebar" /></button>
        <button class="icon-btn" title="搜索 (⌘K)" @click="showSearch = true"><Icon name="search" /></button>
        <span class="topbar-spacer"></span>
        <span class="top-pill">
          <span class="dot" :style="hermes && !hermes.is_active ? 'background:var(--ink-faint)' : ''"></span>
          ACP · {{ hermes?.name || 'Hermes Agent' }}
        </span>
        <NotificationPanel />
        <button class="icon-btn" title="调整 Tweaks" @click="showTweaks = !showTweaks"><Icon name="settings" /></button>
      </div>
      <router-view />
    </main>

    <TweaksPanel :open="showTweaks" @close="showTweaks = false" />
    <SearchPalette v-if="showSearch" @close="showSearch = false" />
    <ToastContainer />
  </div>
</template>
