<script setup lang="ts">
/* 帮助中心 · 助手与记忆 — Profile 体系与代理记忆/技能。 */
import Icon from "@/components/Icon.vue";
</script>

<template>
  <div class="section-card" style="margin-bottom: 16px">
    <div class="section-head"><div class="section-title"><Icon name="mask" /> 助手（Profile）</div></div>
    <div class="help-intro">
      每个"助手"是一份配置好的人设：名称、图标、默认模型、绑定的底层 Agent、系统提示词、技能标签，
      以及作用范围——<b>个人 / 团队 / 全局</b>。对话一旦绑定某个助手就会锁定（换助手需新建对话），
      避免中途"人格漂移"。
    </div>
    <div class="help-feature-grid">
      <div class="help-feature">
        <div class="help-feature-title"><Icon name="user" :size="14" /> 个人助手</div>
        <div class="help-feature-body">只有你自己能看到和使用，适合个性化配置的私人助理。</div>
      </div>
      <div class="help-feature">
        <div class="help-feature-title"><Icon name="channel" :size="14" /> 团队助手</div>
        <div class="help-feature-body">团队"共享助手"页里勾选共享，团队所有成员都能在群聊或对话里使用，可共享记忆。</div>
      </div>
      <div class="help-feature">
        <div class="help-feature-title"><Icon name="globe" :size="14" /> 全局助手</div>
        <div class="help-feature-body">对所有用户可见，通常由管理员在"助手管理"里创建维护。</div>
      </div>
      <div class="help-feature">
        <div class="help-feature-title"><Icon name="cube" :size="14" /> 混合模型 MoA</div>
        <div class="help-feature-body">一个助手可以配置为"混合模型"，自动分发给一组参考助手并综合它们的回答。</div>
      </div>
    </div>
  </div>

  <div class="section-card" style="margin-bottom: 16px">
    <div class="section-head"><div class="section-title"><Icon name="moon" /> 代理记忆（做梦整理）</div></div>
    <div class="help-intro">
      在"设置 → 代理记忆"里，AI 会在你点击"🌙 做梦整理记忆"后，把最近的对话提炼进三块长期记忆，
      并生成可回溯的历史对话摘要。
    </div>
    <div class="help-mockup">
      <svg viewBox="0 0 560 190" width="100%" style="max-width: 560px; display: block; margin: 0 auto">
        <rect x="1" y="1" width="558" height="188" rx="10" fill="var(--bg-canvas)" stroke="var(--rule)" />
        <rect x="18" y="16" width="524" height="30" rx="15" fill="var(--accent-tint)" stroke="var(--accent-soft)" />
        <text x="34" y="35" font-size="10.5" fill="var(--accent-deep)" font-family="var(--font-sans)">🌙 做梦整理记忆 — 上次整理：3 小时前</text>
        <rect x="18" y="58" width="164" height="112" rx="8" fill="var(--bg-panel)" stroke="var(--rule)" />
        <text x="30" y="76" font-size="9.5" fill="var(--ink)" font-weight="600">我的笔记</text>
        <rect x="30" y="86" width="140" height="6" rx="3" fill="var(--ink-faint)" />
        <rect x="30" y="98" width="120" height="6" rx="3" fill="var(--ink-faint)" />
        <rect x="30" y="150" width="80" height="6" rx="3" fill="var(--ink-mute)" />
        <rect x="198" y="58" width="164" height="112" rx="8" fill="var(--bg-panel)" stroke="var(--rule)" />
        <text x="210" y="76" font-size="9.5" fill="var(--ink)" font-weight="600">用户画像</text>
        <rect x="210" y="86" width="140" height="6" rx="3" fill="var(--ink-faint)" />
        <rect x="210" y="98" width="110" height="6" rx="3" fill="var(--ink-faint)" />
        <rect x="378" y="58" width="164" height="112" rx="8" fill="var(--bg-panel)" stroke="var(--rule)" />
        <text x="390" y="76" font-size="9.5" fill="var(--ink)" font-weight="600">个性设定</text>
        <rect x="390" y="86" width="140" height="6" rx="3" fill="var(--ink-faint)" />
        <rect x="390" y="98" width="100" height="6" rx="3" fill="var(--ink-faint)" />
      </svg>
      <div class="help-mockup-caption">界面示意：我的笔记 / 用户画像 / 个性设定三个可编辑区块，共享 2200 字总预算。</div>
    </div>
  </div>

  <div class="section-card">
    <div class="section-head"><div class="section-title"><Icon name="sparkle" /> 三块记忆 + 历史回忆 + 个人技能</div></div>
    <div class="help-feature-grid">
      <div class="help-feature">
        <div class="help-feature-title">我的笔记</div>
        <div class="help-feature-body">你自己写给 AI 的备注和提醒，完全由你掌控内容。</div>
      </div>
      <div class="help-feature">
        <div class="help-feature-title">用户画像</div>
        <div class="help-feature-body">AI 对你的理解，整理时自动更新，你也可以手动修正。</div>
      </div>
      <div class="help-feature">
        <div class="help-feature-title">个性设定</div>
        <div class="help-feature-body">你希望 AI 表现出的沟通风格/人设，例如"简洁直接"或"耐心细致"。</div>
      </div>
      <div class="help-feature">
        <div class="help-feature-title">历史对话回忆（只读）</div>
        <div class="help-feature-body">每次整理会为对话生成摘要"episode"，聊天时按相关性被检索出来作为背景。</div>
      </div>
      <div class="help-feature">
        <div class="help-feature-title">个人技能</div>
        <div class="help-feature-body">自定义技能：设置名称、内容和触发方式（关键词匹配或"常驻"注入到系统提示词）。</div>
      </div>
      <div class="help-feature">
        <div class="help-feature-title">冷却时间</div>
        <div class="help-feature-body">普通用户整理后有冷却期，避免频繁重算；超级管理员不受冷却限制，可随时重新整理验证。</div>
      </div>
    </div>
  </div>
</template>
