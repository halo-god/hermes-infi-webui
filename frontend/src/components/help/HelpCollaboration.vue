<script setup lang="ts">
/* 帮助中心 · 协作与圆桌 — 群聊、@ 提及与多助手协同。 */
import Icon from "@/components/Icon.vue";
</script>

<template>
  <div class="section-card" style="margin-bottom: 16px">
    <div class="section-head"><div class="section-title"><Icon name="channel" /> 群聊与圆桌</div></div>
    <div class="help-intro">
      群聊使用长连接（WebSocket）实时同步消息、输入状态、成员变化；当多个助手同时被 @ 或触发"所有 AI"时，
      每位助手各自生成一张回复卡片，全部完成后自动汇总出一段"综合各方观点"的结论——这就是"圆桌"。
    </div>
    <div class="help-mockup">
      <svg viewBox="0 0 560 260" width="100%" style="max-width: 560px; display: block; margin: 0 auto">
        <rect x="1" y="1" width="558" height="258" rx="10" fill="var(--bg-canvas)" stroke="var(--rule)" />
        <rect x="18" y="16" width="164" height="70" rx="10" fill="var(--bg-panel)" stroke="var(--accent-soft)" />
        <circle cx="34" cy="32" r="8" fill="var(--accent-tint)" stroke="var(--accent)" />
        <rect x="48" y="27" width="60" height="7" rx="3" fill="var(--ink-faint)" />
        <rect x="30" y="46" width="140" height="7" rx="3" fill="var(--ink-faint)" />
        <rect x="30" y="58" width="110" height="7" rx="3" fill="var(--ink-faint)" />
        <rect x="30" y="70" width="90" height="7" rx="3" fill="var(--accent-deep)" opacity="0.5" />

        <rect x="198" y="16" width="164" height="70" rx="10" fill="var(--bg-panel)" stroke="var(--rule)" />
        <circle cx="214" cy="32" r="8" fill="var(--bg-canvas)" stroke="var(--ink-faint)" />
        <rect x="228" y="27" width="60" height="7" rx="3" fill="var(--ink-faint)" />
        <rect x="210" y="46" width="140" height="7" rx="3" fill="var(--ink-faint)" />
        <rect x="210" y="58" width="100" height="7" rx="3" fill="var(--ink-faint)" />
        <text x="210" y="76" font-size="9" fill="var(--ink-mute)" font-family="var(--font-sans)">生成中…</text>

        <rect x="378" y="16" width="164" height="70" rx="10" fill="var(--bg-panel)" stroke="var(--rule)" />
        <circle cx="394" cy="32" r="8" fill="var(--bg-canvas)" stroke="var(--ink-faint)" />
        <rect x="408" y="27" width="60" height="7" rx="3" fill="var(--ink-faint)" />
        <rect x="390" y="46" width="140" height="7" rx="3" fill="var(--ink-faint)" />
        <rect x="390" y="58" width="120" height="7" rx="3" fill="var(--ink-faint)" />
        <text x="390" y="76" font-size="9" fill="var(--danger)" font-family="var(--font-sans)">超时</text>

        <!-- synthesis -->
        <rect x="18" y="100" width="524" height="46" rx="10" fill="var(--accent-tint)" stroke="var(--accent-soft)" />
        <text x="34" y="118" font-size="10.5" fill="var(--accent-deep)" font-family="var(--font-sans)" font-weight="600">✦ 综合各方观点</text>
        <rect x="34" y="126" width="480" height="7" rx="3" fill="var(--accent-deep)" opacity="0.45" />

        <!-- message row with @ mention -->
        <rect x="18" y="164" width="300" height="30" rx="10" fill="var(--bg-panel)" stroke="var(--rule)" />
        <rect x="32" y="174" width="30" height="7" rx="3" fill="var(--accent)" />
        <rect x="66" y="174" width="220" height="7" rx="3" fill="var(--ink-faint)" />
        <text x="330" y="184" font-size="9.5" fill="var(--ink-mute)" font-family="var(--font-sans)">@ 提及某位助手/同事</text>

        <!-- typing indicator -->
        <circle cx="30" cy="220" r="3" fill="var(--ink-mute)" />
        <circle cx="40" cy="220" r="3" fill="var(--ink-mute)" opacity="0.6" />
        <circle cx="50" cy="220" r="3" fill="var(--ink-mute)" opacity="0.3" />
        <text x="60" y="224" font-size="9.5" fill="var(--ink-mute)" font-family="var(--font-sans)">同事正在输入…</text>
      </svg>
      <div class="help-mockup-caption">界面示意：三位助手并行回复（完成 / 生成中 / 超时），随后自动汇总；下方为 @ 提及与输入状态。</div>
    </div>
  </div>

  <div class="section-card">
    <div class="section-head"><div class="section-title"><Icon name="at" /> 群聊控制</div></div>
    <div class="help-feature-grid">
      <div class="help-feature">
        <div class="help-feature-title"><Icon name="channel" :size="14" /> AI 回复模式</div>
        <div class="help-feature-body">成员面板可设置"自动回复"（每条消息都触发 AI）、"@ 触发"（默认，仅被提及时回复）或"关闭"（纯人类讨论）。</div>
      </div>
      <div class="help-feature">
        <div class="help-feature-title"><Icon name="at" :size="14" /> @ 提及选择器</div>
        <div class="help-feature-body">输入 <span class="mono">@</span> 弹出选择器：可选"所有 AI"、"所有真人"，或具体某个助手/同事。</div>
      </div>
      <div class="help-feature">
        <div class="help-feature-title"><Icon name="edit" :size="14" /> 编辑与撤回</div>
        <div class="help-feature-body">自己发的消息支持编辑（标注"已编辑"）和撤回；悬停消息还能快速回复引用或添加表情反应。</div>
      </div>
      <div class="help-feature">
        <div class="help-feature-title"><Icon name="cube" :size="14" /> 混合模型（MoA）</div>
        <div class="help-feature-body">管理员可配置"混合模型"助手：一次提问自动分发给多个参考助手并综合答案，无需手动 @ 所有人。</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.mono { font-family: var(--font-mono); background: var(--rule-soft); padding: 1px 4px; border-radius: 3px; }
</style>
