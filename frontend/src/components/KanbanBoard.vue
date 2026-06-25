<script setup lang="ts">
/* Kanban 看板 — 拖拽式项目管理任务看板。 */
import { computed, onMounted, ref } from "vue";
import Icon from "@/components/Icon.vue";
import { projectsApi } from "@/api/projects";
import { useNotificationStore } from "@/stores/notifications";
import type { Task } from "@/types";

const props = defineProps<{ projectId: string }>();
const ns = useNotificationStore();

const tasks = ref<Task[]>([]);
const loading = ref(true);
const newTaskTitle = ref("");
const newTaskStatus = ref("todo");
const showNewTask = ref<string | null>(null);
const dragTaskId = ref<string | null>(null);
const dragOverColumn = ref<string | null>(null);

const COLUMNS = [
  { key: "todo", label: "待办" },
  { key: "doing", label: "进行中" },
  { key: "done", label: "已完成" },
];

const tasksByColumn = computed(() => {
  const map: Record<string, Task[]> = { todo: [], doing: [], done: [] };
  for (const t of tasks.value) {
    const col = map[t.status] || map.todo;
    col.push(t);
  }
  // Sort by order_idx within each column
  for (const col of Object.values(map)) col.sort((a, b) => (a.order_idx ?? 0) - (b.order_idx ?? 0));
  return map;
});

async function loadTasks() {
  loading.value = true;
  try {
    tasks.value = await projectsApi.tasks(props.projectId);
  } catch {
    ns.toast("加载任务失败", "error");
  } finally {
    loading.value = false;
  }
}

async function createTask(status: string) {
  const title = newTaskTitle.value.trim();
  if (!title) return;
  try {
    const t = await projectsApi.createTask(props.projectId, { title });
    // Set status if not the default "todo"
    if (status !== "todo") {
      await projectsApi.updateTask(t.id, { status });
      t.status = status;
    }
    tasks.value.push(t);
    newTaskTitle.value = "";
    showNewTask.value = null;
    ns.toast("任务已创建");
  } catch {
    ns.toast("创建失败", "error");
  }
}

async function deleteTask(id: string) {
  if (!confirm("确定删除此任务？")) return;
  try {
    await projectsApi.deleteTask(id);
    tasks.value = tasks.value.filter((t) => t.id !== id);
    ns.toast("已删除");
  } catch {
    ns.toast("删除失败", "error");
  }
}

function onDragStart(e: DragEvent, taskId: string) {
  dragTaskId.value = taskId;
  if (e.dataTransfer) {
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", taskId);
  }
}

function onDragEnd() {
  dragTaskId.value = null;
  dragOverColumn.value = null;
}

function onColumnDragOver(e: DragEvent, column: string) {
  e.preventDefault();
  dragOverColumn.value = column;
}

function onColumnDragLeave(column: string) {
  if (dragOverColumn.value === column) dragOverColumn.value = null;
}

async function onColumnDrop(e: DragEvent, column: string) {
  e.preventDefault();
  const taskId = dragTaskId.value || e.dataTransfer?.getData("text/plain");
  dragOverColumn.value = null;
  dragTaskId.value = null;
  if (!taskId) return;
  const task = tasks.value.find((t) => t.id === taskId);
  if (!task || task.status === column) return;
  task.status = column;
  // Reorder: put at end of the target column
  const colTasks = tasksByColumn.value[column] || [];
  task.order_idx = colTasks.length;
  try {
    await projectsApi.updateTask(task.id, { status: column, order_idx: task.order_idx });
  } catch {
    ns.toast("移动失败", "error");
    await loadTasks(); // Reload on failure
  }
}

onMounted(loadTasks);
</script>

