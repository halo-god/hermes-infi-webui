<script setup lang="ts">
import { computed } from "vue";
import ModalShell from "@/components/ModalShell.vue";
import Icon from "@/components/Icon.vue";
import { useChatStore } from "@/stores/chat";

const props = defineProps<{
  teamId: string;
  sharedAgentIds: string[];
}>();

const emit = defineEmits<{ close: []; toggle: [agentId: string] }>();
const chat = useChatStore();

// Build a unified list: profiles first, then raw agents that have no profile
const displayItems = computed(() => {
  const items: { id: string; label: string; icon: string; color: string; description: string }[] = [];
  const coveredAgentIds = new Set<string>();
  for (const p of chat.profiles) {
    items.push({ id: p.default_agent_id || p.handle || p.id, label: p.name, icon: p.icon || "sparkle", color: p.color || "#b8852a", description: p.desc || "" });
    if (p.default_agent_id) coveredAgentIds.add(p.default_agent_id);
  }
  for (const a of chat.agents) {
    if (!coveredAgentIds.has(a.id)) {
      items.push({ id: a.id, label: a.label, icon: a.icon || "sparkle", color: a.color || "#b8852a", description: a.description || "" });
    }
  }
  return items;
});

function isShared(id: string) {
  return props.sharedAgentIds.includes(id);
}
</script>

<template>
  <ModalShell title="管理共享助手" subtitle="点击助手卡片以加入或移出团队共享" :width="560" @close="emit('close')">
    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 10px; max-height: 400px; overflow-y: auto">
      <button
        v-for="a in displayItems"
        :key="a.id"
        class="agent-card"
        :style="isShared(a.id) ? 'border-color:var(--accent-soft);box-shadow:var(--shadow-sm);background:var(--accent-tint)' : ''"
        @click="emit('toggle', a.id)"
      >
        <div class="agent-icon" :style="{ background: a.color }">
          <Icon :name="a.icon" />
        </div>
        <div class="agent-meta">
          <div class="agent-name">
            {{ a.label }}
            <span v-if="isShared(a.id)" class="official">已共享</span>
          </div>
          <div class="agent-desc">{{ a.description }}</div>
        </div>
      </button>
    </div>
    <template #foot>
      <div style="display: flex; justify-content: flex-end; width: 100%">
        <button class="btn primary" @click="emit('close')">完成</button>
      </div>
    </template>
  </ModalShell>
</template>
