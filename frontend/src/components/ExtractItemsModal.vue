<script setup lang="ts">
import { onMounted, ref } from "vue";
import ModalShell from "@/components/ModalShell.vue";
import Icon from "@/components/Icon.vue";
import { conversationsApi } from "@/api/conversations";
import { projectsApi } from "@/api/projects";
import { useNotificationStore } from "@/stores/notifications";

const props = defineProps<{
  conversationId: string;
  teams: { id: string; name: string }[];
}>();
const emit = defineEmits<{ close: []; created: [projectId: string] }>();
const ns = useNotificationStore();

const loading = ref(true);
const creating = ref(false);
const projectName = ref("");
const tasks = ref<string[]>([]);
const selectedTeamId = ref(props.teams[0]?.id || "");
const editingTask = ref<number | null>(null);
const newTask = ref("");
const error = ref("");

onMounted(async () => {
  try {
    const res = await conversationsApi.extractItems(props.conversationId);
    projectName.value = res.project_name || "新项目";
    tasks.value = res.tasks;
    if (res.team_id && props.teams.find((t) => t.id === res.team_id)) {
      selectedTeamId.value = res.team_id;
    }
  } catch {
    error.value = "解析失败，请手动填写";
  } finally {
    loading.value = false;
  }
});

function removeTask(i: number) {
  tasks.value.splice(i, 1);
}
function addTask() {
  const t = newTask.value.trim();
  if (t) {
    tasks.value.push(t);
    newTask.value = "";
  }
}

async function create() {
  if (!projectName.value.trim() || !selectedTeamId.value || creating.value) return;
  creating.value = true;
  try {
    const proj = await projectsApi.create(selectedTeamId.value, { name: projectName.value.trim() });
    for (const title of tasks.value) {
      await projectsApi.createTask(proj.id, { title }).catch(() => {});
    }
    ns.toast(`项目「${proj.name}」已创建，共 ${tasks.value.length} 个任务`);
    emit("created", proj.id);
  } catch (e: any) {
    ns.toast(e?.response?.data?.detail || "创建失败", "error");
  } finally {
    creating.value = false;
  }
}
</script>

<template>
  <ModalShell title="从会话创建项目" subtitle="AI 已从对话中提取以下内容，确认后自动创建" :width="520" @close="emit('close')">
    <div v-if="loading" style="text-align: center; padding: 24px; color: var(--ink-soft)">正在分析对话内容…</div>
    <div v-else style="display: flex; flex-direction: column; gap: 14px">
      <div v-if="error" style="font-size: 12.5px; color: #c0392b; padding: 8px 12px; background: #fdf0ed; border-radius: 6px">{{ error }}</div>

      <div v-if="teams.length > 1">
        <label style="font-size: 12.5px; font-weight: 500; color: var(--ink-mute); display: block; margin-bottom: 4px">所属团队</label>
        <select v-model="selectedTeamId" style="width: 100%; padding: 8px 12px; border: 1px solid var(--border); border-radius: 8px; font-size: 13.5px; background: var(--surface); color: var(--ink); outline: none">
          <option v-for="t in teams" :key="t.id" :value="t.id">{{ t.name }}</option>
        </select>
      </div>

      <div>
        <label style="font-size: 12.5px; font-weight: 500; color: var(--ink-mute); display: block; margin-bottom: 4px">项目名称</label>
        <input v-model="projectName" type="text" placeholder="项目名称"
          style="width: 100%; padding: 8px 12px; border: 1px solid var(--border); border-radius: 8px; font-size: 13.5px; background: var(--surface); color: var(--ink); outline: none; box-sizing: border-box" />
      </div>

      <div>
        <label style="font-size: 12.5px; font-weight: 500; color: var(--ink-mute); display: block; margin-bottom: 6px">
          任务列表 <span style="font-weight: 400">（{{ tasks.length }} 项，可编辑）</span>
        </label>
        <div v-if="!tasks.length" style="font-size: 12.5px; color: var(--ink-mute); padding: 8px 0">
          未检测到任务，请手动添加
        </div>
        <div v-for="(task, i) in tasks" :key="i"
          style="display: flex; align-items: center; gap: 8px; padding: 6px 10px; border: 1px solid var(--rule-soft); border-radius: 6px; margin-bottom: 4px; font-size: 13px">
          <Icon name="check" :size="12" style="color: var(--accent); flex-shrink: 0" />
          <span v-if="editingTask !== i" style="flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap" @dblclick="editingTask = i">{{ task }}</span>
          <input v-else v-model="tasks[i]" style="flex: 1; border: none; outline: none; font-size: 13px; background: transparent"
            @blur="editingTask = null" @keydown.enter="editingTask = null" autofocus />
          <button @click="removeTask(i)" style="color: var(--ink-mute); background: none; border: none; cursor: pointer; padding: 0 2px; line-height: 1">×</button>
        </div>
        <div style="display: flex; gap: 6px; margin-top: 6px">
          <input v-model="newTask" type="text" placeholder="添加任务…"
            style="flex: 1; padding: 6px 10px; border: 1px dashed var(--border); border-radius: 6px; font-size: 13px; background: var(--surface); color: var(--ink); outline: none"
            @keydown.enter="addTask" />
          <button class="btn" @click="addTask"><Icon name="plus" :size="12" /></button>
        </div>
      </div>
    </div>

    <template #foot>
      <button class="btn" @click="emit('close')">取消</button>
      <button class="btn primary" :disabled="!projectName.trim() || !selectedTeamId || creating || loading" @click="create">
        <Icon name="sparkle" :size="13" />
        {{ creating ? "创建中…" : "创建项目与任务" }}
      </button>
    </template>
  </ModalShell>
</template>
