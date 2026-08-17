<script setup lang="ts">
import { nextTick, ref, watch } from "vue";
import ModalShell from "@/components/ModalShell.vue";
import { usePromptModal } from "@/composables/usePromptModal";

const { state, settle } = usePromptModal();
const value = ref("");
const error = ref("");
const textInput = ref<HTMLInputElement | null>(null);

watch(
  () => state.open,
  async (open) => {
    if (open) {
      value.value = state.opts.initial || "";
      error.value = "";
      await nextTick();
      textInput.value?.focus();
    }
  }
);

function submit() {
  if (state.opts.mode === "confirm") {
    settle("ok");
    return;
  }
  const v = value.value.trim();
  if (!v) {
    error.value = "不能为空";
    return;
  }
  const err = state.opts.validate ? state.opts.validate(v) : null;
  if (err) {
    error.value = err;
    return;
  }
  settle(v);
}

function onKey(e: KeyboardEvent) {
  if (e.key === "Enter") {
    e.preventDefault();
    submit();
  } else if (e.key === "Escape") {
    settle(null);
  }
}
</script>

<template>
  <ModalShell
    v-if="state.open"
    :title="state.opts.title"
    :subtitle="state.opts.mode === 'confirm' ? state.opts.message : state.opts.label"
    :width="480"
    @close="settle(null)"
  >
    <template v-if="state.opts.mode !== 'confirm'">
      <input
        ref="textInput"
        v-model="value"
        type="text"
        class="pm-input"
        :placeholder="state.opts.placeholder"
        @keydown="onKey"
      />
      <div v-if="error" class="pm-error">{{ error }}</div>
    </template>
    <template #foot>
      <div class="pm-foot">
        <button class="pm-btn" @click="settle(null)">取消</button>
        <button class="pm-btn pm-primary" @click="submit">
          {{ state.opts.confirmText || "确定" }}
        </button>
      </div>
    </template>
  </ModalShell>
</template>

<style scoped>
.pm-input {
  width: 100%;
  height: 40px;
  padding: 0 14px;
  border-radius: var(--r-sm);
  border: 1px solid var(--rule);
  background: var(--bg-panel);
  color: var(--ink);
  font-size: 14px;
  outline: none;
  transition: border-color 150ms, box-shadow 150ms;
}
.pm-input:focus {
  border-color: var(--accent-soft);
  box-shadow: 0 0 0 3px rgba(184, 133, 42, 0.08);
}
.pm-input::placeholder {
  color: var(--ink-faint);
}
.pm-error {
  margin-top: 8px;
  font-size: 12.5px;
  color: var(--danger);
}
.pm-foot {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
.pm-btn {
  height: 34px;
  padding: 0 14px;
  border-radius: var(--r-sm);
  font-size: 13px;
  font-weight: 500;
  background: var(--bg-panel);
  border: 1px solid var(--rule);
  color: var(--ink-soft);
  cursor: pointer;
  transition: background 120ms, color 120ms, border-color 120ms;
}
.pm-btn:hover {
  background: var(--bg-hover);
  border-color: var(--ink-faint);
  color: var(--ink);
}
.pm-primary {
  background: var(--ink);
  border-color: var(--ink);
  color: var(--ink-on-accent);
}
.pm-primary:hover {
  background: var(--ink-soft);
}
</style>
