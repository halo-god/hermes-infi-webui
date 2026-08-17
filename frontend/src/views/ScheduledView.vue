<script setup lang="ts">
/* 定时任务页 — 真实 CRUD，后端 cron 调度循环自动触发 ACP agent 执行。 */
import { computed, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import Icon from "@/components/Icon.vue";
import { useChatStore } from "@/stores/chat";
import { useBrandingStore } from "@/stores/branding";
import { useNotificationStore } from "@/stores/notifications";
import { scheduledApi } from "@/api/scheduled";
import type { ScheduledTask } from "@/types";

const chat = useChatStore();
const branding = useBrandingStore();
const ns = useNotificationStore();
const router = useRouter();

function viewResults(conversationId: string) {
  router.push(`/?c=${conversationId}`);
}

const tasks = ref<ScheduledTask[]>([]);
const loading = ref(true);
const showForm = ref(false);
const editingId = ref<string | null>(null);

// Form state
const form = ref({ name: "", agent_id: "", profile_id: "" as string, prompt: "", cron: "0 9 * * *", enabled: true });
const saving = ref(false);

// ── 可视化调度配置 ──
type ScheduleType = "daily" | "weekly" | "monthly" | "hourly" | "custom";
const scheduleType = ref<ScheduleType>("daily");
const scheduleTime = ref("09:00");
const scheduleWeekdays = ref<number[]>([1]);
const scheduleDayOfMonth = ref(1);
const scheduleEveryNHours = ref(1);
const WEEKDAY_LABELS = ["日", "一", "二", "三", "四", "五", "六"];

function buildCron(): string {
  const [h, m] = scheduleTime.value.split(":").map(Number);
  // 5-field cron: min hour day-of-month month day-of-week
  switch (scheduleType.value) {
    case "daily": return `${m || 0} ${h || 0} * * *`;
    case "weekly": return `${m || 0} ${h || 0} * * ${scheduleWeekdays.value.join(",") || "*"}`;
    case "monthly": return `${m || 0} ${h || 0} ${scheduleDayOfMonth.value} * *`;
    // Keep the minute from scheduleTime so editing `30 */2 * * *` round-trips
    // instead of silently rewriting it to `0 */2 * * *`.
    case "hourly": return `${m || 0} */${Math.max(1, scheduleEveryNHours.value)} * * *`;
    default: return form.value.cron;
  }
}
function parseCron(cron: string) {
  const parts = cron.trim().split(/\s+/);
  if (parts.length < 5) { scheduleType.value = "custom"; return; }
  // 5-field: min hour dom month dow
  const [m, h, dom, , dow] = parts;
  // Any minute is valid for hourly (`30 */2 * * *` is hourly too) — the m==="0"
  // requirement used to misparse it as daily and silently rewrite the schedule.
  if (h.startsWith("*/")) {
    scheduleType.value = "hourly";
    scheduleEveryNHours.value = parseInt(h.slice(2)) || 1;
    scheduleTime.value = `${String(parseInt(h) || 0).padStart(2, "0")}:${String(parseInt(m) || 0).padStart(2, "0")}`;
    return;
  }
  if (dom === "*" && dow !== "*" && !dow.includes("/")) { scheduleType.value = "weekly"; scheduleTime.value = `${String(parseInt(h)).padStart(2,"0")}:${String(parseInt(m)).padStart(2,"0")}`; scheduleWeekdays.value = dow.split(",").map(Number).filter((n) => !isNaN(n)); return; }
  if (dom !== "*" && dow === "*") { scheduleType.value = "monthly"; scheduleTime.value = `${String(parseInt(h)).padStart(2,"0")}:${String(parseInt(m)).padStart(2,"0")}`; scheduleDayOfMonth.value = parseInt(dom) || 1; return; }
  if (dom === "*" && dow === "*") { scheduleType.value = "daily"; scheduleTime.value = `${String(parseInt(h)).padStart(2,"0")}:${String(parseInt(m)).padStart(2,"0")}`; return; }
  // Also handle 6-field (legacy) by skipping first field
  if (parts.length >= 6) {
    const [, m6, h6, dom6, , dow6] = parts;
    if (dom6 === "*" && dow6 !== "*" && !dow6.includes("/")) { scheduleType.value = "weekly"; scheduleTime.value = `${String(parseInt(h6)).padStart(2,"0")}:${String(parseInt(m6)).padStart(2,"0")}`; scheduleWeekdays.value = dow6.split(",").map(Number).filter((n) => !isNaN(n)); return; }
    if (dom6 !== "*" && dow6 === "*") { scheduleType.value = "monthly"; scheduleTime.value = `${String(parseInt(h6)).padStart(2,"0")}:${String(parseInt(m6)).padStart(2,"0")}`; scheduleDayOfMonth.value = parseInt(dom6) || 1; return; }
    if (dom6 === "*" && dow6 === "*") { scheduleType.value = "daily"; scheduleTime.value = `${String(parseInt(h6)).padStart(2,"0")}:${String(parseInt(m6)).padStart(2,"0")}`; return; }
  }
  scheduleType.value = "custom";
}
function toggleWeekday(d: number) {
  const i = scheduleWeekdays.value.indexOf(d);
  if (i >= 0) scheduleWeekdays.value.splice(i, 1); else scheduleWeekdays.value.push(d);
  scheduleWeekdays.value.sort();
}
const cronPreview = computed(() => scheduleType.value === "custom" ? form.value.cron : buildCron());
const cronHuman = computed(() => {
  switch (scheduleType.value) {
    case "daily": return `每天 ${scheduleTime.value}`;
    case "weekly": return `每周${scheduleWeekdays.value.map((d) => WEEKDAY_LABELS[d]).join("、")} ${scheduleTime.value}`;
    case "monthly": return `每月${scheduleDayOfMonth.value}号 ${scheduleTime.value}`;
    case "hourly": return `每 ${scheduleEveryNHours.value} 小时`;
    default: return "自定义 Cron";
  }
});

function describeCron(cron: string): string {
  // Per-row cron description — the shared cronHuman computed reflects the
  // edit form's last parse, so every row would show the same time.
  const parts = cron.trim().split(/\s+/);
  if (parts.length >= 6) parts.shift();  // legacy 6-field: drop seconds
  if (parts.length < 5) return "自定义 Cron";
  const [m, h, dom, , dow] = parts;
  if (h.startsWith("*/")) return `每 ${parseInt(h.slice(2))} 小时`;
  const time = `${String(parseInt(h)).padStart(2, "0")}:${String(parseInt(m)).padStart(2, "0")}`;
  if (dom === "*" && dow === "*") return `每天 ${time}`;
  if (dom === "*") return `每周${dow.split(",").map((d) => WEEKDAY_LABELS[Number(d)]).filter(Boolean).join("、")} ${time}`;
  if (dow === "*") return `每月${parseInt(dom)}号 ${time}`;
  return "自定义 Cron";
}

function profileById(id: string | null) {
  if (!id) return { label: "默认", color: branding.accent, icon: "sparkle" };
  const p = chat.profiles.find((pp) => pp.id === id);
  return { label: p?.name || "未知助手", color: p?.color || branding.accent, icon: p?.icon || "sparkle" };
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
  form.value = { name: "", agent_id: chat.profiles[0]?.default_agent_id || "hermes", profile_id: chat.profiles[0]?.id || "", prompt: "", cron: "0 9 * * *", enabled: true };
  scheduleType.value = "daily"; scheduleTime.value = "09:00"; scheduleWeekdays.value = [1]; scheduleDayOfMonth.value = 1; scheduleEveryNHours.value = 1;
  showForm.value = true;
}

function openEdit(t: ScheduledTask) {
  editingId.value = t.id;
  form.value = { name: t.name, agent_id: t.agent_id, profile_id: t.profile_id || "", prompt: t.prompt, cron: t.cron, enabled: t.enabled };
  parseCron(t.cron);
  showForm.value = true;
}

async function save() {
  if (!form.value.name.trim() || !form.value.prompt.trim()) {
    ns.toast("请填写名称和指令", "error");
    return;
  }
  saving.value = true;
  try {
    // 从可视化配置生成 cron + 同步 agent_id
    form.value.cron = buildCron();
    const sel = chat.profiles.find((p) => p.id === form.value.profile_id);
    if (sel) form.value.agent_id = sel.default_agent_id;
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
  } catch (e: unknown) {
    const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
    ns.toast(detail || "操作失败", "error");
  }
}

onMounted(() => {
  void loadTasks();
});
</script>

<template>
  <div class="stage">
    <div class="admin-hero">
      <div class="admin-hero-row">
        <span class="admin-badge"><Icon name="clock" :size="11" /> SCHEDULED</span>
        <span style="font-size: 11.5px; color: var(--ink-mute); font-family: var(--font-mono)">{{ branding.tenantName }}</span>
      </div>
      <h1 class="admin-title">定时<em>任务</em></h1>
      <div class="admin-sub">让 {{ branding.shortName }} 在指定时刻替你跑腿。</div>
    </div>

    <div class="admin-body">
      <!-- Stat cards -->
      <div class="stat-grid" style="grid-template-columns: repeat(3, 1fr); margin-bottom: 20px">
        <div class="stat">
          <div class="stat-label">全部任务</div>
          <div class="stat-value">{{ tasks.length }}</div>
        </div>
        <div class="stat">
          <div class="stat-label">运行中</div>
          <div class="stat-value" style="color: var(--ok)">{{ tasks.filter(t => t.enabled).length }}</div>
        </div>
        <div class="stat">
          <div class="stat-label">已暂停</div>
          <div class="stat-value" style="color: var(--ink-mute)">{{ tasks.filter(t => !t.enabled).length }}</div>
        </div>
      </div>

      <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px">
        <h2 style="margin: 0; font-size: 15px; font-weight: 600; color: var(--ink)"><Icon name="clock" :size="15" /> 任务列表</h2>
        <button class="btn primary" @click="openCreate"><Icon name="plus" :size="13" /> 新建任务</button>
      </div>

      <!-- Task list -->
      <div v-if="loading" style="text-align: center; padding: 40px; color: var(--ink-mute)">加载中…</div>
      <div v-else-if="!tasks.length && !showForm" style="text-align: center; padding: 40px; color: var(--ink-mute)">
        还没有定时任务。
      </div>

      <template v-for="t in tasks" :key="t.id">
        <div
          class="section-card sched-row"
          :class="{ disabled: !t.enabled }"
          style="margin-bottom: 10px; cursor: pointer"
          @click="openEdit(t)"
        >
          <div style="padding: 14px 18px; display: flex; align-items: center; gap: 12px">
            <div class="sched-icon" :style="{ background: profileById(t.profile_id).color }">
              <Icon :name="profileById(t.profile_id).icon || 'sparkle'" :size="14" />
            </div>
            <div class="sched-body" style="flex: 1; min-width: 0">
              <div class="sched-name" style="font-size: 13.5px; font-weight: 600; color: var(--ink)">{{ t.name }}</div>
              <div class="sched-meta" style="font-size: 11.5px; color: var(--ink-mute); margin-top: 3px; display: flex; gap: 12px; flex-wrap: wrap">
                <span>🕐 {{ describeCron(t.cron) }}</span>
                <span>由 {{ profileById(t.profile_id).label }} 执行</span>
                <span v-if="t.enabled">下次：{{ fmtDate(t.next_run_at) }}</span>
                <span v-else>已暂停</span>
                <span :style="{ color: statusColor(t.last_status) }">{{ statusLabel(t.last_status) }}</span>
                <span v-if="t.success_count || t.fail_count" style="font-size: 11px">
                  <span style="color: var(--ok)">✓ {{ t.success_count }}</span>
                  <span v-if="t.fail_count" style="color: var(--danger)"> ✗ {{ t.fail_count }}</span>
                </span>
              </div>
            </div>
            <div class="sched-actions" style="display: flex; gap: 6px" @click.stop>
              <button v-if="t.conversation_id" class="icon-btn" title="查看执行结果" @click="viewResults(t.conversation_id!)">
                <Icon name="chat" :size="14" />
              </button>
              <button class="icon-btn" :title="t.enabled ? '暂停' : '启用'" @click="toggle(t)">
                <Icon :name="t.enabled ? 'moon' : 'sun'" :size="14" />
              </button>
              <button class="icon-btn" style="color: var(--danger)" title="删除" @click="removeTask(t.id, t.name)">
                <Icon name="close" :size="14" />
              </button>
            </div>
          </div>
        </div>
      </template>

      <!-- Create/Edit form -->
      <div v-if="showForm" class="section-card" style="margin-top: 16px">
        <div class="section-head"><div class="section-title"><Icon name="edit" /> {{ editingId ? '编辑定时任务' : '新建定时任务' }}</div></div>
        <div style="padding: 18px; display: flex; flex-direction: column; gap: 12px">
          <label style="display: flex; flex-direction: column; gap: 4px">
            <span style="font-size: 12px; font-weight: 500; color: var(--ink-mute)">任务名称</span>
            <input v-model="form.name" class="cfg-input" placeholder="如：每周五生成周报草稿" />
          </label>
          <label style="display: flex; flex-direction: column; gap: 4px">
            <span style="font-size: 12px; font-weight: 500; color: var(--ink-mute)">执行 Agent</span>
            <select v-model="form.profile_id" class="cfg-input">
              <option v-for="p in chat.profiles" :key="p.id" :value="p.id">
                {{ p.name }}
              </option>
            </select>
          </label>
          <label style="display: flex; flex-direction: column; gap: 4px">
            <span style="font-size: 12px; font-weight: 500; color: var(--ink-mute)">指令内容</span>
            <textarea v-model="form.prompt" class="cfg-input" rows="4" placeholder="发给 Agent 的 prompt，如：请生成本周的工作周报草稿…" style="resize: vertical; font-family: inherit" />
          </label>
          <!-- 可视化调度配置 -->
          <div style="display: flex; flex-direction: column; gap: 8px">
            <span style="font-size: 12px; font-weight: 500; color: var(--ink-mute)">执行频率</span>
            <!-- 频率类型选择 -->
            <div style="display: flex; gap: 6px; flex-wrap: wrap">
              <button v-for="t in (['daily','weekly','monthly','hourly','custom'] as const)" :key="t"
                style="font-size: 12px; padding: 5px 12px; border-radius: 6px; cursor: pointer; border: 1px solid var(--rule)"
                :style="{ background: scheduleType === t ? 'var(--accent)' : 'var(--bg-canvas)', color: scheduleType === t ? '#fff' : 'var(--ink-mute)', borderColor: scheduleType === t ? 'var(--accent)' : 'var(--rule)' }"
                @click="scheduleType = t">
                {{ ({ daily: '每天', weekly: '每周', monthly: '每月', hourly: '每隔N小时', custom: '自定义' })[t] }}
              </button>
            </div>
            <!-- 每天/每周/每月: 时间选择 -->
            <div v-if="scheduleType === 'daily' || scheduleType === 'weekly' || scheduleType === 'monthly'" style="display: flex; align-items: center; gap: 8px">
              <span style="font-size: 12px; color: var(--ink-mute)">时间</span>
              <input type="time" v-model="scheduleTime" class="cfg-input" style="width: auto" />
            </div>
            <!-- 每周: 星期选择 -->
            <div v-if="scheduleType === 'weekly'" style="display: flex; align-items: center; gap: 6px; flex-wrap: wrap">
              <span style="font-size: 12px; color: var(--ink-mute)">星期</span>
              <button v-for="(lbl, d) in WEEKDAY_LABELS" :key="d"
                style="font-size: 11px; width: 26px; height: 26px; border-radius: 50%; cursor: pointer; border: 1px solid var(--rule)"
                :style="{ background: scheduleWeekdays.includes(d) ? 'var(--accent)' : 'var(--bg-canvas)', color: scheduleWeekdays.includes(d) ? '#fff' : 'var(--ink-mute)' }"
                @click="toggleWeekday(d)">{{ lbl }}</button>
            </div>
            <!-- 每月: 日期选择 -->
            <div v-if="scheduleType === 'monthly'" style="display: flex; align-items: center; gap: 8px">
              <span style="font-size: 12px; color: var(--ink-mute)">每月</span>
              <input type="number" min="1" max="28" v-model="scheduleDayOfMonth" class="cfg-input" style="width: 60px" />
              <span style="font-size: 12px; color: var(--ink-mute)">号</span>
            </div>
            <!-- 每隔N小时 -->
            <div v-if="scheduleType === 'hourly'" style="display: flex; align-items: center; gap: 8px">
              <span style="font-size: 12px; color: var(--ink-mute)">每</span>
              <input type="number" min="1" max="24" v-model="scheduleEveryNHours" class="cfg-input" style="width: 60px" />
              <span style="font-size: 12px; color: var(--ink-mute)">小时执行一次</span>
            </div>
            <!-- 自定义 cron -->
            <div v-if="scheduleType === 'custom'">
              <input v-model="form.cron" class="cfg-input" placeholder="如：0 0 9 * * *（每天 09:00）" />
            </div>
            <!-- 预览 -->
            <div style="font-size: 11.5px; color: var(--accent); padding: 4px 8px; background: rgba(184,133,42,0.08); border-radius: 5px">
              📅 {{ cronHuman }} <span style="color: var(--ink-faint); font-family: var(--font-mono)">{{ cronPreview }}</span>
            </div>
          </div>
          <label style="display: flex; align-items: center; gap: 6px; font-size: 13px; color: var(--ink)">
            <input type="checkbox" v-model="form.enabled" /> 启用
          </label>
          <div style="display: flex; gap: 8px; justify-content: flex-end">
            <button class="btn" @click="showForm = false">取消</button>
            <button class="btn primary" :disabled="saving" @click="save">{{ saving ? '保存中…' : '保存' }}</button>
          </div>
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