<template>
  <div class="kanban">
    <div class="kanban-columns">
      <div
        v-for="col in COLUMNS"
        :key="col.key"
        class="kanban-col"
        :class="{ 'drag-over': dragOverColumn === col.key }"
        @dragover="onColumnDragOver($event, col.key)"
        @dragleave="onColumnDragLeave(col.key)"
        @drop="onColumnDrop($event, col.key)"
      >
        <div class="kanban-col-head">
          <span>{{ col.label }}</span>
          <span class="kanban-count">{{ tasksByColumn[col.key]?.length || 0 }}</span>
          <button class="kanban-add" title="添加任务" @click="showNewTask = showNewTask === col.key ? null : col.key; newTaskStatus = col.key">+</button>
        </div>
        <div class="kanban-col-body">
          <div v-if="showNewTask === col.key" class="kanban-new">
            <input
              v-model="newTaskTitle"
              class="kanban-new-input"
              placeholder="任务标题…"
              @keydown.enter="createTask(col.key)"
              @keydown.escape="showNewTask = null"
              autofocus
            />
          </div>
          <div
            v-for="t in tasksByColumn[col.key]"
            :key="t.id"
            class="kanban-card"
            :class="{ dragging: dragTaskId === t.id }"
            draggable="true"
            @dragstart="onDragStart($event, t.id)"
            @dragend="onDragEnd"
          >
            <div class="kanban-card-title">{{ t.title }}</div>
            <div class="kanban-card-meta">
              <span v-if="t.agent_id" class="kanban-card-agent">{{ t.agent_id }}</span>
              <button class="kanban-card-del" title="删除" @click="deleteTask(t.id)">
                <Icon name="close" :size="10" />
              </button>
            </div>
          </div>
          <div v-if="!tasksByColumn[col.key]?.length && showNewTask !== col.key" class="kanban-empty">
            暂无任务
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.kanban {
  width: 100%;
}
.kanban-columns {
  display: flex;
  gap: 12px;
  align-items: flex-start;
}
.kanban-col {
  flex: 1;
  min-width: 240px;
  background: var(--bg-panel);
  border-radius: 12px;
  border: 1px solid var(--rule-soft);
  display: flex;
  flex-direction: column;
  min-height: 200px;
  transition: border-color 0.15s, background 0.15s;
}
.kanban-col.drag-over {
  border-color: var(--accent);
  background: var(--accent-tint);
}
.kanban-col-head {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 10px 12px;
  font-size: 13px;
  font-weight: 600;
  color: var(--ink);
  border-bottom: 1px solid var(--rule-soft);
}
.kanban-count {
  font-size: 11px;
  color: var(--ink-mute);
  background: var(--bg-hover);
  padding: 0 6px;
  border-radius: 999px;
  line-height: 16px;
}
.kanban-add {
  margin-left: auto;
  background: none;
  border: none;
  cursor: pointer;
  color: var(--ink-mute);
  font-size: 16px;
  line-height: 1;
  padding: 0 4px;
}
.kanban-add:hover {
  color: var(--accent);
}
.kanban-col-body {
  padding: 8px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.kanban-card {
  background: var(--bg-canvas);
  border: 1px solid var(--rule);
  border-radius: 8px;
  padding: 8px 10px;
  cursor: grab;
  transition: border-color 0.1s;
}
.kanban-card:hover {
  border-color: var(--accent);
}
.kanban-card.dragging {
  opacity: 0.4;
  cursor: grabbing;
}
.kanban-card-title {
  font-size: 12.5px;
  color: var(--ink);
  line-height: 1.4;
}
.kanban-card-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 6px;
}
.kanban-card-agent {
  font-size: 10px;
  color: var(--ink-mute);
  background: var(--accent-tint);
  color: var(--accent-deep);
  padding: 1px 6px;
  border-radius: 4px;
}
.kanban-card-del {
  background: none;
  border: none;
  cursor: pointer;
  color: var(--ink-faint);
  padding: 2px;
  opacity: 0;
  transition: opacity 0.1s;
}
.kanban-card:hover .kanban-card-del {
  opacity: 1;
}
.kanban-card-del:hover {
  color: var(--danger);
}
.kanban-empty {
  text-align: center;
  padding: 16px 8px;
  font-size: 11px;
  color: var(--ink-faint);
  font-style: italic;
}
.kanban-new {
  padding: 2px 0;
}
.kanban-new-input {
  width: 100%;
  box-sizing: border-box;
  background: var(--bg-canvas);
  border: 1px solid var(--accent);
  border-radius: 6px;
  padding: 6px 8px;
  font-size: 12px;
  color: var(--ink);
  outline: none;
  font-family: inherit;
}
</style>
