<script setup lang="ts">
/* Member panel for group chats — styled like WorkspacePanel sidebar */
import { ref, computed, onMounted, watch } from 'vue';
import Icon from '@/components/Icon.vue';
import { conversationsApi } from '@/api/conversations';
import { profilesApi, type Profile } from '@/api/agents';
import type { GroupMember } from '@/types';

const props = defineProps<{
  conversationId: string;
  agents: { agent_id: string; name: string; color: string; icon: string }[];
  channelMode?: string;
}>();

const emit = defineEmits<{
  close: [];
  'update:channelMode': [mode: string];
}>();

const members = ref<GroupMember[]>([]);
const profiles = ref<Profile[]>([]);
const loading = ref(false);

// Collapsible sections
const expandedSections = ref<Set<string>>(new Set(['ai', 'human']));

function toggleSection(key: string) {
  if (expandedSections.value.has(key)) {
    expandedSections.value.delete(key);
  } else {
    expandedSections.value.add(key);
  }
}

const humanMembers = computed(() => members.value.filter((m) => m.user_id));
// Source of truth is the actual GroupMember roster (members.value), not
// props.agents (derived from Conversation.active_profile_ids) — the two can
// drift apart, and only members.value carries the real per-member auto_reply
// flag and the stable member id needed to toggle it.
const aiMembers = computed(() => {
  return members.value
    .filter((m) => m.agent_id)
    .map((m) => {
      const fallback = props.agents.find((a) => a.agent_id === m.agent_id);
      return {
        id: m.id,
        agentId: m.agent_id!,
        name: m.profile_name || fallback?.name || m.agent_id!,
        color: m.profile_color || fallback?.color || '#b8852a',
        icon: m.profile_icon || fallback?.icon || 'sparkle',
      };
    });
});

const channelModeOptions = [
  { value: 'mention', label: '@ 触发', desc: '被 @ 到的助才会回复，@ 所有AI 则全部回复' },
  { value: 'off', label: '关闭', desc: '仅人工对话' },
];

async function load() {
  loading.value = true;
  try {
    const [mem, prof] = await Promise.all([
      conversationsApi.getMembers(props.conversationId),
      profilesApi.list().catch(() => []),
    ]);
    members.value = mem;
    profiles.value = prof;
  } catch {
    /* ignore */
  } finally {
    loading.value = false;
  }
}

function getProfileForAgent(agentId: string) {
  return profiles.value.find((p) => p.default_agent_id === agentId);
}

onMounted(load);
watch(() => props.conversationId, load);
</script>

<template>
  <div class="mp-panel">
    <!-- Header -->
    <div class="mp-head">
      <span class="mp-title">成员</span>
      <span class="mp-count">{{ aiMembers.length + humanMembers.length }}</span>
      <div class="mp-actions">
        <button class="mp-x" @click="emit('close')"><Icon name="close" :size="14" /></button>
      </div>
    </div>

    <div v-if="loading" class="mp-empty">加载中…</div>

    <template v-else>
      <div class="mp-body">
        <!-- AI Section -->
        <div class="mp-section">
          <button class="mp-dir-btn" @click="toggleSection('ai')">
            <span class="mp-dir-arrow" :class="{ expanded: expandedSections.has('ai') }">
              <Icon name="chevron_right" :size="10" />
            </span>
            <Icon name="sparkle" :size="12" />
            <span class="mp-dir-name">AI 助手</span>
            <span class="mp-dir-count">{{ aiMembers.length }}</span>
          </button>
          <template v-if="expandedSections.has('ai')">
            <div v-for="a in aiMembers" :key="a.id" class="mp-item">
              <div class="mp-avatar" :style="{ background: a.color || '#b8852a' }">
                <Icon :name="a.icon || 'sparkle'" :size="12" />
              </div>
              <div class="mp-info">
                <div class="mp-name">{{ a.name }}</div>
                <div class="mp-sub">{{ getProfileForAgent(a.agentId)?.desc || 'AI 助手' }}</div>
              </div>
            </div>
            <div v-if="!aiMembers.length" class="mp-empty-sm">暂无 AI 助手</div>
          </template>
        </div>

        <!-- Human Section -->
        <div class="mp-section">
          <button class="mp-dir-btn" @click="toggleSection('human')">
            <span class="mp-dir-arrow" :class="{ expanded: expandedSections.has('human') }">
              <Icon name="chevron_right" :size="10" />
            </span>
            <Icon name="user" :size="12" />
            <span class="mp-dir-name">成员</span>
            <span class="mp-dir-count">{{ humanMembers.length }}</span>
          </button>
          <template v-if="expandedSections.has('human')">
            <div v-for="m in humanMembers" :key="m.id" class="mp-item">
              <div class="mp-avatar mp-avatar-human">
                {{ (m.user_name || m.user_id || '?')[0]?.toUpperCase() }}
              </div>
              <div class="mp-info">
                <div class="mp-name">{{ m.user_name || m.user_id?.slice(0, 8) || '未知用户' }}</div>
                <div class="mp-sub">{{ m.role === 'admin' ? '管理员' : '成员' }}</div>
              </div>
            </div>
            <div v-if="!humanMembers.length" class="mp-empty-sm">暂无其他成员</div>
          </template>
        </div>

        <!-- Channel Mode -->
        <div class="mp-divider" />
        <div class="mp-section mp-mode-section">
          <div class="mp-mode-label">
            <Icon name="at" :size="12" />
            <span>AI 回复模式</span>
          </div>
          <div class="mp-mode-options">
            <button
              v-for="opt in channelModeOptions"
              :key="opt.value"
              class="mp-mode-btn"
              :class="{ active: channelMode === opt.value }"
              @click="emit('update:channelMode', opt.value)"
              :title="opt.desc"
            >
              {{ opt.label }}
            </button>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.mp-panel {
  position: absolute;
  top: 0; right: 0; bottom: 0;
  width: 240px;
  background: var(--bg-side);
  border-left: 1px solid var(--rule);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  box-shadow: -20px 0 40px -20px rgba(29,26,20,0.18);
  z-index: 10;
}
/* Header — matches .ws-head */
.mp-head {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 10px 12px;
  border-bottom: 1px solid var(--rule-soft);
  flex-shrink: 0;
}
.mp-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--ink);
}
.mp-count {
  font-size: 10px;
  font-weight: 600;
  background: rgba(29,26,20,0.06);
  border-radius: 8px;
  padding: 1px 6px;
  color: var(--ink-mute);
}
.mp-actions { display: flex; align-items: center; gap: 4px; margin-left: auto; }
.mp-x {
  width: 22px; height: 22px; border-radius: 6px;
  display: grid; place-items: center;
  color: var(--ink-mute);
}
.mp-x:hover { background: rgba(29,26,20,0.06); color: var(--ink); }

