<script setup lang="ts">
/* 定时任务页 — mirrors the prototype schedule view (hermes-app.js).
   Static demo data; real scheduling API is a future milestone. */
import { useRouter } from "vue-router";
import Icon from "@/components/Icon.vue";
import { useChatStore } from "@/stores/chat";
import { useBrandingStore } from "@/stores/branding";

const router = useRouter();
const chat = useChatStore();
const branding = useBrandingStore();

function agentById(id: string) {
  const p = chat.profiles.find((pp) => pp.default_agent_id === id);
  return { label: p?.name || id, color: p?.color || branding.accent, icon: p?.icon || "sparkle" };
}

const tasks = [
  { id: "1", name: "每周五 17:00 — 生成周报草稿", agent: "cowork", next: "本周五 17:00", enabled: true },
  { id: "2", name: "每天 09:30 — 拉取昨日关键指标", agent: "analyst", next: "明早 09:30", enabled: true },
  { id: "3", name: "每月 1 号 — 整理上月发票", agent: "word", next: "12月1日 10:00", enabled: false },
];
</script>

<template>
  <div class="stage">
    <div class="landing" style="padding-top: 80px">
      <div class="landing-inner">
        <h1 class="hello" style="font-size: 34px"><em>定时任务</em></h1>
        <div class="hello-sub">让{{ branding.shortName }}在指定时刻替你跑腿。</div>

        <div style="width: 100%; max-width: 640px; margin-top: 24px; display: flex; flex-direction: column; gap: 10px">
          <div
            v-for="t in tasks"
            :key="t.id"
            style="background: var(--bg-panel); border: 1px solid var(--rule); border-radius: 14px; padding: 14px 16px; display: flex; align-items: center; gap: 12px"
            :style="{ opacity: t.enabled ? 1 : 0.55 }"
          >
            <div
              class="agent-icon"
              :style="{ background: agentById(t.agent).color || branding.accent, width: '30px', height: '30px', borderRadius: '8px', flexShrink: '0' }"
            >
              <Icon :name="agentById(t.agent).icon || 'sparkle'" :size="14" />
            </div>
            <div class="flex-1-min">
              <div style="font-weight: 600; color: var(--ink); font-size: 13.5px">{{ t.name }}</div>
              <div style="font-size: 11.5px; color: var(--ink-mute); margin-top: 3px">
                由 {{ agentById(t.agent).label }} 执行 · 下次：{{ t.next }}
              </div>
            </div>
            <div style="display: flex; align-items: center; gap: 8px">
              <span
                v-if="!t.enabled"
                style="font-size: 11px; color: var(--ink-faint); background: var(--bg-canvas); border: 1px solid var(--rule); border-radius: 5px; padding: 2px 7px"
              >已暂停</span>
              <button class="icon-btn" title="设置"><Icon name="settings" :size="14" /></button>
            </div>
          </div>

          <button
            class="btn primary"
            style="align-self: flex-start; display: inline-flex; align-items: center; gap: 5px; margin-top: 6px"
          >
            <Icon name="plus" :size="13" /> 新建定时任务
          </button>

          <button
            @click="router.push('/')"
            style="margin-top: 4px; align-self: flex-start; color: var(--ink-mute); font-size: 12px; display: inline-flex; align-items: center; gap: 4px; background: none; border: none; cursor: pointer; padding: 0"
          >
            <Icon name="back" :size="12" /> 返回
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
