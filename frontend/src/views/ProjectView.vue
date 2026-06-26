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
import { useChatStore } from "@/stores/chat";
import type { Agent, Member, Project, ProjectActivity, Task } from "@/types";

const route = useRoute();
const router = useRouter();
const chat = useChatStore();
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
const activity = ref<ProjectActivity[]>([]);
const editingTaskId = ref<string | null>(null);
const editDraft = ref("");
const openingGroup = ref(false);
async function openProjectGroup() {
  if (openingGroup.value) return;
  openingGroup.value = true;
  try {
    const { channel } = await teamsApi.getProjectGroup(projectId);
    router.push({ path: "/", query: { c: channel.id } });
  } catch {
    /* ignore */
  } finally {
    openingGroup.value = false;
  }
}
const newTaskTitle = ref("");
const menuOpen = ref(false);
const editingProject = ref(false);

const STATUS_NEXT: Record<string, string> = { todo: "doing", doing: "done", done: "todo" };
const STATUS_LABEL: Record<string, string> = { todo: "待办", doing: "进行中", done: "已完成" };
const STATUS_COLOR: Record<string, string> = { todo: "#8a8474", doing: "#3a6da1", done: "#3a8a7a" };
const KANBAN_COLS = ["todo", "doing", "done"] as const;

// ── View mode toggle (list ↔ kanban) ──
const viewMode = ref<"list" | "kanban">("list");
const kanbanCols = computed(() =>
  KANBAN_COLS.map((s) => ({
    key: s,
    label: STATUS_LABEL[s],
    color: STATUS_COLOR[s],
    items: tasks.value.filter((t) => t.status === s),
  }))
);
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
  await loadActivity();
  try {
    teamName.value = (await teamsApi.get(p.team_id)).name;
  } catch {
    /* not a member view */
  }
}

