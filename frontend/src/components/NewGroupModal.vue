<script setup lang="ts">
import { onMounted, ref, computed } from "vue";
import Icon from "@/components/Icon.vue";
import ModalShell from "@/components/ModalShell.vue";
import { agentsApi, type Profile } from "@/api/agents";
import { teamsApi } from "@/api/teams";
import { conversationsApi } from "@/api/conversations";
import { useNotificationStore } from "@/stores/notifications";
import type { Team, TeamDetail } from "@/types";

const emit = defineEmits<{ close: []; created: [id: string] }>();
const ns = useNotificationStore();

// Step 1: 选择团队
const teams = ref<Team[]>([]);
const selectedTeamId = ref<string | null>(null);
const teamDetail = ref<TeamDetail | null>(null);

// Step 2: 选择助手
const title = ref("");
const profiles = ref<Profile[]>([]);
const selectedAgents = ref<string[]>([]);
const loading = ref(false);

// 团队共享的 profile IDs
const sharedProfileIds = computed(() => {
  return teamDetail.value?.shared_profile_ids || [];
});

// 过滤后的 profiles（只显示团队共享的）
const filteredProfiles = computed(() => {
  if (!selectedTeamId.value) return [];
  const ids = sharedProfileIds.value;
  if (!ids.length) return [];
  return profiles.value.filter(
    (p) => p.is_active && ids.includes(p.id)
  );
});

const step = ref<"team" | "agents">("team");

onMounted(async () => {
  try {
    teams.value = await teamsApi.list();
  } catch { /* ignore */ }
});

async function selectTeam(teamId: string) {
  selectedTeamId.value = teamId;
  step.value = "agents";
  try {
    teamDetail.value = await teamsApi.get(teamId);
    profiles.value = await agentsApi.profiles();
  } catch {
    ns.toast("获取团队信息失败", "error");
  }
}

function backToTeams() {
  step.value = "team";
  selectedTeamId.value = null;
  teamDetail.value = null;
  selectedAgents.value = [];
}

function toggleAgent(id: string) {
  const idx = selectedAgents.value.indexOf(id);
  if (idx >= 0) selectedAgents.value.splice(idx, 1);
  else selectedAgents.value.push(id);
}

