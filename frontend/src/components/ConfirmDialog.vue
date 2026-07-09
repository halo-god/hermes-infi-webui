<script setup lang="ts">
import ModalShell from "@/components/ModalShell.vue";

withDefaults(
  defineProps<{ title: string; message: string; confirmText?: string; danger?: boolean }>(),
  { confirmText: "确认" },
);
const emit = defineEmits<{ close: []; confirm: [] }>();
</script>

<template>
  <ModalShell :title="title" :width="420" @close="emit('close')">
    <div style="font-size: 13.5px; color: var(--ink); line-height: 1.6">{{ message }}</div>
    <template #foot>
      <div style="display: flex; gap: 8px; justify-content: flex-end; width: 100%">
        <button class="btn" @click="emit('close')">取消</button>
        <button :class="danger ? 'btn btn-danger' : 'btn primary'" @click="emit('confirm')">{{ confirmText }}</button>
      </div>
    </template>
  </ModalShell>
</template>
