<script setup lang="ts">
/* 1:1 port of the prototype project page (project/hermes-projects.js ProjectPage),
   wired to the real API. Tasks are fully functional; sections/agents/files/members
   reproduce the prototype structure with live data where the API provides it. */
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import Icon from "@/components/Icon.vue";
import NewProjectModal from "@/components/NewProjectModal.vue";
import { projectsApi } from "@/api/projects";
import { teamsApi } from "@/api/teams";
import { agentsApi } from "@/api/agents";
import type { Agent, Member, Project, Task } from "@/types";

const route = useRoute();
const router = useRouter();
const projectId = route.params.id as string;

const project = ref<(Project & import("@/types").ProjectDetail) | null>(null);
const tasks = ref<Task[]>([]);
const teamName = ref("团队");
const members = ref<Member[]>([]);
const docs = ref<import("@/types").ProjectDoc[]>([]);
const convos = ref<import("@/types").ConversationBrief[]>([]);
const docFileInput = ref<HTMLInputElement | null>(null);
const uploadingDoc = ref(false);
const agents = ref<Agent[]>([]);
const editingTaskId = ref<string | null>(null);
const editDraft = ref("");
const newTaskTitle = ref("");
const menuOpen = ref(false);
const editingProject = ref(false);

const STATUS_NEXT: Record<string, string> = { todo: "doing", doing: "done", done: "todo" };
const STATUS_LABEL: Record<string, string> = { todo: "待办", doing: "进行中", done: "已完成" };
const SECTION_LABEL: Record<string, string> = {
  concept: "概念与方向", logo: "标识系统", voice: "声音与语气", rollout: "上线节奏",
  audit: "现状盘点", spec: "规范定义", set: "图标集", deck: "路演 PPT", bp: "一页 BP",
  demo: "演示脚本", press: "媒体材料", metric: "指标定义", baseline: "基线数据",
};

onMounted(() => {
  document.addEventListener("click", () => (menuOpen.value = false));
  load();
});
onBeforeUnmount(() => document.removeEventListener("click", () => (menuOpen.value = false)));

async function load() {
  const p = await projectsApi.get(projectId);
  project.value = p;
  members.value = p.members || [];
  docs.value = p.docs || [];
  convos.value = p.conversations || [];
  const [ts, ags] = await Promise.all([
    projectsApi.tasks(projectId).catch(() => []),
    agentsApi.list().catch(() => []),
  ]);
  tasks.value = ts;
  agents.value = ags;
  try {
    teamName.value = (await teamsApi.get(p.team_id)).name;
  } catch {
    /* not a member view */
  }
}
async function addDoc() {
  // Trigger file picker
  docFileInput.value?.click();
}
async function onDocFileSelected(e: Event) {
  const input = e.target as HTMLInputElement;
  const file = input.files?.[0];
  if (!file || !projectId) return;
  uploadingDoc.value = true;
  try {
    const doc = await projectsApi.uploadDoc(projectId, file);
    docs.value.unshift(doc);
  } catch {
    alert("上传失败");
  } finally {
    uploadingDoc.value = false;
    if (input) input.value = "";
  }
}
async function deleteDoc(id: string) {
  if (!confirm("删除该文件？")) return;
  await projectsApi.deleteDoc(id);
  docs.value = docs.value.filter((d) => d.id !== id);
}
function fmtSize(b: number): string {
  return b >= 1048576 ? (b / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(b / 1024)) + " KB";
}

function agentById(id: string | null): Agent {
  return (
    agents.value.find((a) => a.id === id) ||
    ({ id: id || "", label: id || "助手", color: "#b8852a", icon: "sparkle" } as Agent)
  );
}
function memberById(id: string | null): Member | undefined {
  return members.value.find((m) => m.user_id === id);
}
function sectionLabel(s: string) {
  return SECTION_LABEL[s] || s;
}
const taskStats = computed(() => ({
  done: tasks.value.filter((t) => t.status === "done").length,
  total: tasks.value.length,
}));
const progress = computed(() => project.value?.progress ?? 0);

