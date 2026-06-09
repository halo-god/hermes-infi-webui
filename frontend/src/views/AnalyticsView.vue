<script setup lang="ts">
import { onMounted, ref } from "vue";
import { NCard, NDataTable, NStatistic, NSpin } from "naive-ui";
import { analyticsApi, type UsageStats } from "@/api/analytics";
import Icon from "@/components/Icon.vue";

const stats = ref<UsageStats | null>(null);
const loading = ref(true);

onMounted(async () => {
  try {
    stats.value = await analyticsApi.usage();
  } catch {
    stats.value = null;
  } finally {
    loading.value = false;
  }
});

const dayColumns = [
  { title: "日期", key: "date", width: 140 },
  { title: "消息数", key: "count", width: 100, sorter: (a: any, b: any) => a.count - b.count },
];

const agentColumns = [
  { title: "助手", key: "agent_id", width: 160 },
  { title: "消息数", key: "count", width: 100, sorter: (a: any, b: any) => a.count - b.count },
];

const roleLabels: Record<string, string> = {
  user: "用户",
  agent: "助手",
  roundtable: "圆桌",
};
</script>

<template>
  <div class="analytics-page">
    <div class="analytics-head">
      <Icon name="sparkle" :size="20" />
      <h2>用量分析</h2>
    </div>

    <NSpin :show="loading">
      <div v-if="stats" class="analytics-content">
        <!-- Stat cards -->
        <div class="stat-grid">
          <NCard size="small" class="stat-card">
            <NStatistic label="总消息数" :value="stats.total_messages" />
          </NCard>
          <NCard size="small" class="stat-card">
            <NStatistic label="总会话数" :value="stats.total_conversations" />
          </NCard>
          <NCard size="small" class="stat-card">
            <NStatistic label="Token 总量" :value="stats.tokens_total" />
          </NCard>
          <NCard size="small" class="stat-card">
            <NStatistic label="活跃天数" :value="stats.messages_by_day.length" />
          </NCard>
        </div>

        <!-- Role breakdown -->
        <NCard title="消息分布" size="small" class="section-card">
          <div class="role-grid">
            <div v-for="(count, role) in stats.messages_by_role" :key="role" class="role-item">
              <span class="role-label">{{ roleLabels[role] || role }}</span>
              <span class="role-count">{{ count }}</span>
            </div>
            <div v-if="!Object.keys(stats.messages_by_role).length" class="empty-hint">
              暂无数据
            </div>
          </div>
        </NCard>

        <div class="tables-grid">
          <!-- Messages by day -->
          <NCard title="近30天消息趋势" size="small" class="section-card">
            <NDataTable
              :columns="dayColumns"
              :data="stats.messages_by_day"
              :max-height="360"
              :scrollbar-props="{ trigger: 'hover' }"
              size="small"
            />
          </NCard>

          <!-- Top agents -->
          <NCard title="热门助手 TOP 5" size="small" class="section-card">
            <NDataTable
              :columns="agentColumns"
              :data="stats.top_agents"
              :max-height="360"
              size="small"
            />
          </NCard>
        </div>
      </div>

      <div v-else-if="!loading" class="empty-hint" style="padding: 48px; text-align: center">
        加载失败，请重试
      </div>
    </NSpin>
  </div>
</template>

<style scoped>
.analytics-page {
  max-width: 1400px;
  margin: 0 auto;
  padding: 32px 24px;
}
.analytics-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 24px;
  color: var(--ink);
}
.analytics-head h2 {
  font-family: var(--font-serif);
  font-size: 22px;
  font-weight: 500;
  margin: 0;
}
.stat-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 14px;
  margin-bottom: 20px;
}
.stat-card {
  background: var(--bg-panel);
}
.section-card {
  background: var(--bg-panel);
  margin-bottom: 20px;
}
.role-grid {
  display: flex;
  gap: 24px;
  flex-wrap: wrap;
}
.role-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.role-label {
  font-size: 12px;
  color: var(--ink-mute);
}
.role-count {
  font-size: 24px;
  font-weight: 600;
  color: var(--ink);
  font-family: var(--font-mono);
}
.tables-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 14px;
}
.empty-hint {
  color: var(--ink-mute);
  font-size: 13px;
}
</style>
