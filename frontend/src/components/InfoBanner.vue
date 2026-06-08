<script setup lang="ts">
defineProps<{
  /** Banner variant for color accent. */
  variant?: "info" | "warn" | "success";
}>();
</script>

<template>
  <div class="info-banner" :class="variant && `info-banner--${variant}`">
    <slot />
  </div>
</template>

<style scoped>
.info-banner--warn {
  background: var(--accent-tint);
  border: 1px solid var(--accent-soft);
}
.info-banner--success {
  background: rgba(74, 122, 58, 0.08);
  border: 1px solid rgba(74, 122, 58, 0.2);
}
</style>
