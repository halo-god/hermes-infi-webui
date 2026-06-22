<script setup lang="ts">
import { computed, onMounted, watch } from "vue";
import { darkTheme, NConfigProvider, NMessageProvider, NDialogProvider } from "naive-ui";
import { useAuthStore } from "@/stores/auth";
import { useBrandingStore } from "@/stores/branding";
import { useTheme } from "@/composables/useTheme";
import { usePresence } from "@/composables/usePresence";
import { useNotificationStream } from "@/composables/useNotificationStream";
import { useI18n } from "vue-i18n";

const auth = useAuthStore();
const branding = useBrandingStore();
const { theme } = useTheme();
const { startHeartbeat, stopHeartbeat } = usePresence();
const notifyStream = useNotificationStream();
const { t } = useI18n();

const naiveTheme = computed(() => (theme.value === "dark" ? darkTheme : null));
const themeOverrides = computed(() => ({
  common: {
    ...branding.accentOverrides,
    borderRadius: "10px",
    borderRadiusSmall: "6px",
    fontFamily: '"Inter", "Noto Sans SC", system-ui, -apple-system, sans-serif',
  },
}));

const bootMark = computed(() => {
  if (branding.logoUrl) return null;
  return (branding.shortName || "H").trim().charAt(0) || "H";
});

onMounted(async () => {
  // Branding is public — fetch in parallel with session restore so the
  // document title / favicon / accent are correct from the first paint.
  void branding.fetchBranding();
  await auth.bootstrap();
  // Start heartbeat after bootstrap confirms user is authenticated
  if (auth.user) {
    startHeartbeat();
    notifyStream.start();
  }
});

// Also handle login/logout transitions
watch(() => auth.user, (user, oldUser) => {
  if (user && !oldUser) {
    startHeartbeat();
    notifyStream.start();
  } else if (!user && oldUser) {
    stopHeartbeat();
    notifyStream.stop();
  }
});
</script>

<template>
  <NConfigProvider :theme="naiveTheme" :theme-overrides="themeOverrides">
    <NMessageProvider>
      <NDialogProvider>
        <router-view v-if="auth.ready" />
        <div v-else class="boot-screen">
          <img v-if="branding.logoUrl" class="boot-mark-img" :src="branding.logoUrl" alt="" />
          <div v-else class="boot-mark">{{ bootMark }}</div>
          <div class="boot-text">{{ t('boot.preparing', { brand: branding.shortName }) }}</div>
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
.boot-mark-img {
  width: 52px;
  height: 52px;
  border-radius: 13px;
  object-fit: contain;
}
.boot-text {
  font-family: var(--font-serif);
  font-style: italic;
  color: var(--ink-mute);
}
</style>
