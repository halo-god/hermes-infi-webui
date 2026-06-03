<script setup lang="ts">
import { ref, watch, computed, nextTick } from "vue";
import ModalShell from "@/components/ModalShell.vue";
import type { ConfirmationRequest } from "@/types";

const props = defineProps<{
  request?: ConfirmationRequest;
}>();

const emit = defineEmits<{
  close: [];
  respond: [choice: string];
}>();

const currentStep = ref(0);
const answers = ref<string[]>([]);
const freeText = ref("");
const textInput = ref<HTMLInputElement | null>(null);

const isMultiQuestion = computed(() => (props.request?.questions?.length || 0) > 1);

const currentQ = computed(() => {
  if (props.request?.questions?.length) {
    return props.request.questions[currentStep.value];
  }
  return null;
});

const currentOptions = computed(() => currentQ.value?.options || []);
const hasOptions = computed(() => currentOptions.value.length > 0);
const allowFreeText = computed(() => currentQ.value?.allow_free_text ?? !hasOptions.value);

const currentQuestion = computed(() => currentQ.value?.question || props.request?.question || "");

const totalSteps = computed(() => props.request?.questions?.length || 1);
const isLastStep = computed(() => currentStep.value >= totalSteps.value - 1);

watch(
  () => props.request,
  () => {
    currentStep.value = 0;
    answers.value = [];
    freeText.value = "";
  },
  { immediate: true }
);

watch(currentStep, () => {
  freeText.value = "";
  nextTick(() => textInput.value?.focus());
});

function selectOption(opt: string) {
  answers.value[currentStep.value] = opt;
  if (isMultiQuestion.value && !isLastStep.value) {
    currentStep.value++;
  } else {
    submitAll();
  }
}

function submitFreeText() {
  if (!freeText.value.trim()) return;
  selectOption(freeText.value.trim());
}

function submitAll() {
  if (!props.request?.questions?.length) return;
  if (props.request.questions.length === 1) {
    emit("respond", answers.value[0] || "跳过");
    return;
  }
  const parts = props.request.questions.map((q, i) => {
    const answer = answers.value[i] || "跳过";
    return `${q.question}: ${answer}`;
  });
  emit("respond", parts.join("; "));
}

function goBack() {
  if (currentStep.value > 0) currentStep.value--;
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    submitFreeText();
  }
}

const modalTitle = computed(() => {
  if (isMultiQuestion.value) return `问题 ${currentStep.value + 1} / ${totalSteps.value}`;
  return "需要确认";
});
</script>

<template>
  <ModalShell
    v-if="request"
    :title="modalTitle"
    :subtitle="currentQuestion"
    :width="480"
    @close="emit('close')"
  >
    <!-- Progress bar for multi-step -->
    <div v-if="isMultiQuestion" class="cf-progress">
      <div
        class="cf-progress-fill"
        :style="{ width: `${((currentStep + 1) / totalSteps) * 100}%` }"
      />
    </div>

    <div class="cf-body">
      <!-- Options list -->
      <div v-if="hasOptions" class="cf-options">
        <button
          v-for="opt in currentOptions"
          :key="opt"
          class="cf-option"
          @click="selectOption(opt)"
        >
          <span class="cf-option-dot" />
          <span>{{ opt }}</span>
        </button>
      </div>

      <!-- Text input (shown when no options, or options + allow_free_text) -->
      <div v-if="allowFreeText" class="cf-free">
        <input
          ref="textInput"
          v-model="freeText"
          type="text"
          :placeholder="hasOptions ? '或输入自定义内容...' : `回答 ${currentQuestion}...`"
          class="cf-input"
          @keydown="onKeydown"
        />
        <button class="btn primary" :disabled="!freeText.trim()" @click="submitFreeText">
          确认
        </button>
      </div>
    </div>

    <template #foot>
      <span v-if="isMultiQuestion" class="cf-foot-hint">
        {{ isLastStep ? "选择后将提交所有答案" : `还有 ${totalSteps - currentStep - 1} 个问题` }}
      </span>
      <span v-else />
      <div class="cf-foot-actions">
        <button class="btn" @click="emit('respond', '跳过')">跳过</button>
        <button v-if="isMultiQuestion && currentStep > 0" class="btn" @click="goBack">
          上一步
        </button>
      </div>
    </template>
  </ModalShell>
</template>

<style scoped>
.cf-progress {
  height: 3px;
  background: var(--rule-soft);
  position: relative;
  margin: 0 0 16px;
}
.cf-progress-fill {
  height: 100%;
  background: var(--accent);
  transition: width 300ms cubic-bezier(0.4, 0, 0.2, 1);
  border-radius: 2px;
}
.cf-body {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.cf-options {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.cf-option {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border-radius: var(--r-sm);
  font-size: 13.5px;
  color: var(--ink);
  text-align: left;
  cursor: pointer;
  transition: background 120ms, border-color 120ms;
  border: 1px solid transparent;
  background: transparent;
}
.cf-option:hover {
  background: var(--accent-tint);
  border-color: var(--accent-soft);
}
.cf-option-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--ink-faint);
  flex-shrink: 0;
  transition: background 120ms;
}
.cf-option:hover .cf-option-dot {
  background: var(--accent);
}
.cf-free {
  display: flex;
  gap: 8px;
  align-items: center;
}
.cf-input {
  flex: 1;
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
.cf-input:focus {
  border-color: var(--accent-soft);
  box-shadow: 0 0 0 3px rgba(184, 133, 42, 0.08);
}
.cf-input::placeholder {
  color: var(--ink-faint);
}
.cf-foot-hint {
  font-size: 12px;
  color: var(--ink-mute);
}
.cf-foot-actions {
  display: flex;
  gap: 8px;
}
.btn {
  height: 34px;
  padding: 0 14px;
  border-radius: var(--r-sm);
  font-size: 13px;
  font-weight: 500;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  transition: background 120ms, color 120ms, border-color 120ms;
  background: var(--bg-panel);
  border: 1px solid var(--rule);
  color: var(--ink-soft);
  cursor: pointer;
}
.btn:hover {
  background: var(--bg-hover);
  border-color: var(--ink-faint);
  color: var(--ink);
}
.btn.primary {
  background: var(--ink);
  border-color: var(--ink);
  color: var(--ink-on-accent);
}
.btn.primary:hover {
  background: var(--ink-soft);
}
.btn.primary:disabled {
  opacity: 0.5;
  cursor: default;
}
</style>
