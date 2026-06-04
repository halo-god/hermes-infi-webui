<script setup lang="ts">
/* Lightweight knowledge file picker modal for channel/Composer.
   Shows file list + content preview, returns selected file IDs. */
import { ref, computed } from "vue";
import ModalShell from "@/components/ModalShell.vue";
import Icon from "@/components/Icon.vue";
import type { Knowledge } from "@/types";

const props = defineProps<{
  items: Knowledge[];
  teamId: string;
}>();

const emit = defineEmits<{
  select: [ids: string[]];
  close: [];
}>();

const selected = ref<Set<string>>(new Set());
const search = ref("");

const filtered = computed(() => {
  if (!search.value.trim()) return props.items;
  const q = search.value.toLowerCase();
  return props.items.filter((k) => k.name.toLowerCase().includes(q));
});

function toggle(id: string) {
  if (selected.value.has(id)) selected.value.delete(id);
  else selected.value.add(id);
  selected.value = new Set(selected.value);
}

function confirm() {
  emit("select", [...selected.value]);
}

function fmtSize(b: number): string {
  return b >= 1048576 ? (b / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(b / 1024)) + " KB";
}
</script>

<template>
  <ModalShell title="引用知识库" :subtitle="`选择文件作为上下文发送给 AI（已选 ${selected.size} 个）`" :width="520" @close="emit('close')">
    <div class="kp-search">
      <Icon name="search" :size="13" />
      <input v-model="search" placeholder="搜索文件…" class="kp-search-input" autofocus />
    </div>

    <div class="kp-list">
      <div v-if="!filtered.length" class="kp-empty">没有找到文件</div>
      <button
        v-for="k in filtered"
        :key="k.id"
        class="kp-item"
        :class="{ active: selected.has(k.id) }"
        @click="toggle(k.id)"
      >
        <div class="kp-check">
          <Icon v-if="selected.has(k.id)" name="check" :size="12" />
        </div>
        <Icon name="doc" :size="16" style="flex-shrink: 0; color: var(--ink-mute)" />
        <div class="kp-info">
          <div class="kp-name">{{ k.name }}</div>
          <div class="kp-meta">{{ fmtSize(k.size_bytes) }} · {{ k.kind }} · {{ k.uploaded_by_name || "成员" }}</div>
        </div>
      </button>
    </div>

    <template #foot>
      <span class="kp-hint">选中的文件内容将作为上下文发送给 AI</span>
      <div style="display: flex; gap: 8px">
        <button class="btn" @click="emit('close')">取消</button>
        <button class="btn primary" :disabled="!selected.size" @click="confirm">引用 {{ selected.size }} 个文件</button>
      </div>
    </template>
  </ModalShell>
</template>

<style scoped>
.kp-search {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border: 1px solid var(--rule);
  border-radius: 8px;
  margin-bottom: 12px;
  background: var(--bg-canvas);
}
.kp-search-input {
  flex: 1;
  border: none;
  background: transparent;
  outline: none;
  font-size: 13px;
  color: var(--ink);
}
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
.kp-item:hover {
  background: var(--accent-tint);
}
.kp-item.active {
  background: rgba(184, 133, 42, 0.08);
  border-color: var(--accent-soft);
}
.kp-check {
  width: 18px;
  height: 18px;
  border: 1.5px solid var(--rule);
  border-radius: 4px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  color: var(--accent);
  transition: all 120ms;
}
.kp-item.active .kp-check {
  background: var(--accent);
  border-color: var(--accent);
  color: white;
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
.kp-meta {
  font-size: 11.5px;
  color: var(--ink-mute);
  margin-top: 2px;
}
.kp-hint {
  font-size: 12px;
  color: var(--ink-mute);
}
</style>
