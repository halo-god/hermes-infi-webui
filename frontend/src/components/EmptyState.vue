<script setup lang="ts">
defineProps<{
  /** Optional icon name (from Icon component). */
  icon?: string;
  /** Size variant. */
  size?: "sm" | "lg";
}>();
</script>

<template>
  <div :class="size === 'lg' ? 'empty-state-lg' : 'empty-state'">
    <slot />
  </div>
</template>
