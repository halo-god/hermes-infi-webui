<script setup lang="ts">
/* 定时任务页 — 真实 CRUD，后端 cron 调度循环自动触发 ACP agent 执行。 */
import { onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import Icon from "@/components/Icon.vue";
import { useChatStore } from "@/stores/chat";
import { useBrandingStore } from "@/stores/branding";
import { useNotificationStore } from "@/stores/notifications";
import { scheduledApi } from "@/api/scheduled";
import type { ScheduledTask } from "@/types";

const router = useRouter();
const chat = useChatStore();
const branding = useBrandingStore();
const ns = useNotificationStore();

const tasks = ref<ScheduledTask[]>([]);
const loading = ref(true);
const showForm = ref(false);
const editingId = ref<string | null>(null);

// Form state
const form = ref({ name: "", agent_id: "", prompt: "", cron: "0 0 9 * * *", enabled: true });
const saving = ref(false);

// Cron presets
const CRON_PRESETS = [
  { label: "每天 09:00", cron: "0 0 9 * * *" },
  { label: "每天 18:00", cron: "0 0 18 * * *" },
  { label: "每周一 09:00", cron: "0 0 9 * * 1" },
  { label: "每周五 17:00", cron: "0 0 17 * * 5" },
  { label: "每月 1 号 10:00", cron: "0 0 10 1 * *" },
  { label: "每小时", cron: "0 0 * * * *" },
];

function agentById(id: string) {
  const p = chat.profiles.find((pp) => pp.default_agent_id === id);
  return { label: p?.name || id, color: p?.color || branding.accent, icon: p?.icon || "sparkle" };
}

function statusLabel(s: string | null) {
  if (!s) return "—";
  return { success: "成功", failed: "失败", running: "执行中" }[s] || s;
}

function statusColor(s: string | null) {
  return { success: "var(--ok)", failed: "var(--danger)", running: "var(--accent)" }[s || ""] || "var(--ink-mute)";
}

function fmtDate(iso: string | null) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
  } catch {
    return iso;
  }
}

async function loadTasks() {
  loading.value = true;
  try {
    tasks.value = await scheduledApi.list();
  } catch (e) {
    console.error("[scheduled] load failed:", e);
    ns.toast("加载失败", "error");
  } finally {
    loading.value = false;
  }
}

function openCreate() {
  editingId.value = null;
  form.value = { name: "", agent_id: chat.profiles[0]?.default_agent_id || "hermes", prompt: "", cron: "0 0 9 * * *", enabled: true };
  showForm.value = true;
}

function openEdit(t: ScheduledTask) {
  editingId.value = t.id;
  form.value = { name: t.name, agent_id: t.agent_id, prompt: t.prompt, cron: t.cron, enabled: t.enabled };
  showForm.value = true;
}

async function save() {
  if (!form.value.name.trim() || !form.value.prompt.trim()) {
    ns.toast("请填写名称和指令", "error");
    return;
  }
  saving.value = true;
  try {
    if (editingId.value) {
      await scheduledApi.update(editingId.value, form.value);
      ns.toast("已更新");
    } else {
      await scheduledApi.create(form.value);
      ns.toast("已创建");
    }
    showForm.value = false;
    await loadTasks();
  } catch (e: unknown) {
    ns.toast("保存失败：" + ((e as Error)?.message || "未知错误"), "error");
  } finally {
    saving.value = false;
  }
}

async function removeTask(id: string, name: string) {
  if (!confirm(`确定删除定时任务「${name}」？`)) return;
  try {
    await scheduledApi.remove(id);
    ns.toast("已删除");
    await loadTasks();
  } catch {
    ns.toast("删除失败", "error");
  }
}

async function toggle(t: ScheduledTask) {
  try {
    await scheduledApi.toggle(t.id, !t.enabled);
    await loadTasks();
  } catch {
    ns.toast("操作失败", "error");
  }
}

onMounted(() => {
  void loadTasks();
});
</script>