function activityAgo(iso: string): string {
  const mins = Math.floor((Date.now() - new Date(iso).getTime()) / 60000);
  if (mins < 1) return "刚刚";
  if (mins < 60) return `${mins} 分钟前`;
  if (mins < 1440) return `${Math.floor(mins / 60)} 小时前`;
  return `${Math.floor(mins / 1440)} 天前`;
}
const ACTIVITY_ICON: Record<string, string> = {
  "task.created": "plus", "task.derived": "sparkle", "task.moved": "arrow-right",
  "task.done": "check", "doc.created": "doc", "knowledge.created": "doc",
};
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
function profileById(profileId: string): { label: string; color: string; icon: string; description: string } {
  const p = chat.profiles.find((x) => x.id === profileId);
  return {
    label: p?.name || "助手",
    color: p?.color || "#b8852a",
    icon: p?.icon || "sparkle",
    description: p?.desc || "",
  };
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

// Recompute progress locally (mirrors backend round(done/total*100)) + refresh feed.
function syncProgressLocal() {
  if (!project.value) return;
  const total = tasks.value.length;
  const done = tasks.value.filter((t) => t.status === "done").length;
  project.value.progress = total ? Math.round((done / total) * 100) : 0;
}
async function loadActivity() {
  activity.value = await projectsApi.activity(projectId).catch(() => []);
}
async function applyStatus(t: Task, next: string) {
  const prev = t.status;
  t.status = next;
  syncProgressLocal();
  try {
    await projectsApi.moveTaskStatus(t.id, next);
    await loadActivity();
  } catch {
    t.status = prev;
    syncProgressLocal();
  }
}

async function cycleStatus(t: Task) {
  const next = STATUS_NEXT[t.status] || "todo";
  await applyStatus(t, next);
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

// ── Kanban drag-and-drop ──
const dragTaskId = ref<string | null>(null);
const dragOverCol = ref<string | null>(null);
function onKanbanDragStart(e: DragEvent, taskId: string) {
  dragTaskId.value = taskId;
  if (e.dataTransfer) { e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/plain", taskId); }
}
function onKanbanDragEnd() {
  dragTaskId.value = null;
  dragOverCol.value = null;
}
async function onKanbanDrop(e: DragEvent, col: string) {
  e.preventDefault();
  const taskId = dragTaskId.value || e.dataTransfer?.getData("text/plain");
  dragOverCol.value = null;
  dragTaskId.value = null;
  if (!taskId) return;
  const t = tasks.value.find((x) => x.id === taskId);
  if (!t || t.status === col) return;
  await applyStatus(t, col);
}
function taskToAI(task?: Task, agentId?: string) {
  const seed = task ? `请帮我完成以下任务：${task.title}` : undefined;
  const profile = agentId ? chat.profiles.find((p) => p.default_agent_id === agentId) : null;
  const q: Record<string, string> = { project: projectId };
  if (seed) q.seed = seed;
  if (profile) q.profile = profile.id;
  router.push({ path: "/", query: q });
}
function docToAI(docName: string, agentId?: string) {
  const seed = `请帮我分析项目文件：${docName}`;
  const profile = agentId ? chat.profiles.find((p) => p.default_agent_id === agentId) : null;
  const q: Record<string, string> = { project: projectId, seed };
  if (profile) q.profile = profile.id;
  router.push({ path: "/", query: q });
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
            <span><Icon name="sparkle" /> {{ project.pinned_profile_ids.length }} 个助手</span>
            <span><Icon name="check" /> 任务 {{ taskStats.done }}/{{ taskStats.total }}</span>
          </div>
        </div>
        <div class="team-actions">
          <button class="btn" :disabled="openingGroup" @click="openProjectGroup"><Icon name="users" /> 项目群聊</button>
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
          <div style="display:flex;align-items:center;gap:8px">
            <div class="view-toggle">
              <button :class="{ active: viewMode === 'list' }" @click="viewMode = 'list'" title="列表视图">
                <Icon name="list" :size="13" />
              </button>
              <button :class="{ active: viewMode === 'kanban' }" @click="viewMode = 'kanban'" title="看板视图">
                <Icon name="cube" :size="13" />
              </button>
            </div>
            <span style="font-size: 11.5px; color: var(--ink-mute)">点击状态切换 · 交给助手处理</span>
          </div>
        </div>

        <!-- List view -->
        <div v-if="viewMode === 'list'" class="section-body flush">
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

        <!-- Kanban view -->
        <div v-else class="kanban-board">
          <div
            v-for="col in kanbanCols"
            :key="col.key"
            class="kanban-col"
            :class="{ 'drag-over': dragOverCol === col.key }"
            @dragover.prevent="dragOverCol = col.key"
            @dragleave="dragOverCol = dragOverCol === col.key ? null : dragOverCol"
            @drop="onKanbanDrop($event, col.key)"
          >
            <div class="kanban-col-head">
              <span class="kanban-col-dot" :style="{ background: col.color }"></span>
              <span class="kanban-col-label">{{ col.label }}</span>
              <span class="kanban-col-count">{{ col.items.length }}</span>
            </div>
            <div class="kanban-cards">
              <div
                v-for="t in col.items"
                :key="t.id"
                class="kanban-card"
                :class="{ dragging: dragTaskId === t.id }"
                draggable="true"
                @dragstart="onKanbanDragStart($event, t.id)"
                @dragend="onKanbanDragEnd"
              >
                <div class="kanban-card-title" :class="{ done: t.status === 'done' }">{{ t.title }}</div>
                <div class="kanban-card-meta">
                  <span v-if="memberById(t.owner_id)" class="task-owner" style="font-size:10.5px">
                    <span class="mem-avatar tiny" :style="{ background: memberById(t.owner_id)!.color || '#b8852a' }">{{ memberById(t.owner_id)!.initials }}</span>
                    {{ memberById(t.owner_id)!.name }}
                  </span>
                  <span v-if="t.agent_id" class="task-agent" style="font-size:10.5px">
                    <span class="agent-dot" :style="{ background: agentById(t.agent_id).color || '#b8852a' }"></span>
                    {{ agentById(t.agent_id).label }}
                  </span>
                </div>
                <div class="kanban-card-actions">
                  <button class="kanban-move-btn" :disabled="t.status === 'todo'" @click="cycleStatus(t)" title="向前移动状态">←</button>
                  <button class="row-act accent" title="交给助手" @click="taskToAI(t)"><Icon name="sparkle" :size="11" /></button>
                  <button class="row-act danger" title="删除" @click="deleteTask(t)"><Icon name="close" :size="11" /></button>
                  <button class="kanban-move-btn" :disabled="t.status === 'done'" @click="cycleStatus(t)" title="向后移动状态">→</button>
                </div>
              </div>
              <div v-if="!col.items.length" class="kanban-empty">暂无任务</div>
            </div>
            <div v-if="col.key === 'todo'" class="kanban-add">
              <input class="task-add-input" v-model="newTaskTitle" placeholder="+ 新任务" @keydown.enter="addTask" />
            </div>
          </div>
        </div>
      </div>

      <!-- ACTIVITY LOG (closed-loop visibility) -->
      <div class="section-card" style="margin-bottom: 18px">
        <div class="section-head">
          <div class="section-title"><Icon name="bolt" /> 活动日志</div>
          <span style="font-size: 11.5px; color: var(--ink-mute)">任务推进 · 产出沉淀 · 自动记录</span>
        </div>
        <div class="section-body flush">
          <div v-if="!activity.length" class="empty-state">暂无活动。推进任务或沉淀会话产出后将自动记录。</div>
          <div v-for="a in activity" :key="a.id" class="file-row">
            <div class="file-ico"><Icon :name="ACTIVITY_ICON[a.kind] || 'bolt'" :size="13" /></div>
            <div class="flex-1-min">
              <div class="row-title"><b>{{ a.actor_name || "成员" }}</b> {{ a.summary }}</div>
              <div class="file-meta">{{ activityAgo(a.created_at) }}</div>
            </div>
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
              <button v-for="pid in project.pinned_profile_ids" :key="pid" class="agent-mini" @click="() => { const p = chat.profiles.find((x) => x.id === pid); router.push({ path: '/', query: { project: projectId, ...(p ? { profile: p.id } : {}) } }); }">
                <div class="agent-icon" :style="{ background: profileById(pid).color || '#b8852a' }"><Icon :name="profileById(pid).icon || 'sparkle'" /></div>
                <div style="min-width: 0; flex: 1">
                  <div class="nm">{{ profileById(pid).label }}</div>
                  <div class="ds">{{ profileById(pid).description }}</div>
                </div>
              </button>
              <div v-if="!project.pinned_profile_ids.length" style="padding: 18px; color: var(--ink-mute); font-size: 12px">未指定项目助手。</div>
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
