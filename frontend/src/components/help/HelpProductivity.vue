<script setup lang="ts">
/* 帮助中心 · 效率工具 — 历史、定时任务、搜索与个性化。 */
import Icon from "@/components/Icon.vue";

const SHORTCUTS = [
  { keys: "⌘K / Ctrl+K", desc: "打开全局搜索面板（会话与助手）" },
  { keys: "⌘\\ / Ctrl+\\", desc: "折叠 / 展开左侧栏" },
  { keys: "⌘F / Ctrl+F", desc: "在当前会话内搜索消息" },
  { keys: "Enter", desc: "发送消息" },
  { keys: "Shift+Enter", desc: "输入框内换行" },
  { keys: "/", desc: "唤出斜杠命令菜单（/new /export /clear）" },
  { keys: "@", desc: "唤出 @ 提及选择器（群聊）" },
];
</script>

<template>
  <div class="section-card" style="margin-bottom: 16px">
    <div class="section-head"><div class="section-title"><Icon name="clock" /> 历史与定时任务</div></div>
    <div class="help-feature-grid">
      <div class="help-feature">
        <div class="help-feature-title"><Icon name="list" :size="14" /> 全部会话</div>
        <div class="help-feature-body">按今天/昨天/本周/更早分组，支持搜索、按范围筛选（团队/项目/个人）、置顶、重命名、批量删除。</div>
      </div>
      <div class="help-feature">
        <div class="help-feature-title"><Icon name="clock" :size="14" /> 定时任务</div>
        <div class="help-feature-body">配置一个助手 + 提示词 + Cron 表达式，到点自动触发对话；内置每日/每周/每月/每小时等常用预设，无需手写 Cron。</div>
      </div>
    </div>
  </div>

  <div class="section-card" style="margin-bottom: 16px">
    <div class="section-head"><div class="section-title"><Icon name="settings" /> 个性化</div></div>
    <div class="help-feature-grid">
      <div class="help-feature">
        <div class="help-feature-title">⌘K 搜索面板</div>
        <div class="help-feature-body">全局模糊搜索会话标题和助手名称，快速跳转。</div>
      </div>
      <div class="help-feature">
        <div class="help-feature-title">Tweaks 面板</div>
        <div class="help-feature-body">顶栏齿轮图标：调整信息密度（舒展/标准/紧凑）和语气偏好（古典/亲切/工程），影响问候语等文案。</div>
      </div>
      <div class="help-feature">
        <div class="help-feature-title">氣質（主题）</div>
        <div class="help-feature-body">顶栏太阳/月亮图标在 5 种配色主题间循环切换，设置会保存到本地。</div>
      </div>
      <div class="help-feature">
        <div class="help-feature-title">在线状态</div>
        <div class="help-feature-body">团队/项目/管理页里的绿色圆点表示同事当前在线，每 30 秒左右更新一次。</div>
      </div>
    </div>
  </div>

  <div class="section-card">
    <div class="section-head"><div class="section-title"><Icon name="list" /> 常用快捷键</div></div>
    <div style="padding: 6px 18px 18px">
      <table style="width: 100%; border-collapse: collapse">
        <tbody>
          <tr v-for="s in SHORTCUTS" :key="s.keys" style="border-top: 1px solid var(--rule-soft)">
            <td style="padding: 8px 10px 8px 0; white-space: nowrap"><span class="help-kbd">{{ s.keys }}</span></td>
            <td style="padding: 8px 0; font-size: 12.5px; color: var(--ink-soft)">{{ s.desc }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
