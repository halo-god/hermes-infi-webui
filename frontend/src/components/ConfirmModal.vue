<script setup lang="ts">
import { ref, watch, computed, nextTick, onMounted, onUnmounted } from "vue";
import ModalShell from "@/components/ModalShell.vue";
import type { ClarifyOption, ConfirmationRequest } from "@/types";

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

// Normalize option to structured form
function normalizeOpt(opt: string | ClarifyOption): ClarifyOption {
  if (typeof opt === "string") return { label: opt };
  return opt;
}

function getRiskColor(risk?: string): string {
  if (risk === "high") return "var(--error)";
  if (risk === "medium") return "var(--warning)";
  return "var(--ink-faint)";
}

watch(
  () => props.request,
  () => {
    currentStep.value = 0;
    answers.value = [];
    freeText.value = "";
  },
  { immediate: true },
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
    emit("respond", answers.value[0] || "skip");
    return;
  }
  const parts = props.request.questions.map((q, i) => {
    const answer = answers.value[i] || "skip";
    return `${q.question}: ${answer}`;
  });
  emit("respond", parts.join("; "));
}

function goBack() {
  if (currentStep.value > 0) currentStep.value--;
}

// Keyboard shortcuts: 1-9 to select option, Enter for free text
function onKeydown(e: KeyboardEvent) {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    submitFreeText();
    return;
  }
  // Number keys 1-9 for quick select
  const num = parseInt(e.key, 10);
  if (num >= 1 && num <= currentOptions.value.length) {
    e.preventDefault();
    const opt = normalizeOpt(currentOptions.value[num - 1]);
    selectOption(opt.label);
  }
  // Escape to close
  if (e.key === "Escape") {
    emit("close");
  }
}

onMounted(() => {
  document.addEventListener("keydown", onKeydown);
});
onUnmounted(() => {
  document.removeEventListener("keydown", onKeydown);
});

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
    :width="520"
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
          v-for="(rawOpt, idx) in currentOptions"
          :key="normalizeOpt(rawOpt).label"
          class="cf-option"
          :class="{ 'cf-option--high': normalizeOpt(rawOpt).risk === 'high', 'cf-option--medium': normalizeOpt(rawOpt).risk === 'medium' }"
          @click="selectOption(normalizeOpt(rawOpt).label)"
        >
          <span class="cf-option-key">{{ idx + 1 }}</span>
          <span class="cf-option-dot" :style="{ background: getRiskColor(normalizeOpt(rawOpt).risk) }" />
          <div class="cf-option-content">
            <span class="cf-option-label">{{ normalizeOpt(rawOpt).label }}</span>
            <span v-if="normalizeOpt(rawOpt).description" class="cf-option-desc">{{ normalizeOpt(rawOpt).description }}</span>
          </div>
        </button>
      </div>

      <!-- Text input -->
      <div v-if="allowFreeText" class="cf-free">
        <input
          ref="textInput"
          v-model="freeText"
          type="text"
          :placeholder="hasOptions ? '或输入自定义内容...' : `回答 ${currentQuestion}...`"
          class="cf-input"
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
      <span v-else class="cf-foot-hint">1-9 快速选择</span>
      <div class="cf-foot-actions">
        <button class="btn" @click="emit('respond', 'skip')">跳过</button>
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
.cf-option--high:hover {
  background: rgba(220, 50, 50, 0.06);
  border-color: rgba(220, 50, 50, 0.2);
}
.cf-option--medium:hover {
  background: rgba(200, 150, 30, 0.06);
  border-color: rgba(200, 150, 30, 0.2);
}
.cf-option-key {
  width: 20px;
  height: 20px;
  border-radius: 4px;
  background: var(--bg-panel);
  border: 1px solid var(--rule);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 11px;
  font-weight: 600;
  color: var(--ink-mute);
  flex-shrink: 0;
}
.cf-option:hover .cf-option-key {
  background: var(--accent-tint);
  border-color: var(--accent-soft);
  color: var(--accent);
}
.cf-option-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  flex-shrink: 0;
  transition: background 120ms;
}
.cf-option:hover .cf-option-dot {
  background: var(--accent) !important;
}
.cf-option-content {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.cf-option-label {
  font-weight: 500;
}
.cf-option-desc {
  font-size: 12px;
  color: var(--ink-mute);
  line-height: 1.4;
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
