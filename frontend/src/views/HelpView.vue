<script setup lang="ts">
/* 帮助中心 — 图文说明各功能模块，布局对齐 AdminView/FeedbackView 的
   stage + admin-hero + admin-tabs + admin-body 模式。 */
import { computed, ref } from "vue";
import Icon from "@/components/Icon.vue";
import { useAuthStore } from "@/stores/auth";
import { useBrandingStore } from "@/stores/branding";
import HelpGettingStarted from "@/components/help/HelpGettingStarted.vue";
import HelpChat from "@/components/help/HelpChat.vue";
import HelpCollaboration from "@/components/help/HelpCollaboration.vue";
import HelpTeamsProjects from "@/components/help/HelpTeamsProjects.vue";
import HelpAssistantsMemory from "@/components/help/HelpAssistantsMemory.vue";
import HelpFilesNotifications from "@/components/help/HelpFilesNotifications.vue";
import HelpProductivity from "@/components/help/HelpProductivity.vue";
import HelpAdmin from "@/components/help/HelpAdmin.vue";
import HelpFaq from "@/components/help/HelpFaq.vue";

const auth = useAuthStore();
const branding = useBrandingStore();

const BASE_TABS = [
  { id: "start", label: "快速开始", component: HelpGettingStarted },
  { id: "chat", label: "智能对话", component: HelpChat },
  { id: "collab", label: "协作与圆桌", component: HelpCollaboration },
  { id: "teams", label: "团队与项目", component: HelpTeamsProjects },
  { id: "memory", label: "助手与记忆", component: HelpAssistantsMemory },
  { id: "files", label: "文件与通知", component: HelpFilesNotifications },
  { id: "tools", label: "效率工具", component: HelpProductivity },
];
const ADMIN_TAB = { id: "admin", label: "管理后台", component: HelpAdmin };
const FAQ_TAB = { id: "faq", label: "常见问题", component: HelpFaq };

const tabs = computed(() =>
  auth.isAdmin ? [...BASE_TABS, ADMIN_TAB, FAQ_TAB] : [...BASE_TABS, FAQ_TAB],
);

const tab = ref("start");
const activeSection = computed(() => tabs.value.find((t) => t.id === tab.value)?.component ?? HelpGettingStarted);
</script>

<template>
  <div class="stage">
    <div class="admin-hero">
      <div class="admin-hero-row">
        <span class="admin-badge"><Icon name="help" :size="11" /> HELP</span>
        <span style="font-size: 11.5px; color: var(--ink-mute); font-family: var(--font-mono)">{{ branding.tenantName }}</span>
      </div>
      <h1 class="admin-title">帮助<em>中心</em></h1>
      <div class="admin-sub">了解 Hermes 的每一个功能，快速上手你的协作场景。</div>
      <div class="admin-tabs">
        <button
          v-for="t in tabs"
          :key="t.id"
          class="team-tab"
          :class="{ active: tab === t.id }"
          @click="tab = t.id"
        >
          {{ t.label }}
        </button>
      </div>
    </div>

    <div class="admin-body">
      <component :is="activeSection" />
    </div>
  </div>
</template>
