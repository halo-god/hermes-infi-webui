<script setup lang="ts">
import { computed, ref } from "vue";
import ModalShell from "@/components/ModalShell.vue";
import Icon from "@/components/Icon.vue";
import { useChatStore } from "@/stores/chat";
import { teamsApi } from "@/api/teams";
import type { TeamDetail } from "@/types";

const props = defineProps<{
  teamId: string;
  sharedProfileIds: string[];
}>();

const emit = defineEmits<{ close: []; updated: [detail: TeamDetail] }>();
const chat = useChatStore();
const saving = ref(false);

// Build a unified list: profiles first, then raw agents that have no profile
const displayItems = computed(() => {
  const items: { profileId: string; label: string; icon: string; color: string; description: string }[] = [];
  const coveredAgentIds = new Set<string>();
  for (const p of chat.profiles) {
    items.push({ profileId: p.id, label: p.name, icon: p.icon || "sparkle", color: p.color || "#b8852a", description: p.desc || "" });
    if (p.default_agent_id) coveredAgentIds.add(p.default_agent_id);
  }
  return items;
});

function isShared(profileId: string) {
  return props.sharedProfileIds.includes(profileId);
}

async function toggle(profileId: string) {
  if (saving.value) return;
  saving.value = true;
  try {
    const current = [...props.sharedProfileIds];
    const idx = current.indexOf(profileId);
    if (idx >= 0) current.splice(idx, 1);
    else current.push(profileId);
    const updated = await teamsApi.setSharedProfiles(props.teamId, current);
    emit("updated", updated);
  } catch {
    /* ignore */
  } finally {
    saving.value = false;
  }
}
</script>

<template>
  <ModalShell title="管理共享助手" subtitle="点击助手卡片以加入或移出团队共享" :width="560" @close="emit('close')">
    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 10px; max-height: 400px; overflow-y: auto">
      <button
        v-for="a in displayItems"
        :key="a.profileId"
        class="agent-card"
        :style="isShared(a.profileId) ? 'border-color:var(--accent-soft);box-shadow:var(--shadow-sm);background:var(--accent-tint)' : ''"
        @click="toggle(a.profileId)"
      >
        <div class="agent-icon" :style="{ background: a.color }">
          <Icon :name="a.icon" />
        </div>
        <div class="agent-meta">
          <div class="agent-name">
            {{ a.label }}
            <span v-if="isShared(a.profileId)" class="official">已共享</span>
          </div>
          <div class="agent-desc">{{ a.description }}</div>
        </div>
      </button>
      <div v-if="!displayItems.length" style="color:var(--ink-mute);font-size:13px;grid-column:1/-1;padding:20px;text-align:center">
        暂无可用助手 Profile，请先在「系统管理」中创建。
      </div>
    </div>
    <template #foot>
      <div style="display: flex; justify-content: flex-end; width: 100%">
        <button class="btn primary" @click="emit('close')">完成</button>
      </div>
    </template>
  </ModalShell>
</template>