async function create() {
  if (!title.value.trim()) {
    ns.toast("请输入群聊名称", "warn");
    return;
  }
  if (!selectedAgents.value.length) {
    ns.toast("请至少选择一个AI助手", "warn");
    return;
  }
  if (!selectedTeamId.value) {
    ns.toast("请先选择团队", "warn");
    return;
  }
  loading.value = true;
  try {
    const agentIds = selectedAgents.value.map(
      (pid) => profiles.value.find((p) => p.id === pid)?.default_agent_id || pid
    );
    const res = await conversationsApi.createGroup(
      title.value.trim(),
      [], // no other human members for now
      agentIds,
      selectedTeamId.value
    );
    emit("created", res.id);
  } catch (e: unknown) {
    ns.toast("创建失败：" + ((e as Error).message || "未知错误"), "error");
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <ModalShell
    :title="step === 'team' ? '选择团队' : '创建群聊'"
    :subtitle="step === 'team' ? '选择团队以创建群聊' : '选择AI助手组成群聊'"
    :width="480"
    @close="emit('close')"
  >
    <!-- Step 1: 选择团队 -->
    <div v-if="step === 'team'" style="padding: 0 4px">
      <div v-if="teams.length === 0" style="text-align: center; color: var(--ink-mute); padding: 20px 0">
        暂无团队，请先创建团队
      </div>
      <div v-else class="team-list">
        <button
          v-for="t in teams"
          :key="t.id"
          class="team-card"
          @click="selectTeam(t.id)"
        >
          <span class="team-icon" :style="{ background: t.color || '#666' }">
            <Icon name="users" />
          </span>
          <div class="team-meta">
            <div class="team-name">{{ t.name }}</div>
            <div class="team-handle" v-if="t.handle">@{{ t.handle }}</div>
          </div>
          <Icon name="arrow_right" />
        </button>
      </div>
    </div>

    <!-- Step 2: 输入群名 + 选择助手 -->
    <div v-else style="padding: 0 4px">
      <div style="margin-bottom: 12px">
        <button class="back-btn" @click="backToTeams">
          <Icon name="arrow_left" :size="14" /> 返回选择团队
        </button>
      </div>

      <div style="margin-bottom: 16px">
        <label style="font-size: 12px; color: var(--ink-mute); display: block; margin-bottom: 6px">群聊名称</label>
        <input
          v-model="title"
          class="group-input"
          placeholder="如：项目讨论组"
          @keydown.enter="create"
          autofocus
        />
      </div>

      <label style="font-size: 12px; color: var(--ink-mute); display: block; margin-bottom: 8px">
        选择AI助手 <span v-if="selectedAgents.length">({{ selectedAgents.length }} 已选)</span>
      </label>
      <div v-if="filteredProfiles.length === 0" style="text-align: center; color: var(--ink-mute); padding: 16px 0">
        该团队暂无可用助手
      </div>
      <div v-else class="agent-grid">
        <button
          v-for="p in filteredProfiles"
          :key="p.id"
          class="agent-card"
          :class="{ on: selectedAgents.includes(p.id) }"
          @click="toggleAgent(p.id)"
        >
          <span class="agent-avatar" :style="{ background: p.color }"><Icon :name="p.icon" /></span>
          <div class="agent-meta">
            <div class="agent-name">{{ p.name }}</div>
            <div class="agent-desc">{{ p.desc }}</div>
          </div>
          <Icon v-if="selectedAgents.includes(p.id)" name="check" class="agent-check" />
        </button>
      </div>
    </div>

    <template #foot>
      <button class="btn" @click="emit('close')">取消</button>
      <span class="flex-1"></span>
      <button
        v-if="step === 'agents'"
        class="btn primary"
        :disabled="loading"
        @click="create"
      >
        {{ loading ? '创建中...' : '创建群聊' }} <Icon v-if="!loading" name="arrow_up" :size="12" />
      </button>
    </template>
  </ModalShell>
</template>

<style scoped>
.team-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.team-card {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 14px;
  border-radius: 10px;
  border: 1.5px solid var(--rule);
  background: var(--bg-panel);
  cursor: pointer;
  transition: border-color 140ms, background 140ms;
}
.team-card:hover {
  border-color: var(--accent);
  background: var(--bg-hover, rgba(99, 102, 241, 0.04));
}
.team-icon {
  width: 36px;
  height: 36px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  font-size: 16px;
  flex-shrink: 0;
}
.team-meta {
  flex: 1;
  min-width: 0;
}
.team-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--ink);
}
.team-handle {
  font-size: 11px;
  color: var(--ink-mute);
}
.back-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: var(--ink-mute);
  background: none;
  border: none;
  cursor: pointer;
  padding: 0;
}
.back-btn:hover {
  color: var(--accent);
}
.group-input {
  width: 100%;
  padding: 8px 12px;
  border: 1.5px solid var(--rule);
  border-radius: 8px;
  background: var(--bg-panel);
  color: var(--ink);
  font-size: 13px;
  outline: none;
  transition: border-color 140ms;
}
.group-input:focus {
  border-color: var(--accent);
}
.agent-grid {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.agent-card {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border-radius: 10px;
  border: 1.5px solid var(--rule);
  background: var(--bg-panel);
  cursor: pointer;
  transition: border-color 140ms, background 140ms;
}
.agent-card:hover {
  border-color: var(--accent);
}
.agent-card.on {
  border-color: var(--accent);
  background: rgba(99, 102, 241, 0.06);
}
.agent-avatar {
  width: 32px;
  height: 32px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  font-size: 14px;
  flex-shrink: 0;
}
.agent-meta {
  flex: 1;
  min-width: 0;
}
.agent-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--ink);
}
.agent-desc {
  font-size: 11px;
  color: var(--ink-mute);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.agent-check {
  color: var(--accent);
}
</style>
