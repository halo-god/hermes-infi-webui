<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useRoute } from "vue-router";
import { conversationsApi } from "@/api/conversations";
import { renderMarkdown } from "@/utils/markdown";
import { useBrandingStore } from "@/stores/branding";
import type { ConversationDetail } from "@/types";

const route = useRoute();
const branding = useBrandingStore();

const convo = ref<ConversationDetail | null>(null);
const loading = ref(true);
const error = ref("");

function roleLabel(role: string, agentId: string | null): string {
  if (role === "user") return "用户";
  if (role === "system") return "系统";
  return agentId || "AI";
}

function fmtTime(iso: string): string {
  return new Date(iso).toLocaleString("zh-CN");
}

onMounted(async () => {
  const id = route.params.id as string;
  try {
    convo.value = await conversationsApi.getShared(id);
  } catch (e: unknown) {
    error.value = (e as { response?: { data?: { detail?: string } } }).response?.data?.detail || "分享链接不存在或已失效";
  } finally {
    loading.value = false;
  }
});
</script>

<template>
  <div class="shared-page">
    <div class="shared-head">
      <span class="shared-brand">{{ branding.tenantName }}</span>
      <span class="shared-sub">只读分享视图</span>
    </div>

    <div v-if="loading" class="shared-state">加载中…</div>
    <div v-else-if="error" class="shared-state">{{ error }}</div>
    <template v-else-if="convo">
      <h1 class="shared-title">{{ convo.title }}</h1>
      <div class="shared-list">
        <div v-for="m in convo.messages" :key="m.id" class="msg" :class="m.role">
          <div class="msg-body">
            <div class="msg-name">{{ roleLabel(m.role, m.agent_id) }}</div>
            <div class="msg-bubble">
              <div class="md-body" v-html="renderMarkdown(m.content.text || '')" />
            </div>
            <div class="msg-time">{{ fmtTime(m.created_at) }}</div>
          </div>
        </div>
        <div v-if="!convo.messages.length" class="shared-state">这个会话还没有消息。</div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.shared-page {
  max-width: 720px;
  margin: 0 auto;
  padding: 32px 20px 60px;
  min-height: 100vh;
}
.shared-head {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 20px;
}
.shared-brand {
  font-weight: 600;
  color: var(--ink);
}
.shared-sub {
  font-size: 12px;
  color: var(--ink-mute);
}
.shared-title {
  font-size: 20px;
  color: var(--ink);
  margin: 0 0 20px;
}
.shared-state {
  padding: 40px;
  text-align: center;
  color: var(--ink-mute);
  font-size: 13.5px;
}
.shared-list {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
</style>
