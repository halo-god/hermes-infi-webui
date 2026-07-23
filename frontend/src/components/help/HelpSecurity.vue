<script setup lang="ts">
/* 帮助中心 · 安全与权限 - RBAC、团队权限、数据隔离、SSO。 */
import Icon from "@/components/Icon.vue";

const ROLES = [
  { name: "super_admin", label: "超级管理员", desc: "拥有全部权限，可管理所有用户和系统配置" },
  { name: "admin", label: "管理员", desc: "可管理用户、助手、身份连接器，但不可修改系统核心配置" },
  { name: "member", label: "普通成员", desc: "可使用对话、团队、知识库等常规功能" },
  { name: "viewer", label: "只读成员", desc: "仅可查看，不可创建或修改任何内容" },
];
</script>

<template>
  <div class="section-card" style="margin-bottom: 16px">
    <div class="section-head"><div class="section-title"><Icon name="lock" /> 角色与权限（RBAC）</div></div>
    <div class="help-intro">
      Hermes 采用基于角色的权限控制（RBAC）。管理员可在后台为每个用户分配角色，角色决定了用户能访问哪些功能和执行哪些操作。
    </div>
    <div class="help-feature-grid">
      <div v-for="r in ROLES" :key="r.name" class="help-feature">
        <div class="help-feature-title"><Icon name="user" :size="14" /> {{ r.label }} <span class="help-kbd">{{ r.name }}</span></div>
        <div class="help-feature-body">{{ r.desc }}</div>
      </div>
    </div>
    <div class="help-tip"><Icon name="help" :size="14" style="flex-shrink:0;margin-top:1px;color:var(--accent-deep)" /> 管理员可在后台「权限管理」页面查看完整权限矩阵，并针对单个用户做细粒度的权限覆盖。</div>
  </div>

  <div class="section-card" style="margin-bottom: 16px">
    <div class="section-head"><div class="section-title"><Icon name="users" /> 团队级权限</div></div>
    <div class="help-feature-grid">
      <div class="help-feature">
        <div class="help-feature-title"><Icon name="user" :size="14" /> 团队管理员</div>
        <div class="help-feature-body">可邀请/移除成员、管理团队知识库、创建项目。对团队内的所有资源有管理权限。</div>
      </div>
      <div class="help-feature">
        <div class="help-feature-title"><Icon name="user" :size="14" /> 团队成员</div>
        <div class="help-feature-body">可参与团队群聊、查看团队知识库、使用团队绑定的助手。不可管理成员或修改团队配置。</div>
      </div>
      <div class="help-feature">
        <div class="help-feature-title"><Icon name="user" :size="14" /> 只读成员</div>
        <div class="help-feature-body">仅可查看团队内的对话和知识，不可发消息或修改内容。适合跨团队的信息共享场景。</div>
      </div>
    </div>
  </div>

  <div class="section-card" style="margin-bottom: 16px">
    <div class="section-head"><div class="section-title"><Icon name="lock" /> 数据隔离</div></div>
    <div class="help-feature-grid">
      <div class="help-feature">
        <div class="help-feature-title"><Icon name="lock" :size="14" /> 会话隔离</div>
        <div class="help-feature-body">个人会话仅创建者可见；群聊仅成员可见。系统通过成员关系校验访问权限，防止越权访问。</div>
      </div>
      <div class="help-feature">
        <div class="help-feature-title"><Icon name="lock" :size="14" /> 文件归属校验</div>
        <div class="help-feature-body">附件文件绑定到所属会话，跨会话引用时会校验归属关系，防止跨用户/跨会话的文件泄露。</div>
      </div>
      <div class="help-feature">
        <div class="help-feature-title"><Icon name="lock" :size="14" /> Token 安全</div>
        <div class="help-feature-body">JWT Token 支持撤销机制，Redis 不可用时自动切换为 fail-closed（拒绝已撤销 Token），避免安全降级。</div>
      </div>
    </div>
  </div>

  <div class="section-card">
    <div class="section-head"><div class="section-title"><Icon name="lock" /> 单点登录（SSO）</div></div>
    <div class="help-intro">管理员可配置 LDAP/AD 或企业微信 SSO，实现统一身份认证。</div>
    <div class="help-feature-grid">
      <div class="help-feature">
        <div class="help-feature-title"><Icon name="lock" :size="14" /> LDAP/AD</div>
        <div class="help-feature-body">支持直连认证和搜索认证两种模式。可配置属性映射（用户名/邮箱/部门），并将部门自动映射为团队。</div>
      </div>
      <div class="help-feature">
        <div class="help-feature-title"><Icon name="users" :size="14" /> 企业微信</div>
        <div class="help-feature-body">支持多组织企业微信 SSO。每个组织独立配置 corp_id/secret，用户按 corp_id 命名空间隔离，部门可映射为团队。</div>
      </div>
    </div>
    <div class="help-tip"><Icon name="help" :size="14" style="flex-shrink:0;margin-top:1px;color:var(--accent-deep)" /> SSO 登录后首次访问会自动创建用户账号，并按部门映射关系加入对应团队。企业微信 access_token 有 Redis 缓存，避免频繁调用 API 触发限流。</div>
  </div>
</template>