/* Body — scrollable */
.mp-body {
  flex: 1;
  overflow-y: auto;
  padding: 6px;
}

/* Directory-style collapsible sections */
.mp-section { margin-bottom: 2px; }
.mp-dir-btn {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 7px 9px;
  border-radius: var(--r-sm);
  color: var(--ink-soft);
  font-size: 12px;
  text-align: left;
  cursor: pointer;
}
.mp-dir-btn:hover {
  background: rgba(29,26,20,0.05);
  color: var(--ink);
}
.mp-dir-arrow {
  font-size: 8px;
  color: var(--ink-mute);
  transition: transform 150ms;
  flex-shrink: 0;
  display: inline-flex;
}
.mp-dir-arrow.expanded {
  transform: rotate(90deg);
}
.mp-dir-name {
  flex: 1;
  font-weight: 500;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.mp-dir-count {
  font-size: 10px;
  color: var(--ink-mute);
  flex-shrink: 0;
}

/* Member items — matches .ws-file style */
.mp-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 9px 6px 24px;
  border-radius: var(--r-sm);
}
.mp-item:hover {
  background: rgba(29,26,20,0.04);
}
.mp-avatar {
  width: 24px;
  height: 24px;
  border-radius: 6px;
  display: grid;
  place-items: center;
  color: white;
  flex-shrink: 0;
  font-size: 10px;
}
.mp-avatar-human {
  background: var(--ink-mute);
  font-size: 10px;
  font-weight: 700;
}
.mp-info {
  flex: 1;
  min-width: 0;
}
.mp-name {
  font-size: 12px;
  font-weight: 500;
  color: var(--ink);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.mp-sub {
  font-size: 10px;
  color: var(--ink-mute);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* Auto-reply toggle — per-AI-member switch */
.mp-auto-reply-toggle {
  flex-shrink: 0;
  width: 28px;
  height: 16px;
  border-radius: 8px;
  background: rgba(29,26,20,0.14);
  position: relative;
  transition: background 150ms;
}
.mp-auto-reply-toggle.on {
  background: var(--accent, #b8852a);
}
.mp-toggle-knob {
  position: absolute;
  top: 2px;
  left: 2px;
  width: 12px;
  height: 12px;
  border-radius: 50%;
  background: #fff;
  box-shadow: 0 1px 2px rgba(0,0,0,0.25);
  transition: transform 150ms;
}
.mp-auto-reply-toggle.on .mp-toggle-knob {
  transform: translateX(12px);
}

/* Divider */
.mp-divider {
  height: 1px;
  background: var(--rule-soft);
  margin: 6px 9px;
}

/* Channel mode section */
.mp-mode-section { padding: 4px 0; }
.mp-mode-label {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 9px;
  font-size: 11px;
  font-weight: 500;
  color: var(--ink-mute);
}
.mp-mode-options {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 0 4px;
}
.mp-mode-btn {
  display: flex;
  align-items: center;
  padding: 6px 10px;
  border-radius: var(--r-sm);
  font-size: 11.5px;
  color: var(--ink-soft);
  text-align: left;
  cursor: pointer;
}
.mp-mode-btn:hover {
  background: rgba(29,26,20,0.05);
  color: var(--ink);
}
.mp-mode-btn.active {
  background: var(--accent-tint);
  color: var(--accent-deep);
  box-shadow: inset 0 0 0 1px var(--accent-soft);
  font-weight: 500;
}

/* Empty states */
.mp-empty, .mp-empty-sm {
  color: var(--ink-mute);
  font-size: 12px;
  text-align: center;
}
.mp-empty { padding: 24px 0; }
.mp-empty-sm { padding: 6px 0 6px 24px; font-size: 11px; text-align: left; }
</style>
