<script setup lang="ts">
/* 日志查看器 — 实时查看后端日志，支持级别过滤和关键词搜索。 */
import { onMounted, onUnmounted, ref, computed } from "vue";
import Icon from "@/components/Icon.vue";
import { logsApi, type LogEntry } from "@/api/logs";

const entries = ref<LogEntry[]>([]);
const loading = ref(false);
const levelFilter = ref("");
const keyword = ref("");
const autoRefresh = ref(false);
const refreshTimer = ref<ReturnType<typeof setInterval> | null>(null);

const LEVEL_COLORS: Record<string, string> = {
  DEBUG: "var(--ink-mute)",
  INFO: "var(--ok, #3a8a5a)",
  WARNING: "var(--warn, #b8852a)",
  ERROR: "var(--danger, #c04040)",
  CRITICAL: "var(--danger, #c04040)",
};

const filteredEntries = computed(() => {
  let result = entries.value;
  if (levelFilter.value) {
    const levels = LEVEL_ORDER[levelFilter.value];
    if (levels !== undefined) {
      result = result.filter((e) => (LEVEL_ORDER[e.level] ?? 0) >= levels);
    }
  }
  if (keyword.value.trim()) {
    const kw = keyword.value.toLowerCase();
    result = result.filter((e) => e.message.toLowerCase().includes(kw) || e.logger.toLowerCase().includes(kw));
  }
  return result;
});

const LEVEL_ORDER: Record<string, number> = {
  DEBUG: 0,
  INFO: 1,
  WARNING: 2,
  ERROR: 3,
  CRITICAL: 4,
};

async function loadLogs() {
  loading.value = true;
  try {
    const resp = await logsApi.getLogs({ limit: 300 });
    entries.value = resp.entries;
  } catch (e) {
    console.error("[logs] load failed:", e);
  } finally {
    loading.value = false;
  }
}

function toggleAutoRefresh() {
  autoRefresh.value = !autoRefresh.value;
  if (autoRefresh.value) {
    refreshTimer.value = setInterval(loadLogs, 5000);
  } else if (refreshTimer.value) {
    clearInterval(refreshTimer.value);
    refreshTimer.value = null;
  }
}

onMounted(loadLogs);
onUnmounted(() => {
  if (refreshTimer.value) clearInterval(refreshTimer.value);
});
</script>

<template>
  <div class="stage">
    <div class="admin-hero">
      <div class="admin-hero-row">
        <span class="admin-badge"><Icon name="doc" :size="11" /> LOGS</span>
        <span style="font-size: 11.5px; color: var(--ink-mute); font-family: var(--font-mono)">{{ entries.length }} 条记录</span>
      </div>
      <h1 class="admin-title">日志<em>查看</em></h1>
      <div class="admin-sub">实时查看后端日志，支持级别过滤和关键词搜索。</div>
    </div>

    <div class="admin-body">
      <!-- Filter toolbar -->
      <div class="section-card" style="margin-bottom: 16px">
        <div style="padding: 12px 18px; display: flex; gap: 10px; align-items: center; flex-wrap: wrap">
          <select v-model="levelFilter" class="cfg-input" style="width: auto" @change="loadLogs">
            <option value="">全部级别</option>
            <option value="DEBUG">DEBUG</option>
            <option value="INFO">INFO</option>
            <option value="WARNING">WARNING</option>
            <option value="ERROR">ERROR</option>
            <option value="CRITICAL">CRITICAL</option>
          </select>
          <input v-model="keyword" class="cfg-input" style="flex: 1; min-width: 180px" placeholder="关键词搜索…" @keydown.enter="loadLogs" />
          <span style="font-size: 12px; color: var(--ink-mute)">{{ filteredEntries.length }} / {{ entries.length }} 条</span>
          <button class="btn" :class="{ primary: autoRefresh }" @click="toggleAutoRefresh">
            <Icon name="refresh" :size="12" /> {{ autoRefresh ? "自动刷新中" : "自动刷新" }}
          </button>
          <button class="btn" @click="loadLogs" :disabled="loading">
            <Icon name="refresh" :size="12" /> {{ loading ? "加载中…" : "刷新" }}
          </button>
        </div>
      </div>

      <!-- Log list -->
      <div class="section-card">
        <div style="padding: 4px 0; max-height: calc(100vh - 300px); overflow-y: auto; font-family: var(--font-mono, 'SF Mono', monospace); font-size: 12px; line-height: 1.6">
          <div v-if="loading && !entries.length" style="padding: 40px; text-align: center; color: var(--ink-mute)">加载中…</div>
          <div v-else-if="!filteredEntries.length" style="padding: 40px; text-align: center; color: var(--ink-mute)">
            {{ entries.length ? "无匹配日志" : "暂无日志" }}
          </div>
          <div
            v-for="(entry, i) in filteredEntries"
            :key="i"
            class="log-row"
            :class="entry.level.toLowerCase()"
          >
            <span class="log-ts">{{ entry.timestamp }}</span>
            <span class="log-level" :style="{ color: LEVEL_COLORS[entry.level] || 'var(--ink)' }">{{ entry.level }}</span>
            <span class="log-rid" v-if="entry.request_id !== '-'">[{{ entry.request_id }}]</span>
            <span class="log-logger">{{ entry.logger }}</span>
            <span class="log-msg">{{ entry.message }}</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.log-row {
  display: flex;
  gap: 8px;
  padding: 2px 18px;
  border-bottom: 1px solid var(--rule-soft);
  white-space: nowrap;
}
.log-row.error,
.log-row.critical {
  background: rgba(192, 64, 64, 0.06);
}
.log-row.warning {
  background: rgba(184, 133, 42, 0.04);
}
.log-ts { color: var(--ink-faint); flex-shrink: 0; }
.log-level { font-weight: 700; flex-shrink: 0; min-width: 70px; }
.log-rid { color: var(--accent); flex-shrink: 0; opacity: 0.7; }
.log-logger { color: var(--ink-mute); flex-shrink: 0; max-width: 200px; overflow: hidden; text-overflow: ellipsis; }
.log-msg { color: var(--ink); overflow: hidden; text-overflow: ellipsis; }
</style>
