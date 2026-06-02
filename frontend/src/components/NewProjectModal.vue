<script setup lang="ts">
/* 1:1 port of the prototype NewProjectModal, wired to the real projects API. */
import { computed, reactive, ref, watch } from "vue";
import Icon from "@/components/Icon.vue";
import ModalShell from "@/components/ModalShell.vue";
import { projectsApi } from "@/api/projects";
import { useChatStore } from "@/stores/chat";
import type { Agent, Member, Project } from "@/types";

const props = defineProps<{
  teamId: string; teamName: string; members: Member[];
  project?: Project;  // edit mode when provided
}>();
const emit = defineEmits<{ close: []; created: [Project]; updated: [Project] }>();
const isEdit = computed(() => !!props.project);
const chat = useChatStore();

const COLORS = ["#b8852a", "#3a6da1", "#8a5aa1", "#5b8a4a", "#c45a3a", "#3a8a7a", "#6a3aa1", "#1d1a14"];
const ICONS = ["sparkle", "cube", "target", "chart2", "doc", "chart", "globe", "compass"];
const SECTIONS = [
  { id: "concept", label: "概念" }, { id: "spec", label: "规范" }, { id: "rollout", label: "上线" },
  { id: "audit", label: "盘点" }, { id: "metric", label: "指标" }, { id: "baseline", label: "基线" },
  { id: "deck", label: "演示" }, { id: "press", label: "媒体" },
];

function defaultDeadline() {
  const d = new Date();
  d.setMonth(d.getMonth() + 2);
  return d.toISOString().slice(0, 10);
}

const form = reactive({
  name: props.project?.name || "",
  handle: props.project?.handle || "",
  color: props.project?.color || COLORS[0],
  icon: props.project?.icon || ICONS[0],
  summary: props.project?.summary || "",
  deadline: props.project?.deadline || defaultDeadline(),
  sections: (props.project as any)?.sections || ["concept", "spec", "rollout"],
  pinnedAgents: (props.project as any)?.pinned_agents || [] as string[],
  members: props.project ? ((props.project as any)?.member_ids || []) : props.members.slice(0, 2).map((m) => m.user_id),
  visibility: (props.project as any)?.visibility || "team",
});

