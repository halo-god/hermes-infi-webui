<script setup lang="ts">
import { ref, watch, computed, nextTick } from "vue";
import type { ConfirmationRequest } from "@/types";

const props = withDefaults(
  defineProps<{
    request?: ConfirmationRequest;
    title?: string;
    message?: string;
    confirmText?: string;
    danger?: boolean;
  }>(),
  { title: "确认操作", confirmText: "确认", danger: false }
);

const emit = defineEmits<{
  close: [];
  confirm: [];
  respond: [choice: string];
}>();

const currentStep = ref(0);
const answers = ref<string[]>([]);
const freeText = ref("");
const textInput = ref<HTMLInputElement | null>(null);

const isMultiQuestion = computed(() => (props.request?.questions?.length || 0) > 0);

const currentOptions = computed(() => {
  if (isMultiQuestion.value && props.request?.questions) {
    return props.request.questions[currentStep.value]?.options || [];
  }
  return props.request?.options || [];
});

const currentQuestion = computed(() => {
  if (isMultiQuestion.value && props.request?.questions) {
    return props.request.questions[currentStep.value]?.question || "";
  }
  return props.request?.question || "";
});

const totalSteps = computed(() => {
  if (isMultiQuestion.value && props.request?.questions) {
    return props.request.questions.length;
  }
  return 1;
});

const isLastStep = computed(() => currentStep.value >= totalSteps.value - 1);
const isFreeText = computed(() => currentOptions.value.length === 0);

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
  if (isFreeText.value) {
    nextTick(() => textInput.value?.focus());
  }
});

function selectOption(opt: string) {
  if (isMultiQuestion.value) {
    answers.value[currentStep.value] = opt;
    if (isLastStep.value) {
      submitAll();
    } else {
      currentStep.value++;
    }
  } else {
    emit("respond", opt);
  }
}

function submitFreeText() {
  if (!freeText.value.trim()) return;
  selectOption(freeText.value.trim());
}

function submitAll() {
  if (!props.request?.questions) return;
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
</script>

<template>
  <template v-if="request">
    <Teleport to="body">
      <div class="confirm-overlay" @click.self="emit('close')">
        <div class="confirm-modal">
          <div class="confirm-header">
            <span class="confirm-icon">🤔</span>
            <div>
              <div class="confirm-title">
                {{ isMultiQuestion ? `问题 ${currentStep + 1} / ${totalSteps}` : "需要您的确认" }}
              </div>
              <div class="confirm-sub">
                {{ request.question || "AI 在继续前需要您做出选择" }}
              </div>
            </div>
          </div>

          <div v-if="isMultiQuestion" class="progress-bar">
            <div class="progress-fill" :style="{ width: `${((currentStep + 1) / totalSteps) * 100}%` }" />
          </div>

          <div class="confirm-question">{{ currentQuestion }}</div>

          <!-- Options list -->
          <div v-if="!isFreeText" class="confirm-options">
            <button
              v-for="opt in currentOptions"
              :key="opt"
              class="confirm-option"
              @click="selectOption(opt)"
            >
              {{ opt }}
            </button>
          </div>

          <!-- Free text input -->
          <div v-else class="confirm-free-input">
            <input
              ref="textInput"
              v-model="freeText"
              type="text"
              :placeholder="`请输入 ${currentQuestion}...`"
              class="text-input"
              @keydown="onKeydown"
            />
            <button class="btn btn-primary" :disabled="!freeText.trim()" @click="submitFreeText">
              确认
            </button>
          </div>

          <div class="confirm-footer">
            <button class="btn" @click="emit('respond', 'deny')">跳过全部</button>
            <button v-if="isMultiQuestion && currentStep > 0" class="btn" @click="goBack">上一步</button>
          </div>
        </div>
      </div>
    </Teleport>
  </template>

  <template v-else>
    <Teleport to="body">
      <div class="confirm-overlay" @click.self="emit('close')">
        <div class="confirm-modal" style="max-width: 420px">
          <div class="confirm-question">{{ message }}</div>
          <div class="confirm-footer">
            <button class="btn" @click="emit('close')">取消</button>
            <button
              class="btn"
              :style="danger ? 'color:#fff;background:var(--danger);border-color:var(--danger)' : 'color:#fff;background:var(--accent);border-color:var(--accent)'"
              @click="emit('confirm')"
            >
              {{ confirmText }}
            </button>
          </div>
        </div>
      </div>
    </Teleport>
  </template>
</template>

<style scoped>
.confirm-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.45);
  backdrop-filter: blur(3px);
  z-index: 2000;
  display: flex;
  align-items: center;
  justify-content: center;
}
.confirm-modal {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 16px;
  box-shadow: var(--shadow-lg);
  width: min(520px, 92vw);
  padding: 28px;
}
.confirm-header {
  display: flex;
  align-items: center;
  gap: 14px;
  margin-bottom: 18px;
}
.confirm-icon { font-size: 28px; line-height: 1; }
.confirm-title { font-size: 16px; font-weight: 600; color: var(--ink); }
.confirm-sub { font-size: 12px; color: var(--ink-mute); margin-top: 2px; }
.progress-bar {
  height: 4px;
  background: var(--rule);
  border-radius: 2px;
  margin-bottom: 18px;
  overflow: hidden;
}
.progress-fill {
  height: 100%;
  background: var(--accent);
  border-radius: 2px;
  transition: width 300ms ease;
}
.confirm-question {
  font-size: 14px;
  font-weight: 500;
  color: var(--ink);
  line-height: 1.6;
  margin-bottom: 20px;
  padding: 14px 16px;
  background: var(--bg-panel);
  border-radius: 10px;
  border: 1px solid var(--rule);
}
.confirm-options {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-bottom: 18px;
}
.confirm-option {
  width: 100%;
  padding: 12px 16px;
  border-radius: 10px;
  border: 1.5px solid var(--rule);
  background: var(--bg-panel);
  color: var(--ink);
  font-size: 13.5px;
  text-align: left;
  cursor: pointer;
  transition: border-color 160ms, background 160ms;
}
.confirm-option:hover {
  border-color: var(--accent);
  background: var(--accent-tint);
  color: var(--accent-deep);
}
.confirm-free-input {
  display: flex;
  gap: 8px;
  margin-bottom: 18px;
}
.text-input {
  flex: 1;
  padding: 10px 14px;
  border-radius: 8px;
  border: 1.5px solid var(--rule);
  background: var(--bg-panel);
  color: var(--ink);
  font-size: 13.5px;
  outline: none;
  transition: border-color 160ms;
}
.text-input:focus {
  border-color: var(--accent);
}
.confirm-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  border-top: 1px solid var(--rule-soft);
  padding-top: 14px;
}
.btn {
  padding: 8px 16px;
  border-radius: 8px;
  border: 1px solid var(--rule);
  background: var(--surface);
  color: var(--ink);
  font-size: 13px;
  cursor: pointer;
  transition: all 160ms;
}
.btn:hover { border-color: var(--accent); }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.btn-primary {
  background: var(--accent);
  border-color: var(--accent);
  color: #fff;
}
.btn-primary:hover:not(:disabled) { opacity: 0.9; }
</style>
