<script setup lang="ts">
import { ref, watch, computed, nextTick } from "vue";
import Icon from "@/components/Icon.vue";
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
  <!-- AI Confirmation Mode -->
  <template v-if="request">
    <Teleport to="body">
      <div class="modal-scrim" @mousedown.self="emit('close')">
        <div class="modal" :style="{ maxWidth: '480px' }" role="dialog">
          <div class="modal-head">
            <div>
              <div class="modal-title">
                {{ isMultiQuestion ? `问题 ${currentStep + 1} / ${totalSteps}` : "需要确认" }}
              </div>
              <div v-if="request.question" class="modal-sub">{{ request.question }}</div>
            </div>
            <button class="modal-close" @click="emit('close')" aria-label="关闭">
              <Icon name="close" />
            </button>
          </div>

          <!-- Progress bar for multi-step -->
          <div v-if="isMultiQuestion" class="cf-progress">
            <div class="cf-progress-fill" :style="{ width: `${((currentStep + 1) / totalSteps) * 100}%` }" />
          </div>

          <div class="modal-body">
            <!-- Current question -->
            <div class="cf-question">{{ currentQuestion }}</div>

            <!-- Options list -->
            <div v-if="!isFreeText" class="cf-options">
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

            <!-- Free text input -->
            <div v-else class="cf-free">
              <input
                ref="textInput"
                v-model="freeText"
                type="text"
                :placeholder="`输入 ${currentQuestion}...`"
                class="cf-input"
                @keydown="onKeydown"
              />
            </div>
          </div>

          <div class="modal-foot">
            <span v-if="isMultiQuestion" class="np-foot-hint">
              {{ isLastStep ? "选择后将提交所有答案" : `还有 ${totalSteps - currentStep - 1} 个问题` }}
            </span>
            <span v-else />
            <div style="display: flex; gap: 8px">
              <button class="btn" @click="emit('respond', 'deny')">跳过</button>
              <button v-if="isMultiQuestion && currentStep > 0" class="btn" @click="goBack">上一步</button>
              <button
                v-if="isFreeText"
                class="btn primary"
                :disabled="!freeText.trim()"
                @click="submitFreeText"
              >
                确认
              </button>
            </div>
          </div>
        </div>
      </div>
    </Teleport>
  </template>

  <!-- Classic Dialog Mode -->
  <template v-else-if="message">
    <Teleport to="body">
      <div class="modal-scrim" @mousedown.self="emit('close')">
        <div class="modal" :style="{ maxWidth: '420px' }" role="dialog">
          <div class="modal-body">
            <div class="cf-body">
              <div class="cf-icon" :class="{ danger }">
                <Icon name="alert-triangle" />
              </div>
              <div>
                <div class="cf-msg">{{ message }}</div>
              </div>
            </div>
          </div>
          <div class="modal-foot">
            <span />
            <div style="display: flex; gap: 8px">
              <button class="btn" @click="emit('close')">取消</button>
              <button class="btn primary" :class="{ 'btn-danger': danger }" @click="emit('confirm')">
                {{ confirmText }}
              </button>
            </div>
          </div>
        </div>
      </div>
    </Teleport>
  </template>
</template>

<style scoped>
.cf-progress {
  height: 3px;
  background: var(--rule-soft);
  position: relative;
}
.cf-progress-fill {
  height: 100%;
  background: var(--accent);
  transition: width 300ms cubic-bezier(0.4, 0, 0.2, 1);
}
.cf-question {
  font-size: 14px;
  font-weight: 600;
  color: var(--ink);
  line-height: 1.5;
  padding: 12px 14px;
  background: var(--bg-canvas);
  border-radius: var(--r-sm);
  border: 1px solid var(--rule);
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
  transition: background 120ms;
  border: 1px solid transparent;
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
}
.cf-input {
  flex: 1;
  height: 36px;
  padding: 0 12px;
  border-radius: var(--r-sm);
  border: 1px solid var(--rule);
  background: var(--bg-panel);
  color: var(--ink);
  font-size: 13.5px;
  outline: none;
  transition: border-color 120ms, box-shadow 120ms;
}
.cf-input:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px rgba(184, 133, 42, 0.1);
}
.cf-input::placeholder {
  color: var(--ink-faint);
}
</style>
