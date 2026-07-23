<script setup lang="ts">
/* 帮助中心 · 定时任务 - 创建/编辑/cron 表达式/执行历史。 */
import Icon from "@/components/Icon.vue";

const CRON_EXAMPLES = [
  { expr: "0 9 * * *", desc: "每天上午 9:00 执行" },
  { expr: "0 9 * * 1", desc: "每周一上午 9:00 执行" },
  { expr: "*/30 * * * *", desc: "每 30 分钟执行一次" },
  { expr: "0 0 1 * *", desc: "每月 1 号 0:00 执行" },
  { expr: "0 18 * * 1-5", desc: "工作日（周一至周五）18:00 执行" },
];
</script>

<template>
  <div class="section-card" style="margin-bottom: 16px">
    <div class="section-head"><div class="section-title"><Icon name="clock" /> 定时任务是什么</div></div>
    <div class="help-intro">
      定时任务让你设定一个 cron 表达式，系统会按计划自动向指定助手发送预设的 prompt。适合日报生成、数据巡检、定期总结等场景。
      每个用户最多创建 20 个定时任务。
    </div>
    <div class="help-feature-grid">
      <div class="help-feature">
        <div class="help-feature-title"><Icon name="plus" :size="14" /> 创建任务</div>
        <div class="help-feature-body">在侧栏「日程」页面点击新建，填写任务名称、选择助手、输入 prompt 和 cron 表达式即可。</div>
      </div>
      <div class="help-feature">
        <div class="help-feature-title"><Icon name="edit" :size="14" /> 编辑与启停</div>
        <div class="help-feature-body">已创建的任务可随时编辑 prompt、调整 cron、或临时禁用/启用。禁用后不再执行，但保留配置。</div>
      </div>
      <div class="help-feature">
        <div class="help-feature-title"><Icon name="refresh" :size="14" /> 执行历史</div>
        <div class="help-feature-body">每次执行的结果会作为一条消息追加到对应的会话中，你可以像查看普通对话一样查看历史执行结果。</div>
      </div>
    </div>
  </div>

  <div class="section-card" style="margin-bottom: 16px">
    <div class="section-head"><div class="section-title"><Icon name="clock" /> Cron 表达式速查</div></div>
    <div class="help-intro">Cron 格式为 5 段：<code>分 时 日 月 周</code>。以下为常用示例：</div>
    <div class="help-feature-grid">
      <div v-for="e in CRON_EXAMPLES" :key="e.expr" class="help-feature">
        <div class="help-feature-title"><span class="help-kbd">{{ e.expr }}</span></div>
        <div class="help-feature-body">{{ e.desc }}</div>
      </div>
    </div>
    <div class="help-tip"><Icon name="help" :size="14" style="flex-shrink:0;margin-top:1px;color:var(--accent-deep)" /> 时区使用服务器本地时间。周字段 0 和 7 都表示周日。支持 <code>*</code>（任意）、<code>*/N</code>（每 N）、<code>-</code>（范围）、<code>,</code>（列表）。</div>
  </div>

  <div class="section-card">
    <div class="section-head"><div class="section-title"><Icon name="sparkle" /> 使用技巧</div></div>
    <div class="help-feature-grid">
      <div class="help-feature">
        <div class="help-feature-title"><Icon name="doc" :size="14" /> 日报生成</div>
        <div class="help-feature-body">设置每天 18:00 执行，prompt 写"总结今天的对话要点，生成日报"。助手会自动拉取当天上下文生成摘要。</div>
      </div>
      <div class="help-feature">
        <div class="help-feature-title"><Icon name="refresh" :size="14" /> 数据巡检</div>
        <div class="help-feature-body">配合助手的文件读取能力，定期检查某个目录下的日志文件并汇报异常，实现自动化监控。</div>
      </div>
    </div>
  </div>
</template>
