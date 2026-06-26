<script setup lang="ts">
/* 反馈按钮 — 顶栏图标，点击直接跳转到反馈中心页面。 */
import { useRouter } from "vue-router";
import Icon from "@/components/Icon.vue";

const router = useRouter();

function goFeedback() {
  router.push("/feedback");
}
</script>

<template>
  <button class="icon-btn" title="反馈中心" @click="goFeedback">
    <Icon name="chat" :size="16" />
  </button>
</template>