async function cycleStatus(t: Task) {
  const next = STATUS_NEXT[t.status] || "todo";
  t.status = next;
  await projectsApi.updateTask(t.id, { status: next });
}
function startEditTask(t: Task) {
  editingTaskId.value = t.id;
  editDraft.value = t.title;
}
async function saveEditTask(t: Task) {
  const v = editDraft.value.trim();
  editingTaskId.value = null;
  if (v && v !== t.title) {
    t.title = v;
    await projectsApi.updateTask(t.id, { title: v });
  }
}
async function addTask() {
  const v = newTaskTitle.value.trim();
  if (!v) return;
  const t = await projectsApi.createTask(projectId, { title: v });
  tasks.value.push(t);
  newTaskTitle.value = "";
}
async function deleteTask(t: Task) {
  if (!confirm(`删除任务「${t.title}」？`)) return;
  await projectsApi.deleteTask(t.id);
  tasks.value = tasks.value.filter((x) => x.id !== t.id);
}
function taskToAI(task?: Task) {
  const seed = task ? `请帮我完成以下任务：${task.title}` : undefined;
  router.push({ path: "/", query: { project: projectId, seed } });
}
function docToAI(docName: string) {
  const seed = `请帮我分析项目文件：${docName}`;
  router.push({ path: "/", query: { project: projectId, seed } });
}
function back() {
  if (project.value) router.push(`/teams/${project.value.team_id}`);
  else router.push("/");
}
async function archiveProject() {
  if (!project.value) return;
  await projectsApi.update(projectId, { status: project.value.status === "active" ? "paused" : "active" });
  await load();
  menuOpen.value = false;
}
function openEditProject() {
  editingProject.value = true;
  menuOpen.value = false;
}
function onProjectUpdated() {
  editingProject.value = false;
  load();
}
async function removeProject() {
  if (!project.value) return;
  if (!confirm(`删除项目「${project.value.name}」？`)) return;
  await projectsApi.remove(projectId);
  back();
}
</script>

