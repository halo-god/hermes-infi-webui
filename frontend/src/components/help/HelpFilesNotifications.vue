<script setup lang="ts">
/* 帮助中心 · 文件与通知 — 工作区文件预览与消息提醒。 */
import Icon from "@/components/Icon.vue";
</script>

<template>
  <div class="section-card" style="margin-bottom: 16px">
    <div class="section-head"><div class="section-title"><Icon name="folder" /> 工作区文件</div></div>
    <div class="help-intro">
      对话里产生或引用的文件都会出现在右侧"工作区"面板；同样的面板也用于团队知识库文件预览，
      支持多标签同时打开，并按扩展名智能选择预览方式。
    </div>
    <div class="help-mockup">
      <svg viewBox="0 0 560 190" width="100%" style="max-width: 560px; display: block; margin: 0 auto">
        <rect x="1" y="1" width="558" height="188" rx="10" fill="var(--bg-canvas)" stroke="var(--rule)" />
        <rect x="18" y="16" width="90" height="24" rx="7" fill="var(--accent-tint)" stroke="var(--accent-soft)" />
        <text x="63" y="31" font-size="9" text-anchor="middle" fill="var(--accent-deep)" font-family="var(--font-sans)">报告.md</text>
        <rect x="114" y="16" width="90" height="24" rx="7" fill="var(--bg-panel)" stroke="var(--rule)" />
        <text x="159" y="31" font-size="9" text-anchor="middle" fill="var(--ink-mute)" font-family="var(--font-sans)">数据.csv</text>
        <rect x="210" y="16" width="30" height="24" rx="7" fill="var(--bg-panel)" stroke="var(--rule)" />
        <text x="225" y="31" font-size="10" text-anchor="middle" fill="var(--ink-mute)">+2</text>
        <line x1="18" y1="46" x2="542" y2="46" stroke="var(--rule-soft)" />
        <rect x="18" y="58" width="524" height="14" rx="4" fill="var(--ink)" opacity="0.85" />
        <rect x="30" y="62" width="80" height="6" rx="3" fill="var(--bg-canvas)" opacity="0.7" />
        <rect x="18" y="80" width="524" height="7" rx="3" fill="var(--ink-faint)" />
        <rect x="18" y="94" width="480" height="7" rx="3" fill="var(--ink-faint)" />
        <rect x="18" y="108" width="500" height="7" rx="3" fill="var(--ink-faint)" />
        <rect x="18" y="130" width="120" height="18" rx="9" fill="var(--bg-panel)" stroke="var(--rule)" />
        <text x="78" y="142" font-size="8.5" text-anchor="middle" fill="var(--ink-mute)" font-family="var(--font-sans)">编辑 · 下载</text>
        <rect x="146" y="130" width="120" height="18" rx="9" fill="var(--bg-panel)" stroke="var(--rule)" />
        <text x="206" y="142" font-size="8.5" text-anchor="middle" fill="var(--ink-mute)" font-family="var(--font-sans)">版本历史</text>
      </svg>
      <div class="help-mockup-caption">界面示意：多标签文件浏览，代码块高亮显示，底部为编辑/下载/版本历史操作。</div>
    </div>
  </div>

  <div class="section-card" style="margin-bottom: 16px">
    <div class="section-head"><div class="section-title"><Icon name="doc" /> 支持的格式</div></div>
    <div class="help-feature-grid">
      <div class="help-feature">
        <div class="help-feature-title">文档类</div>
        <div class="help-feature-body">Markdown、Word/Excel/PPT（服务端渲染预览）、PDF、纯文本/日志。</div>
      </div>
      <div class="help-feature">
        <div class="help-feature-title">结构化数据</div>
        <div class="help-feature-body">JSON 自动格式化，CSV 渲染为表格，HTML 在沙盒 iframe 中安全展示。</div>
      </div>
      <div class="help-feature">
        <div class="help-feature-title">代码与 Diff</div>
        <div class="help-feature-body">常见语言基础语法高亮；改动文件以 diff/patch 形式带颜色展示增删行。</div>
      </div>
      <div class="help-feature">
        <div class="help-feature-title">图片</div>
        <div class="help-feature-body">png/jpg/gif/svg/webp/bmp/ico 直接预览；不支持预览的类型提供"下载文件"。</div>
      </div>
    </div>
    <div class="help-tip"><Icon name="pin" :size="14" style="flex-shrink: 0; margin-top: 1px; color: var(--accent-deep)" /> 独立的"文件"页面（侧栏底部）是不依赖具体对话的全局文件管理器，支持文件夹、拖拽上传和批量移动。</div>
  </div>

  <div class="section-card">
    <div class="section-head"><div class="section-title"><Icon name="bolt" /> 通知提醒</div></div>
    <div class="help-intro">顶栏铃铛图标聚合了跨会话的提醒；未读数量会显示徽标（超过 9 条显示"9+"）。</div>
    <div class="help-feature-grid">
      <div class="help-feature">
        <div class="help-feature-title">被 @ 提及</div>
        <div class="help-feature-body">你不在当前会话时被提及，会弹出"新的提及"提示并计入未读。</div>
      </div>
      <div class="help-feature">
        <div class="help-feature-title">后台任务完成</div>
        <div class="help-feature-body">离开页面后交给助手处理的后台任务完成或失败，都会有对应提示。</div>
      </div>
      <div class="help-feature">
        <div class="help-feature-title">需要你确认</div>
        <div class="help-feature-body">助手执行中需要澄清时会弹出确认提示，标签页不可见时还会触发系统级桌面通知。</div>
      </div>
      <div class="help-feature">
        <div class="help-feature-title">通知偏好</div>
        <div class="help-feature-body">"设置 → 通知"里可分别控制提及/邀请/项目动态/任务完成/告警/公告，以及邮件提醒（离线时 @ 或每日摘要）。</div>
      </div>
    </div>
  </div>
</template>
