<script setup lang="ts">
/* 帮助中心 · 快速开始 — 界面总览与基本操作。 */
import Icon from "@/components/Icon.vue";
</script>

<template>
  <div class="section-card" style="margin-bottom: 16px">
    <div class="section-head"><div class="section-title"><Icon name="compass" /> Hermes 是什么</div></div>
    <div class="help-intro">
      Hermes 是一个多智能体协作工作台：你可以和单个 AI 助手一对一对话，也可以拉上多个助手甚至同事一起开"圆桌"群聊；
      团队沉淀的知识、正在推进的项目、后台运行的定时任务，都汇集在同一个界面里。下面的界面示意标出了最常用的几个入口。
    </div>
    <div class="help-mockup">
      <svg viewBox="0 0 560 300" width="100%" style="max-width: 560px; display: block; margin: 0 auto">
        <rect x="1" y="1" width="558" height="298" rx="10" fill="var(--bg-canvas)" stroke="var(--rule)" />
        <!-- sidebar -->
        <rect x="1" y="1" width="130" height="298" rx="10" fill="var(--bg-side)" stroke="var(--rule)" />
        <rect x="14" y="16" width="60" height="10" rx="3" fill="var(--accent)" />
        <rect x="14" y="40" width="100" height="8" rx="3" fill="var(--ink-faint)" />
        <rect x="14" y="56" width="80" height="8" rx="3" fill="var(--ink-faint)" />
        <rect x="14" y="72" width="90" height="8" rx="3" fill="var(--ink-faint)" />
        <rect x="14" y="96" width="70" height="7" rx="3" fill="var(--ink-mute)" />
        <rect x="14" y="112" width="100" height="8" rx="3" fill="var(--ink-faint)" />
        <rect x="14" y="128" width="85" height="8" rx="3" fill="var(--ink-faint)" />
        <!-- topbar -->
        <rect x="131" y="1" width="428" height="34" fill="var(--bg-panel)" />
        <line x1="131" y1="35" x2="559" y2="35" stroke="var(--rule-soft)" />
        <circle cx="410" cy="18" r="8" fill="var(--bg-canvas)" stroke="var(--rule)" />
        <circle cx="438" cy="18" r="8" fill="var(--accent-tint)" stroke="var(--accent)" stroke-width="1.4" />
        <circle cx="410" cy="18" r="2" fill="var(--ink-mute)" />
        <path d="M434 18a4 4 0 0 1 8 1.4c0 2.8-4 4-4 4" stroke="var(--accent-deep)" stroke-width="1.2" fill="none" stroke-linecap="round" />
        <circle cx="466" cy="18" r="8" fill="var(--bg-canvas)" stroke="var(--rule)" />
        <circle cx="494" cy="18" r="8" fill="var(--bg-canvas)" stroke="var(--rule)" />
        <!-- callout to help icon -->
        <path d="M438 30 L438 44" stroke="var(--accent)" stroke-width="1.4" stroke-dasharray="3 2" />
        <rect x="360" y="46" width="156" height="20" rx="10" fill="var(--accent-tint)" stroke="var(--accent-soft)" />
        <text x="438" y="60" font-size="10.5" text-anchor="middle" fill="var(--accent-deep)" font-family="var(--font-sans)">通知旁边就是"帮助"</text>
        <!-- chat body -->
        <rect x="150" y="52" width="200" height="16" rx="6" fill="var(--bg-panel)" stroke="var(--rule-soft)" />
        <rect x="150" y="90" width="260" height="34" rx="10" fill="var(--bg-panel)" stroke="var(--rule)" />
        <rect x="164" y="100" width="180" height="7" rx="3" fill="var(--ink-faint)" />
        <rect x="164" y="112" width="130" height="7" rx="3" fill="var(--ink-faint)" />
        <rect x="230" y="134" width="260" height="34" rx="10" fill="var(--accent-tint)" stroke="var(--accent-soft)" />
        <rect x="246" y="144" width="150" height="7" rx="3" fill="var(--accent-deep)" opacity="0.6" />
        <rect x="246" y="156" width="200" height="7" rx="3" fill="var(--accent-deep)" opacity="0.6" />
        <rect x="150" y="250" width="340" height="30" rx="10" fill="var(--bg-panel)" stroke="var(--rule)" />
        <rect x="164" y="262" width="80" height="7" rx="3" fill="var(--ink-faint)" />
      </svg>
      <div class="help-mockup-caption">界面示意：左侧为会话/团队导航栏，右上角图标依次是反馈、通知、帮助。</div>
    </div>
  </div>

  <div class="section-card">
    <div class="section-head"><div class="section-title"><Icon name="target" /> 三步上手</div></div>
    <div class="help-feature-grid">
      <div class="help-feature">
        <div class="help-feature-title"><Icon name="chat" :size="14" /> 1 · 选一个助手开聊</div>
        <div class="help-feature-body">首页点击任意"助手卡片"或直接在输入框发送消息即可开始对话；侧栏"+"可以新建对话或群聊。</div>
      </div>
      <div class="help-feature">
        <div class="help-feature-title"><Icon name="channel" :size="14" /> 2 · 拉团队一起协作</div>
        <div class="help-feature-body">加入或创建一个团队后，可以发起"团队群聊"，多个人和多个助手在同一个会话里 @ 彼此。</div>
      </div>
      <div class="help-feature">
        <div class="help-feature-title"><Icon name="cube" :size="14" /> 3 · 把讨论沉淀下来</div>
        <div class="help-feature-body">满意的回复可以"沉淀为团队知识"或"生成项目任务"，团队的经验和进度会持续积累，而不是随对话消失。</div>
      </div>
      <div class="help-feature">
        <div class="help-feature-title"><Icon name="help" :size="14" /> 遇到问题怎么办</div>
        <div class="help-feature-body">先看本页的"常见问题"；仍未解决的，去顶栏"反馈中心"提交 Bug 或建议，管理员会跟进回复。</div>
      </div>
    </div>
    <div class="help-tip"><Icon name="sparkle" :size="14" style="flex-shrink: 0; margin-top: 1px; color: var(--accent-deep)" /> 顶栏右侧的月亮/太阳图标可以切换界面"氣質"（配色主题），设置面板（齿轮图标）里还能调整信息密度和语气偏好。</div>
  </div>
</template>
