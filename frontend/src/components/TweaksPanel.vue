<script setup lang="ts">
/* 1:1 port of the prototype Tweaks panel (氣質 Atmosphere / 密度 Density /
   语气 Voice). Atmosphere + density drive body[data-atmos]/[data-density],
   which prototype.css already styles for all 5 themes. Persisted to localStorage. */
import { onUnmounted, reactive, watch } from "vue";
import Icon from "@/components/Icon.vue";

defineProps<{ open: boolean }>();
defineEmits<{ close: [] }>();

const KEY = "hermes.tweaks";
const tweaks = reactive({ atmos: "letter", density: "normal", voice: "warm" });

const saved = localStorage.getItem(KEY);
if (saved) {
  try {
    Object.assign(tweaks, JSON.parse(saved));
  } catch {
    /* ignore */
  }
}
apply();

function apply() {
  document.body.dataset.atmos = tweaks.atmos;
  document.body.dataset.density = tweaks.density;
  document.body.dataset.voice = tweaks.voice;
  localStorage.setItem(KEY, JSON.stringify(tweaks));
  // Keep the AppLayout topbar icon in sync (DOM dataset is not reactive).
  window.dispatchEvent(new CustomEvent("hermes:atmos", { detail: tweaks.atmos }));
}
watch(tweaks, apply, { deep: true });

// The AppLayout topbar can also cycle the atmos; without this listener the
// panel's internal tweaks.atmos goes stale and its next watch→apply() would
// silently roll the theme back to the pre-cycle value.
function onExternalAtmos(e: Event) {
  const v = (e as CustomEvent<string>).detail;
  if (typeof v === "string" && v && tweaks.atmos !== v) tweaks.atmos = v;
}
window.addEventListener("hermes:atmos", onExternalAtmos);
onUnmounted(() => window.removeEventListener("hermes:atmos", onExternalAtmos));

const densityHint: Record<string, string> = { loose: "从容铺开", normal: "正合适", tight: "不浪费每一寸" };
const voiceHint: Record<string, string> = { classical: "简练有古意", warm: "像同事在说话", engineering: "只说事实" };
</script>

<template>
  <div class="twk" :class="{ open }">
    <div class="twk-head">
      <div class="twk-title">调整 · <em style="font-style: italic; color: var(--accent-deep)">Tweaks</em></div>
      <button class="twk-close" @click="$emit('close')"><Icon name="close" /></button>
    </div>

    <div class="twk-section">
      <div class="twk-section-label"><span>密度 · Density</span><span class="twk-hint">{{ densityHint[tweaks.density] }}</span></div>
      <div class="twk-presets">
        <button class="twk-preset" :class="{ active: tweaks.density === 'loose' }" @click="tweaks.density = 'loose'"><div class="swatch density-loose"><i></i><i></i><i></i></div>舒展</button>
        <button class="twk-preset" :class="{ active: tweaks.density === 'normal' }" @click="tweaks.density = 'normal'"><div class="swatch density-normal"><i></i><i></i><i></i><i></i></div>标准</button>
        <button class="twk-preset" :class="{ active: tweaks.density === 'tight' }" @click="tweaks.density = 'tight'"><div class="swatch density-tight"><i></i><i></i><i></i><i></i><i></i><i></i></div>紧凑</button>
      </div>
    </div>

    <div class="twk-section">
      <div class="twk-section-label"><span>语气 · Voice</span><span class="twk-hint">{{ voiceHint[tweaks.voice] }}</span></div>
      <div class="twk-presets">
        <button class="twk-preset" :class="{ active: tweaks.voice === 'classical' }" @click="tweaks.voice = 'classical'"><div class="swatch voice">辞</div>古典</button>
        <button class="twk-preset" :class="{ active: tweaks.voice === 'warm' }" @click="tweaks.voice = 'warm'"><div class="swatch voice warm">嗨</div>亲切</button>
        <button class="twk-preset" :class="{ active: tweaks.voice === 'engineering' }" @click="tweaks.voice = 'engineering'"><div class="swatch voice engineering">$ —</div>工程</button>
      </div>
    </div>
  </div>
</template>
