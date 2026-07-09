<script setup lang="ts">
/* "移动到" picker for team knowledge files/folders — lists eligible target
   folders (excluding the item itself and, if it's a folder, all of its
   descendants, to avoid orphaning a subtree by nesting a folder inside itself). */
import { ref, computed, onMounted } from "vue";
import ModalShell from "@/components/ModalShell.vue";
import Icon from "@/components/Icon.vue";
import { teamsApi } from "@/api/teams";
import type { Knowledge } from "@/types";

const props = defineProps<{
  teamId: string;
  item: Knowledge;
}>();
const emit = defineEmits<{ close: []; moved: [] }>();

const loading = ref(true);
const moving = ref(false);
const allItems = ref<Knowledge[]>([]);

onMounted(async () => {
  try {
    allItems.value = await teamsApi.listKnowledge(props.teamId, undefined, true);
  } finally {
    loading.value = false;
  }
});

const excludedIds = computed<Set<string>>(() => {
  const ex = new Set<string>([props.item.id]);
  if (!props.item.is_folder) return ex;
  const byParent = new Map<string, Knowledge[]>();
  for (const k of allItems.value) {
    if (!k.folder_id) continue;
    if (!byParent.has(k.folder_id)) byParent.set(k.folder_id, []);
    byParent.get(k.folder_id)!.push(k);
  }
  const stack = [props.item.id];
  while (stack.length) {
    const id = stack.pop()!;
    for (const child of byParent.get(id) || []) {
      if (!ex.has(child.id)) {
        ex.add(child.id);
        stack.push(child.id);
      }
    }
  }
  return ex;
});

const folderOptions = computed(() => {
  const byId = new Map(allItems.value.map((k) => [k.id, k]));
  function depth(k: Knowledge): number {
    let d = 0;
    let cur: Knowledge | undefined = k;
    while (cur?.folder_id) {
      const parent = byId.get(cur.folder_id);
      if (!parent) break;
      d++;
      cur = parent;
    }
    return d;
  }
  return allItems.value
    .filter((k) => k.is_folder && !excludedIds.value.has(k.id))
    .map((k) => ({ id: k.id, name: k.name, depth: depth(k) }))
    .sort((a, b) => a.name.localeCompare(b.name));
});

async function moveTo(folderId: string | null) {
  if (moving.value) return;
  moving.value = true;
  try {
    await teamsApi.moveKnowledge(props.teamId, props.item.id, folderId);
    emit("moved");
  } finally {
    moving.value = false;
  }
}
</script>

<template>
  <ModalShell title="移动到" :subtitle="`移动「${item.name}」`" :width="420" @close="emit('close')">
    <div v-if="loading" style="padding: 24px; text-align: center; color: var(--ink-mute); font-size: 13px">加载中…</div>
    <div v-else class="kp-list">
      <button class="kp-item" :disabled="!item.folder_id || moving" @click="moveTo(null)">
        <Icon name="home" :size="15" style="flex-shrink: 0; color: var(--ink-mute)" />
        <div class="kp-info"><div class="kp-name">根目录</div></div>
      </button>
      <button
        v-for="f in folderOptions"
        :key="f.id"
        class="kp-item"
        :disabled="moving"
        :style="{ paddingLeft: 12 + f.depth * 18 + 'px' }"
        @click="moveTo(f.id)"
      >
        <Icon name="folder" :size="15" style="flex-shrink: 0; color: var(--ink-mute)" />
        <div class="kp-info"><div class="kp-name">{{ f.name }}</div></div>
      </button>
      <div v-if="!folderOptions.length" class="kp-empty">没有其他可用文件夹</div>
    </div>
    <template #foot>
      <button class="btn" @click="emit('close')">取消</button>
    </template>
  </ModalShell>
</template>

<style scoped>
.kp-list {
  max-height: 360px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.kp-empty {
  padding: 32px;
  text-align: center;
  color: var(--ink-mute);
  font-size: 13px;
}
.kp-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border-radius: 8px;
  cursor: pointer;
  transition: background 120ms;
  border: 1px solid transparent;
  background: transparent;
  text-align: left;
  width: 100%;
}
.kp-item:hover:not(:disabled) {
  background: var(--accent-tint);
}
.kp-item:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}
.kp-info {
  flex: 1;
  min-width: 0;
}
.kp-name {
  font-size: 13.5px;
  color: var(--ink);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
</style>
