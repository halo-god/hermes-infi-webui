<script setup lang="ts">
/* 1:1 port of the prototype ⌘K search palette — searches conversations + agents. */
import { computed, nextTick, onMounted, ref, watch } from "vue";
import { useRouter } from "vue-router";
import Icon from "@/components/Icon.vue";
import { useChatStore } from "@/stores/chat";
import { conversationsApi } from "@/api/conversations";
import type { Conversation } from "@/types";

const emit = defineEmits<{ close: [] }>();
const chat = useChatStore();
const router = useRouter();
const q = ref("");
const input = ref<HTMLInputElement | null>(null);
const convoResults = ref<Conversation[]>([]);

onMounted(() => nextTick(() => input.value?.focus()));

// Conversation search hits the backend (covers the full corpus, not just what's
// already loaded into the store) while agent matching stays client-side since
// the profile list is always fully loaded.
let searchTimer: ReturnType<typeof setTimeout> | null = null;
watch(q, (term) => {
  if (searchTimer) clearTimeout(searchTimer);
  const trimmed = term.trim();
  if (!trimmed) {
    convoResults.value = [];
    return;
  }
  searchTimer = setTimeout(async () => {
    convoResults.value = await conversationsApi.list({ q: trimmed, limit: 8 });
  }, 250);
});

const matches = computed(() => {
  const term = q.value.trim().toLowerCase();
  if (!term) {
    return { convos: chat.conversations.slice(0, 5), profiles: chat.profiles.filter((p) => p.is_active).slice(0, 4) };
  }
  return {
    convos: convoResults.value,
    profiles: chat.profiles.filter((p) => p.is_active && (p.name.toLowerCase().includes(term) || (p.desc || "").toLowerCase().includes(term))),
  };
});

async function openConvo(id: string) {
  emit("close");
  await chat.openConversation(id);
  router.push({ path: "/", query: { c: id } });
}
function pickAgent() {
  emit("close");
  router.push("/");
}
</script>

<template>
  <div class="scrim" @mousedown.self="emit('close')">
    <div class="palette" @keydown.esc="emit('close')">
      <input ref="input" v-model="q" class="palette-input" placeholder="搜索对话、助手…  按 Esc 关闭" @keydown.esc="emit('close')" />
      <div class="palette-list">
        <template v-if="matches.convos.length">
          <div class="palette-section">对话</div>
          <button v-for="c in matches.convos" :key="c.id" class="menu-item" @click="openConvo(c.id)">
            <Icon :name="c.icon || 'chat'" />
            <span class="m-name">{{ c.title }}</span>
            <span class="m-tag">{{ c.pinned ? "已置顶" : "" }}</span>
          </button>
        </template>
        <template v-if="matches.profiles.length">
          <div class="palette-section">助手</div>
          <button v-for="p in matches.profiles" :key="p.id" class="menu-item" @click="pickAgent">
            <Icon :name="p.icon || 'sparkle'" :style="{ color: p.color || '#b8852a' }" />
            <span class="m-name">{{ p.name }}</span>
            <span class="m-tag">{{ p.scope }}</span>
          </button>
        </template>
        <div v-if="!matches.convos.length && !matches.profiles.length" class="palette-empty">没有匹配项</div>
      </div>
    </div>
  </div>
</template>
