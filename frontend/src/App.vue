<script setup lang="ts">
import { computed, onMounted } from "vue";
import { darkTheme, NConfigProvider, NMessageProvider, NDialogProvider } from "naive-ui";
import { useAuthStore } from "@/stores/auth";
import { useTheme } from "@/composables/useTheme";

const auth = useAuthStore();
const { theme } = useTheme();

const naiveTheme = computed(() => (theme.value === "dark" ? darkTheme : null));
const themeOverrides = computed(() => ({
  common: {
    primaryColor: "#b8852a",
    primaryColorHover: "#d4a04a",
    primaryColorPressed: "#8a6418",
    primaryColorSuppl: "#d4a04a",
    borderRadius: "10px",
    borderRadiusSmall: "6px",
    fontFamily: '"Inter", "Noto Sans SC", system-ui, -apple-system, sans-serif',
  },
}));

onMounted(() => auth.bootstrap());
</script>

<template>
  <NConfigProvider :theme="naiveTheme" :theme-overrides="themeOverrides">
    <NMessageProvider>
      <NDialogProvider>
        <router-view v-if="auth.ready" />
        <div v-else class="boot-screen">
          <div class="boot-mark">H</div>
          <div class="boot-text">信使正在准备…</div>
        </div>
      </NDialogProvider>
    </NMessageProvider>
  </NConfigProvider>
</template>

<style scoped>
.boot-screen {
  height: 100vh;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 14px;
  background: var(--bg-canvas);
}
.boot-mark {
  width: 52px;
  height: 52px;
  border-radius: 13px;
  background: linear-gradient(180deg, #2a241a, #15110b);
  color: var(--accent);
  display: grid;
  place-items: center;
  font-family: var(--font-serif);
  font-size: 26px;
  font-weight: 600;
}
.boot-text {
  font-family: var(--font-serif);
  font-style: italic;
  color: var(--ink-mute);
}
</style>