<template>
  <div class="stage" v-if="project">
    <div class="proj-hero">
      <button class="proj-back" @click="back"><Icon name="back" :size="12" /> 返回 {{ teamName }}</button>
      <div class="proj-hero-row">
        <div class="proj-icon" :style="{ background: project.color || '#b8852a' }"><Icon :name="project.icon || 'sparkle'" /></div>
        <div class="proj-hero-info">
          <h1 class="proj-hero-name">{{ project.name }}<span class="handle">@{{ project.handle }}</span></h1>
          <div style="font-family: var(--font-serif); font-style: italic; color: var(--ink-soft); font-size: 15px">{{ project.summary || "（暂无简介）" }}</div>
          <div class="proj-meta-row">
            <span class="proj-status" :class="project.status"><span class="dot"></span>{{ project.status === "active" ? "进行中" : "已暂停" }}</span>
            <span><Icon name="clock" /> 截止 {{ project.deadline || "—" }}</span>
            <span><Icon name="sparkle" /> {{ project.pinned_agents.length }} 个助手</span>
            <span><Icon name="check" /> 任务 {{ taskStats.done }}/{{ taskStats.total }}</span>
          </div>
        </div>
        <div class="team-actions">
          <button class="btn primary" @click="router.push({ path: '/', query: { project: projectId } })"><Icon name="chat" /> 在项目中开会话</button>
          <div style="position: relative">
            <button class="icon-btn" @click.stop="menuOpen = !menuOpen"><Icon name="settings" /></button>
            <div v-if="menuOpen" class="menu" style="top: 34px; right: 0; min-width: 170px" @click.stop>
              <button class="menu-item" @click="openEditProject"><Icon name="edit" /> <span class="m-name">编辑项目</span></button>
              <button class="menu-item" @click="archiveProject"><Icon name="pin" /> <span class="m-name">{{ project.status === "active" ? "归档项目" : "重新启用" }}</span></button>
              <div class="menu-sep"></div>
              <button class="menu-item danger" @click="removeProject"><Icon name="close" /> <span class="m-name">删除项目</span></button>
            </div>
          </div>
        </div>
      </div>
      <div style="height: 22px"></div>
    </div>

    <div class="team-body">
      <div style="display: grid; grid-template-columns: 6px 1fr; gap: 8px; align-items: center; margin-bottom: 10px">
        <span style="height: 5px; background: var(--rule); border-radius: 999px; display: block; width: 100%; grid-column: 1/3; position: relative">
          <span style="position: absolute; left: 0; top: 0; height: 5px; background: var(--accent); border-radius: 999px" :style="{ width: progress + '%' }"></span>
        </span>
      </div>
      <div style="font-size: 12.5px; color: var(--ink-mute); margin-bottom: 24px">完成度 {{ progress }}% · 任务 {{ taskStats.done }}/{{ taskStats.total }}</div>

      <div class="proj-section-grid">
        <div v-for="(s, i) in project.sections" :key="i" class="proj-section-chip">
          <div class="num">{{ String(i + 1).padStart(2, "0") }}</div>
          <div>{{ sectionLabel(s) }}</div>
        </div>
      </div>

      <!-- TASKS -->
      <div class="section-card" style="margin-bottom: 18px">
        <div class="section-head">
          <div class="section-title"><Icon name="check" /> 任务 · {{ taskStats.done }}/{{ taskStats.total }}</div>
          <span style="font-size: 11.5px; color: var(--ink-mute)">点击状态切换 · 交给助手可直接开始处理</span>
        </div>
        <div class="section-body flush">
          <div v-for="t in tasks" :key="t.id" class="task-row" :class="t.status">
            <button class="task-status" :class="t.status" @click="cycleStatus(t)" :title="STATUS_LABEL[t.status]">
              <Icon v-if="t.status === 'done'" name="check" :size="12" />
              <span v-else-if="t.status === 'doing'" class="half"></span>
            </button>
            <div class="flex-1-min">
              <input v-if="editingTaskId === t.id" class="task-edit-input" v-model="editDraft" @keydown.enter="saveEditTask(t)" @blur="saveEditTask(t)" @keydown.esc="editingTaskId = null" />
              <div v-else class="task-title" :class="{ done: t.status === 'done' }">{{ t.title }}</div>
              <div class="task-meta">
                <span class="task-status-pill" :class="t.status">{{ STATUS_LABEL[t.status] }}</span>
                <span v-if="memberById(t.owner_id)" class="task-owner"><span class="mem-avatar tiny" :style="{ background: memberById(t.owner_id)!.color || '#b8852a' }">{{ memberById(t.owner_id)!.initials }}</span>{{ memberById(t.owner_id)!.name }}</span>
                <span v-if="t.agent_id" class="task-agent"><span class="agent-dot" :style="{ background: agentById(t.agent_id).color || '#b8852a' }"></span>{{ agentById(t.agent_id).label }}</span>
              </div>
            </div>
            <div class="row-actions">
              <button class="row-act accent" title="交给助手处理" @click="taskToAI(t)"><Icon name="sparkle" :size="13" /></button>
              <button class="row-act" title="重命名" @click="startEditTask(t)"><Icon name="copy" :size="13" /></button>
              <button class="row-act danger" title="删除" @click="deleteTask(t)"><Icon name="close" :size="13" /></button>
            </div>
          </div>
          <div v-if="!tasks.length" class="empty-state">还没有任务。</div>
          <div class="task-add">
            <Icon name="plus" :size="14" class="text-mute" />
            <input class="task-add-input" v-model="newTaskTitle" placeholder="添加一个任务，回车创建…" @keydown.enter="addTask" />
            <button v-if="newTaskTitle.trim()" class="btn primary" style="height: 28px; padding: 0 12px" @click="addTask">添加</button>
          </div>
        </div>
      </div>

      <div class="col-grid">
        <div class="flex-col-gap">
          <div class="section-card">
            <div class="section-head">
              <div class="section-title"><Icon name="chat" /> 项目对话</div>
              <button class="section-link" @click="router.push({ path: '/', query: { project: projectId } })">新对话 <Icon name="plus" :size="11" /></button>
            </div>
            <div class="section-body">
              <div v-if="!convos.length" class="empty-state-lg" style="padding:32px">还没有项目对话。</div>
              <div v-for="cv in convos" :key="cv.id" class="row-item" @click="router.push({ path: '/', query: { c: cv.id } })">
                <div class="convo-ico" :style="{ background: (agentById(cv.primary_agent_id).color || '#b8852a') + '22', color: agentById(cv.primary_agent_id).color || '#b8852a' }"><Icon :name="agentById(cv.primary_agent_id).icon || 'chat'" /></div>
                <div class="row-text"><div class="row-title">{{ cv.title }}</div><div class="row-sub">{{ agentById(cv.primary_agent_id).label }}</div></div>
              </div>
            </div>
          </div>
          <div class="section-card">
            <div class="section-head"><div class="section-title"><Icon name="doc" /> 项目文件</div></div>
            <div class="section-body flush">
              <div v-for="d in docs" :key="d.id" class="file-row has-actions">
                <div class="file-ico"><Icon name="doc" /></div>
                <div class="flex-1-min"><div class="row-title">{{ d.name }}</div><div class="file-meta">{{ fmtSize(d.size_bytes) }} · {{ d.created_by_name || "成员" }}</div></div>
                <span class="file-kind">{{ d.kind }}</span>
                <div class="row-actions">
                  <button class="row-act accent" title="用助手分析" @click="docToAI(d.name)"><Icon name="sparkle" :size="13" /></button>
                  <button class="row-act danger" title="删除" @click="deleteDoc(d.id)"><Icon name="close" :size="13" /></button>
                </div>
              </div>
              <div v-if="!docs.length" class="empty-state">还没有文件。</div>
              <div class="task-add">
                <input ref="docFileInput" type="file" style="display:none" @change="onDocFileSelected" />
                <Icon name="paperclip" :size="14" class="text-mute" />
                <button class="btn primary" style="height: 28px; padding: 0 12px" :disabled="uploadingDoc" @click="addDoc">
                  {{ uploadingDoc ? "上传中…" : "上传文件" }}
                </button>
              </div>
            </div>
          </div>
        </div>

        <div class="flex-col-gap">
          <div class="section-card">
            <div class="section-head"><div class="section-title"><Icon name="sparkle" /> 项目助手</div></div>
            <div class="agent-mini-grid">
              <button v-for="id in project.pinned_agents" :key="id" class="agent-mini" @click="router.push('/')">
                <div class="agent-icon" :style="{ background: agentById(id).color || '#b8852a' }"><Icon :name="agentById(id).icon || 'sparkle'" /></div>
                <div style="min-width: 0; flex: 1">
                  <div class="nm">{{ agentById(id).label }}</div>
                  <div class="ds">{{ agentById(id).description }}</div>
                </div>
              </button>
              <div v-if="!project.pinned_agents.length" style="padding: 18px; color: var(--ink-mute); font-size: 12px">未指定项目助手。</div>
            </div>
          </div>
          <div class="section-card">
            <div class="section-head"><div class="section-title"><Icon name="user" /> 项目成员</div></div>
            <div class="section-body">
              <div v-for="m in members.slice(0, 6)" :key="m.user_id" class="row-item">
                <div class="mem-avatar" :style="{ background: m.color || '#b8852a' }">{{ m.initials }}<span class="status" :class="m.status"></span></div>
                <div class="row-text"><div class="row-title">{{ m.name }}</div><div class="row-sub">{{ m.role }}</div></div>
              </div>
              <div v-if="!members.length" style="padding: 18px; color: var(--ink-mute); font-size: 12px">—</div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>

  <NewProjectModal
    v-if="editingProject && project"
    :team-id="project.team_id"
    :team-name="teamName"
    :members="members"
    :project="project"
    @close="editingProject = false"
    @updated="onProjectUpdated"
  />
</template>
