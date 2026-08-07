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
import FeedbackPanel from "@/components/FeedbackPanel.vue";
import HelpPanel from "@/components/HelpPanel.vue";
import ToastContainer from "@/components/ToastContainer.vue";
import { useChatStore } from "@/stores/chat";
import { useAuthStore } from "@/stores/auth";
import { useNotificationStore } from "@/stores/notifications";
import { usePresence } from "@/composables/usePresence";

const chat = useChatStore();
const ns = useNotificationStore();
const { startHeartbeat, stopHeartbeat } = usePresence();
const collapsed = ref(false);
const showTweaks = ref(false);
const showSearch = ref(false);

const ATMOS_CYCLE = ["letter", "cinnabar", "celadon", "night", "ink"];
function cycleAtmos() {
  const cur = document.body.dataset.atmos || "letter";
  const next = ATMOS_CYCLE[(ATMOS_CYCLE.indexOf(cur) + 1) % ATMOS_CYCLE.length];
  document.body.dataset.atmos = next;
  const saved = JSON.parse(localStorage.getItem("hermes.tweaks") || "{}");
  localStorage.setItem("hermes.tweaks", JSON.stringify({ ...saved, atmos: next }));
}

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

onMounted(async () => {
  // Ensure auth is ready before loading data
  const auth = useAuthStore();
  if (!auth.ready) await auth.bootstrap();

  // Wait for auth to be fully ready before loading data
  // This prevents race condition where API calls happen before token is restored
  await new Promise(resolve => setTimeout(resolve, 100));

  // Load data sequentially to ensure token is available
  try {
    await chat.loadConversations();
  } catch (e) {
    console.error("[layout] loadConversations:", e);
  }
  try {
    await chat.loadProfiles();
  } catch (e) {
    console.error("[layout] loadProfiles:", e);
  }
  try {
    await chat.loadTeams();
  } catch (e) {
    console.error("[layout] loadTeams:", e);
  }
  try {
    await chat.loadConfig();
  } catch (e) {
    console.error("[layout] loadConfig:", e);
  }

  startHeartbeat();
  window.addEventListener("keydown", onKey);
  window.addEventListener("hermes:search", openSearch);
  // P2-1: surface API errors globally (the axios client only dispatches the
  // event — this is the single listener that turns it into a toast).
  onApiError = (e: Event) => {
    const msg = (e as CustomEvent<string>).detail;
    if (msg) ns.toast(msg, "error");
  };
  window.addEventListener("hermes:api-error", onApiError);
});
onBeforeUnmount(() => {
  stopHeartbeat();
  window.removeEventListener("keydown", onKey);
  window.removeEventListener("hermes:search", openSearch);
  if (onApiError) window.removeEventListener("hermes:api-error", onApiError);
});
let onApiError: ((e: Event) => void) | null = null;
function openSearch() {
  showSearch.value = true;
}

const isNight = computed(() => ["night", "ink"].includes(document.body.dataset.atmos || ""));
</script>

<template>
  <div class="app" :class="{ 'side-collapsed': collapsed }">
    <Sidebar />
    <main class="main">
      <div class="topbar">
        <button class="icon-btn" title="折叠侧栏 (⌘\)" @click="collapsed = !collapsed"><Icon name="sidebar" /></button>
        <button class="icon-btn" title="搜索 (⌘K)" @click="showSearch = true"><Icon name="search" /></button>
        <span class="topbar-spacer"></span>
        <FeedbackPanel />
        <NotificationPanel />
        <HelpPanel />
        <button class="icon-btn" title="切换氣質" @click="cycleAtmos"><Icon :name="isNight ? 'sun' : 'moon'" /></button>
        <button class="icon-btn" title="调整 Tweaks" @click="showTweaks = !showTweaks"><Icon name="settings" /></button>
      </div>
      <router-view />
    </main>

    <TweaksPanel :open="showTweaks" @close="showTweaks = false" />
    <SearchPalette v-if="showSearch" @close="showSearch = false" />
    <ToastContainer />
  </div>
</template>