<template>
  <div class="stage">
    <div class="landing" style="padding-top: 60px">
      <div class="landing-inner">
        <h1 class="hello" style="font-size: 34px"><em>定时任务</em></h1>
        <div class="hello-sub">让{{ branding.shortName }}在指定时刻替你跑腿。</div>

        <div style="width: 100%; max-width: 680px; margin-top: 24px; display: flex; flex-direction: column; gap: 10px">
          <!-- Loading -->
          <div v-if="loading" style="text-align: center; padding: 40px; color: var(--ink-mute)">加载中…</div>

          <!-- Empty -->
          <div v-else-if="!tasks.length && !showForm" style="text-align: center; padding: 40px; color: var(--ink-mute)">
            还没有定时任务。点击下方按钮创建第一个。
          </div>

          <!-- Task list -->
          <template v-for="t in tasks" :key="t.id">
            <div
              class="sched-row"
              :class="{ disabled: !t.enabled }"
              @click="openEdit(t)"
            >
              <div class="sched-icon" :style="{ background: agentById(t.agent_id).color }">
                <Icon :name="agentById(t.agent_id).icon || 'sparkle'" :size="14" />
              </div>
              <div class="sched-body">
                <div class="sched-name">{{ t.name }}</div>
                <div class="sched-meta">
                  <span>🕐 {{ t.cron }}</span>
                  <span>由 {{ agentById(t.agent_id).label }} 执行</span>
                  <span v-if="t.enabled">下次：{{ fmtDate(t.next_run_at) }}</span>
                  <span v-else>已暂停</span>
                  <span :style="{ color: statusColor(t.last_status) }">{{ statusLabel(t.last_status) }}</span>
                </div>
              </div>
              <div class="sched-actions" @click.stop>
                <button
                  class="icon-btn"
                  :title="t.enabled ? '暂停' : '启用'"
                  @click="toggle(t)"
                >
                  <Icon :name="t.enabled ? 'moon' : 'sun'" :size="14" />
                </button>
                <button class="icon-btn danger" title="删除" @click="removeTask(t.id, t.name)">
                  <Icon name="close" :size="14" />
                </button>
              </div>
            </div>
          </template>

          <!-- Create/Edit form -->
          <div v-if="showForm" class="sched-form">
            <div class="sched-form-title">{{ editingId ? '编辑定时任务' : '新建定时任务' }}</div>
            <label class="sched-field">
              <span>任务名称</span>
              <input v-model="form.name" placeholder="如：每周五生成周报草稿" />
            </label>
            <label class="sched-field">
              <span>执行 Agent</span>
              <select v-model="form.agent_id">
                <option v-for="p in chat.profiles" :key="p.default_agent_id" :value="p.default_agent_id">
                  {{ p.name }} ({{ p.default_agent_id }})
                </option>
              </select>
            </label>
            <label class="sched-field">
              <span>指令内容</span>
              <textarea v-model="form.prompt" rows="4" placeholder="发给 Agent 的 prompt，如：请生成本周的工作周报草稿…" />
            </label>
            <label class="sched-field">
              <span>Cron 表达式</span>
              <input v-model="form.cron" placeholder="如：0 0 9 * * *（每天 09:00）" />
              <div class="cron-presets">
                <button v-for="p in CRON_PRESETS" :key="p.cron" class="cron-preset" @click="form.cron = p.cron">
                  {{ p.label }}
                </button>
              </div>
            </label>
            <label class="sched-field-row">
              <input type="checkbox" v-model="form.enabled" />
              <span>启用</span>
            </label>
            <div class="sched-form-actions">
              <button class="btn" @click="showForm = false">取消</button>
              <button class="btn primary" :disabled="saving" @click="save">
                {{ saving ? '保存中…' : '保存' }}
              </button>
            </div>
          </div>

          <!-- New button -->
          <button
            v-if="!showForm"
            class="btn primary"
            style="align-self: flex-start; display: inline-flex; align-items: center; gap: 5px; margin-top: 6px"
            @click="openCreate"
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

<style scoped>
.sched-row {
  background: var(--bg-panel);
  border: 1px solid var(--rule);
  border-radius: 14px;
  padding: 14px 16px;
  display: flex;
  align-items: center;
  gap: 12px;
  cursor: pointer;
  transition: border-color 0.15s;
}
.sched-row:hover {
  border-color: var(--accent);
}
.sched-row.disabled {
  opacity: 0.55;
}
.sched-icon {
  width: 30px;
  height: 30px;
  border-radius: 8px;
  flex-shrink: 0;
  display: grid;
  place-items: center;
  color: white;
}
.sched-body {
  flex: 1;
  min-width: 0;
}
.sched-name {
  font-weight: 600;
  color: var(--ink);
  font-size: 13.5px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.sched-meta {
  font-size: 11.5px;
  color: var(--ink-mute);
  margin-top: 3px;
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
}
.sched-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
}
.sched-form {
  background: var(--bg-panel);
  border: 1px solid var(--accent);
  border-radius: 14px;
  padding: 18px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.sched-form-title {
  font-weight: 600;
  font-size: 15px;
  color: var(--ink);
}
.sched-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.sched-field > span {
  font-size: 12px;
  font-weight: 500;
  color: var(--ink-mute);
}
.sched-field input,
.sched-field select,
.sched-field textarea {
  background: var(--bg-canvas);
  border: 1px solid var(--rule);
  border-radius: 8px;
  padding: 8px 10px;
  font-size: 13px;
  color: var(--ink);
  outline: none;
  font-family: inherit;
}
.sched-field input:focus,
.sched-field select:focus,
.sched-field textarea:focus {
  border-color: var(--accent);
}
.sched-field-row {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--ink);
}
.cron-presets {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-top: 2px;
}
.cron-preset {
  font-size: 11px;
  padding: 3px 8px;
  border-radius: 5px;
  border: 1px solid var(--rule);
  background: var(--bg-canvas);
  color: var(--ink-mute);
  cursor: pointer;
}
.cron-preset:hover {
  border-color: var(--accent);
  color: var(--accent);
}
.sched-form-actions {
  display: flex;
  gap: 8px;
  justify-content: flex-end;
}
.icon-btn.danger:hover {
  color: var(--danger);
}
</style>
