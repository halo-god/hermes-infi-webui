<script setup lang="ts">
/* 帮助按钮 — 顶栏图标，点击直接跳转到帮助中心页面。 */
import { useRouter } from "vue-router";
import Icon from "@/components/Icon.vue";

const router = useRouter();

function goHelp() {
  router.push("/help");
}
</script>

<template>
  <button class="icon-btn" title="帮助中心" @click="goHelp">
    <Icon name="help" :size="16" />
  </button>
</template>
