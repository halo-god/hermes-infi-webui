<script setup lang="ts">
import { onMounted, ref } from "vue";
import Icon from "@/components/Icon.vue";
import ModalShell from "@/components/ModalShell.vue";
import { agentsApi, type Profile } from "@/api/agents";

const emit = defineEmits<{ close: []; created: [profileId: string] }>();

const profiles = ref<Profile[]>([]);
const selected = ref<Profile | null>(null);
const loading = ref(true);

onMounted(async () => {
  try {
    profiles.value = await agentsApi.profiles();
    selected.value = profiles.value[0] || null;
  } catch {
    /* ignore */
  } finally {
    loading.value = false;
  }
});

function pick(p: Profile) {
  selected.value = p;
}
function confirm() {
  if (selected.value) emit("created", selected.value.id);
  else emit("created", "");
}
</script>

<template>
  <ModalShell title="开始新对话" subtitle="选择一位助手" :width="520" @close="emit('close')">
    <div v-if="loading" class="empty-state-lg" style="padding:32px">
      加载助手…
    </div>

    <div v-else-if="profiles.length" class="ncm-list">
      <button
        v-for="p in profiles"
        :key="p.id"
        class="ncm-card"
        :class="{ on: selected?.id === p.id }"
        @click="pick(p)"
      >
        <span class="ncm-avatar" :style="{ background: p.color }"><Icon :name="p.icon" /></span>
        <div class="ncm-meta">
          <div class="ncm-name">{{ p.name }}</div>
          <div class="ncm-desc">{{ p.desc }}</div>
          <div class="ncm-model">{{ p.default_model }}</div>
        </div>
        <Icon v-if="selected?.id === p.id" name="check" class="ncm-check" />
      </button>
    </div>

    <div v-else style="padding: 24px; text-align: center; color: var(--ink-mute); font-size: 13px">
      暂无可用的助手，将使用默认配置开始对话。
    </div>

    <template #foot>
      <button class="btn" @click="emit('close')">取消</button>
      <span class="flex-1"></span>
      <button class="btn primary" @click="confirm">
        开始对话 <Icon name="arrow_up" :size="12" />
      </button>
    </template>
  </ModalShell>
</template>

<style scoped>
.ncm-group-label {
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  color: var(--ink-mute);
  letter-spacing: 0.06em;
  padding: 0 2px 6px;
}
.ncm-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.ncm-card {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 14px;
  border-radius: 10px;
  border: 1.5px solid var(--rule);
  background: var(--bg-panel);
  text-align: left;
  cursor: pointer;
  transition: border-color 140ms, background 140ms;
  width: 100%;
}
.ncm-card:hover {
  border-color: var(--accent-soft);
  background: var(--accent-tint);
}
.ncm-card.on {
  border-color: var(--accent);
  background: var(--accent-tint);
}
.ncm-avatar {
  width: 36px;
  height: 36px;
  border-radius: 8px;
  display: grid;
  place-items: center;
  color: #fff;
  font-size: 16px;
  flex-shrink: 0;
}
.ncm-meta { flex: 1; min-width: 0; }
.ncm-name { font-size: 13.5px; font-weight: 600; color: var(--ink); }
.ncm-handle { font-size: 11.5px; color: var(--ink-mute); font-weight: 400; margin-left: 4px; }
.ncm-desc { font-size: 12px; color: var(--ink-soft); margin-top: 2px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.ncm-model { font-size: 10.5px; color: var(--ink-mute); font-family: var(--font-mono); margin-top: 3px; }
.ncm-check { color: var(--accent); flex-shrink: 0; }
</style>
