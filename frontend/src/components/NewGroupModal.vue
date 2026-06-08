<script setup lang="ts">
import { onMounted, ref } from "vue";
import Icon from "@/components/Icon.vue";
import ModalShell from "@/components/ModalShell.vue";
import { teamsApi } from "@/api/teams";
import { conversationsApi } from "@/api/conversations";
import { useNotificationStore } from "@/stores/notifications";
import type { Team } from "@/types";

const emit = defineEmits<{ close: []; created: [id: string] }>();
const ns = useNotificationStore();

const teams = ref<Team[]>([]);
const loading = ref(false);

onMounted(async () => {
  try {
    teams.value = await teamsApi.list();
  } catch { /* ignore */ }
});

async function selectTeam(team: Team) {
  loading.value = true;
  try {
    const title = `${team.name} · 群聊`;
    const res = await conversationsApi.createGroup(title, [], [], team.id);
    emit("created", res.id);
  } catch (e: unknown) {
    ns.toast("创建失败：" + ((e as Error).message || "未知错误"), "error");
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <ModalShell
    title="创建群聊"
    subtitle="选择团队，自动包含全部成员和助手"
    :width="400"
    @close="emit('close')"
  >
    <div style="padding: 0 4px">
      <div v-if="loading" style="text-align: center; color: var(--ink-mute); padding: 32px 0">
        创建中…
      </div>
      <div v-else-if="teams.length === 0" style="text-align: center; color: var(--ink-mute); padding: 32px 0">
        暂无团队，请先创建团队
      </div>
      <div v-else class="team-list">
        <button
          v-for="t in teams"
          :key="t.id"
          class="team-card"
          @click="selectTeam(t)"
          :disabled="loading"
        >
          <span class="team-icon" :style="{ background: t.color || '#666' }">
            <Icon name="cube" :size="16" />
          </span>
          <div class="team-meta">
            <div class="team-name">{{ t.name }}</div>
            <div class="team-handle" v-if="t.handle">@{{ t.handle }}</div>
          </div>
          <Icon name="arrow_right" :size="14" />
        </button>
      </div>
    </div>

    <template #foot>
      <button class="btn" @click="emit('close')">取消</button>
    </template>
  </ModalShell>
</template>

<style scoped>
.team-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.team-card {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 14px;
  border-radius: 10px;
  border: 1.5px solid var(--rule);
  background: var(--bg-panel);
  cursor: pointer;
  transition: border-color 140ms, background 140ms;
}
.team-card:hover {
  border-color: var(--accent);
  background: var(--bg-hover, rgba(99, 102, 241, 0.04));
}
.team-card:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.team-icon {
  width: 36px;
  height: 36px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  font-size: 16px;
  flex-shrink: 0;
}
.team-meta {
  flex: 1;
  min-width: 0;
}
.team-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--ink);
}
.team-handle {
  font-size: 11px;
  color: var(--ink-mute);
}
</style>
