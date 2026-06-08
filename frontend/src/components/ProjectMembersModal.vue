<script setup lang="ts">
/* Project members management modal */
import { ref, computed } from "vue";
import Icon from "@/components/Icon.vue";
import ModalShell from "@/components/ModalShell.vue";
import { projectsApi } from "@/api/projects";
import type { Member } from "@/types";

const props = defineProps<{
  projectId: string;
  projectName: string;
  teamMembers: Member[];
  currentMemberIds: string[];
}>();
const emit = defineEmits<{ close: []; updated: [] }>();

const selected = ref<Set<string>>(new Set(props.currentMemberIds));
const busy = ref(false);

const allSelected = computed(() => selected.value.size === props.teamMembers.length);

function toggle(id: string) {
  selected.value.has(id) ? selected.value.delete(id) : selected.value.add(id);
  selected.value = new Set(selected.value);
}

function toggleAll() {
  if (allSelected.value) {
    selected.value = new Set();
  } else {
    selected.value = new Set(props.teamMembers.map((m) => m.user_id));
  }
}

async function save() {
  busy.value = true;
  try {
    await projectsApi.setMembers(props.projectId, [...selected.value]);
    emit("updated");
    emit("close");
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <ModalShell :title="'项目成员 · ' + projectName" subtitle="管理哪些团队成员可以访问此项目" :width="480" @close="$emit('close')">
    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px">
      <div class="text-mute-sm">
        已选 {{ selected.size }} / {{ teamMembers.length }} 位成员
      </div>
      <button class="btn" style="font-size: 12px" @click="toggleAll">
        {{ allSelected ? '取消全选' : '全选' }}
      </button>
    </div>

    <div style="display: flex; flex-direction: column; gap: 6px; max-height: 360px; overflow-y: auto">
      <div
        v-for="m in teamMembers"
        :key="m.user_id"
        class="pm-row"
        :class="{ active: selected.has(m.user_id) }"
        @click="toggle(m.user_id)"
      >
        <div class="pm-avatar" :style="{ background: m.color || '#b8852a' }">
          {{ (m.name || '?').slice(0, 1) }}
        </div>
        <div class="flex-1-min">
          <div class="pm-name">{{ m.name || '未命名' }}</div>
          <div class="pm-email">{{ m.email }}</div>
        </div>
        <div class="pm-check">
          <Icon v-if="selected.has(m.user_id)" name="check" />
        </div>
      </div>
    </div>

    <div style="display: flex; justify-content: flex-end; gap: 8px; margin-top: 16px">
      <button class="btn" @click="$emit('close')">取消</button>
      <button class="btn primary" :disabled="busy" @click="save">
        <Icon v-if="busy" name="spinner" style="animation: spin 1s linear infinite" />
        {{ busy ? '保存中...' : '保存' }}
      </button>
    </div>
  </ModalShell>
</template>

<style scoped>
.pm-row {
  display: flex; align-items: center; gap: 10px;
  padding: 8px 10px; border-radius: 8px; cursor: pointer;
  border: 1.5px solid var(--border); transition: all 0.15s;
}
.pm-row:hover { border-color: var(--accent-soft); background: var(--surface-hover); }
.pm-row.active { border-color: var(--accent); background: var(--accent-tint); }
.pm-avatar {
  width: 32px; height: 32px; border-radius: 50%; display: flex; align-items: center;
  justify-content: center; color: #fff; font-size: 13px; font-weight: 600; flex-shrink: 0;
}
.pm-name { font-size: 13px; font-weight: 500; color: var(--ink); }
.pm-email { font-size: 11.5px; color: var(--ink-mute); }
.pm-check { width: 20px; display: flex; align-items: center; justify-content: center; color: var(--accent); }
@keyframes spin { to { transform: rotate(360deg); } }
</style>
