<script setup lang="ts">
/* 帮助中心 · 智能对话 — 单人对话的核心能力。 */
import Icon from "@/components/Icon.vue";
</script>

<template>
  <div class="section-card" style="margin-bottom: 16px">
    <div class="section-head"><div class="section-title"><Icon name="chat" /> 对话与流式回复</div></div>
    <div class="help-intro">
      Enter 发送、Shift+Enter 换行；AI 回复通过流式连接边生成边显示，生成过程中输入框会变成"停止"按钮，可随时中断。
      粘贴图片会自动作为附件发送；点击回形针图标可以"上传本地文件"、"引用文件管理中的文件"，团队会话下还能"引用团队知识库"作为上下文。
    </div>
    <div class="help-mockup">
      <svg viewBox="0 0 560 280" width="100%" style="max-width: 560px; display: block; margin: 0 auto">
        <rect x="1" y="1" width="558" height="278" rx="10" fill="var(--bg-canvas)" stroke="var(--rule)" />
        <rect x="20" y="18" width="260" height="36" rx="10" fill="var(--bg-panel)" stroke="var(--rule)" />
        <rect x="34" y="30" width="180" height="7" rx="3" fill="var(--ink-faint)" />
        <rect x="34" y="42" width="120" height="7" rx="3" fill="var(--ink-faint)" />
        <rect x="120" y="66" width="300" height="60" rx="10" fill="var(--bg-panel)" stroke="var(--rule)" />
        <rect x="134" y="78" width="240" height="7" rx="3" fill="var(--ink-faint)" />
        <rect x="134" y="90" width="260" height="7" rx="3" fill="var(--ink-faint)" />
        <rect x="134" y="102" width="180" height="7" rx="3" fill="var(--ink-faint)" />
        <!-- hover toolbar on the reply -->
        <rect x="300" y="130" width="140" height="22" rx="11" fill="var(--bg-panel)" stroke="var(--accent-soft)" />
        <circle cx="316" cy="141" r="6" fill="none" stroke="var(--accent-deep)" />
        <circle cx="336" cy="141" r="6" fill="none" stroke="var(--accent-deep)" />
        <circle cx="356" cy="141" r="6" fill="none" stroke="var(--accent-deep)" />
        <circle cx="376" cy="141" r="6" fill="none" stroke="var(--accent-deep)" />
        <circle cx="396" cy="141" r="6" fill="none" stroke="var(--accent-deep)" />
        <text x="450" y="145" font-size="9" fill="var(--ink-mute)" font-family="var(--font-sans)">重新生成·Fork·沉淀</text>
        <!-- composer -->
        <rect x="20" y="220" width="520" height="42" rx="12" fill="var(--bg-panel)" stroke="var(--accent)" stroke-width="1.4" />
        <rect x="36" y="236" width="220" height="8" rx="3" fill="var(--ink-faint)" />
        <circle cx="500" cy="241" r="11" fill="var(--accent)" />
        <path d="M496 246 L505 232 L497 238" stroke="var(--ink-on-accent)" stroke-width="1.6" fill="none" stroke-linecap="round" stroke-linejoin="round" />
      </svg>
      <div class="help-mockup-caption">界面示意：鼠标悬停在 AI 回复上会出现操作条（赞/踩、复制、重新生成、Fork、沉淀…）。</div>
    </div>
  </div>

  <div class="section-card" style="margin-bottom: 16px">
    <div class="section-head"><div class="section-title"><Icon name="bolt" /> 快捷输入</div></div>
    <div class="help-feature-grid">
      <div class="help-feature">
        <div class="help-feature-title"><Icon name="send" :size="14" /> 斜杠命令</div>
        <div class="help-feature-body">输入框里打 <span class="mono">/</span> 会弹出命令菜单：<span class="mono">/new</span> 新建对话、<span class="mono">/export</span> 导出、<span class="mono">/clear</span> 清空草稿。</div>
      </div>
      <div class="help-feature">
        <div class="help-feature-title"><Icon name="paperclip" :size="14" /> 附件与引用</div>
        <div class="help-feature-body">回形针菜单可上传本地文件、引用已上传的文件、或（团队会话）引用知识库条目作为背景资料。</div>
      </div>
      <div class="help-feature">
        <div class="help-feature-title"><Icon name="refresh" :size="14" /> 重新生成 / 分叉</div>
        <div class="help-feature-body">对回复不满意可"重新生成"；想基于某条消息尝试不同方向，用"⎇ 分叉"另开一条对话分支，不影响原对话。</div>
      </div>
      <div class="help-feature">
        <div class="help-feature-title"><Icon name="cube" :size="14" /> 沉淀与任务</div>
        <div class="help-feature-body">消息操作里的"沉淀为团队知识/项目文档"把好回复存档；"智能提取待办"能从讨论中自动识别行动项。</div>
      </div>
    </div>
  </div>

  <div class="section-card">
    <div class="section-head"><div class="section-title"><Icon name="search" /> 会话头部</div></div>
    <div class="help-feature-grid">
      <div class="help-feature">
        <div class="help-feature-title">标题与上下文</div>
        <div class="help-feature-body">点击标题可重命名；旁边的圆环表示当前上下文窗口的用量（已用 token / 上限）。</div>
      </div>
      <div class="help-feature">
        <div class="help-feature-title">⌘F 会话内搜索</div>
        <div class="help-feature-body">在长对话里快速定位关键词，支持上一个/下一个跳转并高亮。</div>
      </div>
      <div class="help-feature">
        <div class="help-feature-title">工作区面板</div>
        <div class="help-feature-body">"工作区 (N)" 按钮展开右侧文件面板，预览/编辑本次对话里产生或引用的文件。</div>
      </div>
      <div class="help-feature">
        <div class="help-feature-title">更多操作</div>
        <div class="help-feature-body">导出 Markdown / JSON、查看成员、Fork 整个会话、"智能创建"项目等都在这个菜单里。</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.mono { font-family: var(--font-mono); background: var(--rule-soft); padding: 1px 4px; border-radius: 3px; }
</style>
