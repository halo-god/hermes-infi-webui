<script setup lang="ts">
/* 帮助中心 · 团队与项目 — 团队空间、权限矩阵与项目任务管理。 */
import Icon from "@/components/Icon.vue";
</script>

<template>
  <div class="section-card" style="margin-bottom: 16px">
    <div class="section-head"><div class="section-title"><Icon name="cube" /> 团队空间</div></div>
    <div class="help-intro">
      团队是成员、共享助手、知识库和项目的集合。团队主页分为 <b>概览 · 项目 · 成员 · 共享助手 · 知识库 · 活动 · 设置</b> 七个标签页；
      "团队群聊"按钮会打开（或创建）这个团队的圆桌频道，自动包含全部团队成员和共享助手。
    </div>
    <div class="help-mockup">
      <svg viewBox="0 0 560 200" width="100%" style="max-width: 560px; display: block; margin: 0 auto">
        <rect x="1" y="1" width="558" height="198" rx="10" fill="var(--bg-canvas)" stroke="var(--rule)" />
        <rect x="18" y="16" width="120" height="18" rx="9" fill="var(--accent-tint)" stroke="var(--accent-soft)" />
        <text x="78" y="28" font-size="9.5" text-anchor="middle" fill="var(--accent-deep)" font-family="var(--font-sans)">概览</text>
        <rect x="146" y="16" width="80" height="18" rx="9" fill="none" />
        <text x="186" y="28" font-size="9.5" text-anchor="middle" fill="var(--ink-mute)" font-family="var(--font-sans)">项目</text>
        <rect x="234" y="16" width="80" height="18" rx="9" fill="none" />
        <text x="274" y="28" font-size="9.5" text-anchor="middle" fill="var(--ink-mute)" font-family="var(--font-sans)">成员</text>
        <rect x="322" y="16" width="100" height="18" rx="9" fill="none" />
        <text x="372" y="28" font-size="9.5" text-anchor="middle" fill="var(--ink-mute)" font-family="var(--font-sans)">共享助手</text>
        <rect x="430" y="16" width="80" height="18" rx="9" fill="none" />
        <text x="470" y="28" font-size="9.5" text-anchor="middle" fill="var(--ink-mute)" font-family="var(--font-sans)">知识库</text>
        <line x1="18" y1="44" x2="542" y2="44" stroke="var(--rule-soft)" />
        <rect x="18" y="56" width="128" height="46" rx="8" fill="var(--bg-panel)" stroke="var(--rule)" />
        <text x="30" y="72" font-size="9" fill="var(--ink-mute)">成员数</text>
        <text x="30" y="92" font-size="16" fill="var(--ink)" font-family="var(--font-serif)">12</text>
        <rect x="158" y="56" width="128" height="46" rx="8" fill="var(--bg-panel)" stroke="var(--rule)" />
        <text x="170" y="72" font-size="9" fill="var(--ink-mute)">共享助手</text>
        <text x="170" y="92" font-size="16" fill="var(--ink)" font-family="var(--font-serif)">5</text>
        <rect x="298" y="56" width="128" height="46" rx="8" fill="var(--bg-panel)" stroke="var(--rule)" />
        <text x="310" y="72" font-size="9" fill="var(--ink-mute)">本周会话</text>
        <text x="310" y="92" font-size="16" fill="var(--ink)" font-family="var(--font-serif)">34</text>
        <rect x="438" y="56" width="104" height="46" rx="8" fill="var(--bg-panel)" stroke="var(--rule)" />
        <text x="450" y="72" font-size="9" fill="var(--ink-mute)">知识条目</text>
        <text x="450" y="92" font-size="16" fill="var(--ink)" font-family="var(--font-serif)">21</text>
        <rect x="18" y="118" width="524" height="66" rx="8" fill="var(--bg-panel)" stroke="var(--rule)" />
        <text x="30" y="134" font-size="9.5" fill="var(--ink)" font-weight="600">权限矩阵（团队设置）</text>
        <rect x="30" y="144" width="90" height="7" fill="none" />
        <circle cx="200" cy="148" r="6" fill="var(--accent)" />
        <circle cx="240" cy="148" r="6" fill="var(--accent)" />
        <circle cx="280" cy="148" r="6" fill="none" stroke="var(--rule)" />
        <circle cx="320" cy="148" r="6" fill="none" stroke="var(--rule)" />
        <circle cx="200" cy="164" r="6" fill="var(--accent)" />
        <circle cx="240" cy="164" r="6" fill="none" stroke="var(--rule)" />
        <circle cx="280" cy="164" r="6" fill="none" stroke="var(--rule)" />
        <circle cx="320" cy="164" r="6" fill="none" stroke="var(--rule)" />
      </svg>
      <div class="help-mockup-caption">界面示意：团队概览的统计卡片，以及"设置"里可点选授予/收回的角色 × 权限矩阵。</div>
    </div>
  </div>

  <div class="section-card" style="margin-bottom: 16px">
    <div class="section-head"><div class="section-title"><Icon name="user" /> 成员与邀请</div></div>
    <div class="help-feature-grid">
      <div class="help-feature">
        <div class="help-feature-title">邀请链接</div>
        <div class="help-feature-body">生成一条带角色和有效期（1天/7天/30天/永不）的加入链接，发给对方点开即可加入。</div>
      </div>
      <div class="help-feature">
        <div class="help-feature-title">邮箱邀请</div>
        <div class="help-feature-body">批量粘贴邮箱（逗号/空格/换行分隔），直接把已有账号加入团队。</div>
      </div>
      <div class="help-feature">
        <div class="help-feature-title">SSO 自动同步</div>
        <div class="help-feature-body">企业微信/LDAP 登录的用户可按部门自动加入对应团队并赋予默认角色（需管理员在后台配置）。</div>
      </div>
      <div class="help-feature">
        <div class="help-feature-title">角色与权限</div>
        <div class="help-feature-body">所有者/管理员/成员/只读四级角色，具体能查看、编辑哪些内容由团队设置里的权限矩阵决定。</div>
      </div>
    </div>
  </div>

  <div class="section-card">
    <div class="section-head"><div class="section-title"><Icon name="clipboard" /> 项目与任务</div></div>
    <div class="help-feature-grid">
      <div class="help-feature">
        <div class="help-feature-title">列表 / 看板双视图</div>
        <div class="help-feature-body">任务可按"待办 → 进行中 → 已完成"点击流转状态，看板视图支持拖拽卡片调整状态。</div>
      </div>
      <div class="help-feature">
        <div class="help-feature-title">交给助手处理</div>
        <div class="help-feature-body">每个任务都能"交给助手处理"（生成聊天提示）或"指定助手执行"（直接让助手完成并置为进行中）。</div>
      </div>
      <div class="help-feature">
        <div class="help-feature-title">项目群聊 / 讨论任务</div>
        <div class="help-feature-body">任务行的"在项目群聊中讨论此任务"会打开项目的群聊并带上任务上下文，讨论结果和任务关联。</div>
      </div>
      <div class="help-feature">
        <div class="help-feature-title">从对话智能创建</div>
        <div class="help-feature-body">在任意对话的"更多操作"里选"智能创建"，AI 会从聊天记录提炼出项目名称和任务清单，确认后一键建项目。</div>
      </div>
    </div>
  </div>
</template>