// Unified list: profiles first, then raw agents without a profile
const agentItems = computed<Agent[]>(() => {
  const coveredIds = new Set<string>();
  const items: Agent[] = [];
  for (const p of chat.profiles) {
    const id = p.default_agent_id || p.handle || p.id;
    items.push({ id, label: p.name, icon: p.icon || "sparkle", color: p.color || "#b8852a", description: p.desc || "" } as Agent);
    if (p.default_agent_id) coveredIds.add(p.default_agent_id);
  }
  for (const a of chat.agents) {
    if (!coveredIds.has(a.id)) items.push(a);
  }
  return items.slice(0, 8);
});
let handleEdited = false as boolean;
if (isEdit.value) handleEdited = true;
watch(() => form.name, (v) => {
  if (!handleEdited) form.handle = (v || "").trim().toLowerCase().replace(/\s+/g, "-").replace(/[^\w\-一-鿿]/g, "").slice(0, 24);
});
const valid = computed(() => form.name.trim().length >= 2);
const busy = ref(false);
const toggle = (arr: string[], id: string) => {
  const i = arr.indexOf(id);
  i >= 0 ? arr.splice(i, 1) : arr.push(id);
};
async function submit() {
  if (!valid.value || busy.value) return;
  busy.value = true;
  try {
    const data = {
      name: form.name.trim(), handle: form.handle || undefined, color: form.color, icon: form.icon,
      summary: form.summary || undefined, sections: form.sections, pinned_agents: form.pinnedAgents,
      deadline: form.deadline || undefined,
    };
    if (isEdit.value && props.project) {
      const p = await projectsApi.update(props.project.id, data);
      if (form.members.length) await projectsApi.setMembers(p.id, form.members).catch(() => {});
      emit("updated", p);
    } else {
      const p = await projectsApi.create(props.teamId, data);
      if (form.members.length) await projectsApi.setMembers(p.id, form.members).catch(() => {});
      emit("created", p);
    }
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <ModalShell :title="isEdit ? '编辑项目' : '新建项目'" :subtitle="isEdit ? '修改项目信息' : ('在「' + teamName + '」团队下创建一个新项目')" :width="640" @close="$emit('close')">
    <div class="np-identity">
      <div class="proj-icon np-preview" :style="{ background: form.color }"><Icon :name="form.icon" /></div>
      <div style="flex: 1; min-width: 0">
        <div class="np-name-row">
          <input class="np-input np-name" v-model="form.name" placeholder="项目名称" maxlength="32" autofocus />
          <span class="np-counter">{{ form.name.length }}/32</span>
        </div>
        <div class="np-handle-row">
          <span class="np-handle-at">@</span>
          <input class="np-input np-handle" :value="form.handle" @input="(e) => { handleEdited = true; form.handle = (e.target as HTMLInputElement).value; }" placeholder="handle" maxlength="24" />
          <span class="np-hint">用于 URL 与跨团队引用</span>
        </div>
      </div>
    </div>

    <div class="np-row">
      <div class="np-field">
        <label class="np-label">颜色</label>
        <div class="np-swatches"><button v-for="c in COLORS" :key="c" class="np-swatch" :class="{ active: form.color === c }" :style="{ background: c }" @click="form.color = c"></button></div>
      </div>
      <div class="np-field">
        <label class="np-label">图标</label>
        <div class="np-icon-row"><button v-for="ic in ICONS" :key="ic" class="np-icon-btn" :class="{ active: form.icon === ic }" @click="form.icon = ic"><Icon :name="ic" /></button></div>
      </div>
    </div>

    <div class="np-field">
      <label class="np-label">简介 <span class="np-hint">一两句话说明这个项目要解决什么</span></label>
      <textarea class="np-input np-textarea" v-model="form.summary" rows="3" placeholder="例如：把视觉、声音、用词都统一到新的品牌主张上。"></textarea>
    </div>

    <div class="np-row">
      <div class="np-field"><label class="np-label">截止日期</label><input class="np-input" type="date" v-model="form.deadline" /></div>
      <div class="np-field">
        <label class="np-label">可见性</label>
        <div class="np-seg">
          <button :class="{ active: form.visibility === 'team' }" @click="form.visibility = 'team'"><Icon name="user" /> 团队可见</button>
          <button :class="{ active: form.visibility === 'private' }" @click="form.visibility = 'private'"><Icon name="pin" /> 仅成员</button>
        </div>
      </div>
    </div>

    <div class="np-field">
      <label class="np-label">项目阶段 <span class="np-hint">用作项目内的分组标签</span></label>
      <div class="np-chips">
        <button v-for="s in SECTIONS" :key="s.id" class="np-chip" :class="{ on: form.sections.includes(s.id) }" @click="toggle(form.sections, s.id)">
          <Icon v-if="form.sections.includes(s.id)" name="check" :size="11" /> {{ s.label }}
        </button>
      </div>
    </div>

    <div class="np-field">
      <label class="np-label">钉选助手 <span class="np-hint">项目首页直接可用 · 已选 {{ form.pinnedAgents.length }}</span></label>
      <div class="np-agents">
        <button v-for="a in agentItems" :key="a.id" class="np-agent" :class="{ on: form.pinnedAgents.includes(a.id) }" @click="toggle(form.pinnedAgents, a.id)">
          <span class="np-agent-ico" :style="{ background: a.color || '#b8852a' }"><Icon :name="a.icon || 'sparkle'" /></span>
          <span class="np-agent-nm">{{ a.label }}</span>
          <span v-if="form.pinnedAgents.includes(a.id)" class="np-agent-check"><Icon name="check" :size="9" /></span>
        </button>
      </div>
    </div>

    <div class="np-field">
      <label class="np-label">初始成员 <span class="np-hint">从「{{ teamName }}」中选 · 已选 {{ form.members.length }}</span></label>
      <div class="np-members">
        <button v-for="m in members" :key="m.user_id" class="np-mem" :class="{ on: form.members.includes(m.user_id) }" @click="toggle(form.members, m.user_id)">
          <span class="np-mem-av" :style="{ background: m.color || '#b8852a' }">{{ m.initials }}</span>
          <span class="np-mem-nm">{{ m.name }}</span>
          <span class="np-mem-role">{{ m.role }}</span>
          <span v-if="form.members.includes(m.user_id)" class="np-mem-check"><Icon name="check" :size="10" /></span>
        </button>
      </div>
    </div>

    <template #foot>
      <span class="np-foot-hint">项目会立即在团队主页显示</span>
      <span style="flex: 1"></span>
      <button class="btn" @click="$emit('close')">取消</button>
      <button class="btn primary" :disabled="!valid || busy" @click="submit"><Icon name="plus" /> {{ busy ? "创建中…" : "创建项目" }}</button>
    </template>
  </ModalShell>
</template>
