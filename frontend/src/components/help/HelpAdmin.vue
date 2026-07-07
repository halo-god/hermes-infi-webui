<script setup lang="ts">
/* 帮助中心 · 管理后台 — 仅管理员可见，概述后台各功能模块。 */
import Icon from "@/components/Icon.vue";

const MODULES = [
  { icon: "user", title: "用户管理", desc: "搜索/筛选用户（来源、状态），新建本地账号，调整角色，启用/停用账号。" },
  { icon: "lock", title: "权限管理", desc: "super_admin/admin/team_admin/member/viewer 五种平台角色的权限矩阵；与团队内容权限矩阵是两套独立体系。" },
  { icon: "globe", title: "身份与连接", desc: "LDAP/AD、企业微信（可配置多个组织）单点登录与部门 → 团队自动映射；SAML/OIDC/飞书为占位入口。" },
  { icon: "clipboard", title: "审计日志", desc: "全部管理员/鉴权操作的可搜索日志：操作者、动作、目标、结果、IP、时间戳。" },
  { icon: "mask", title: "助手管理", desc: "助手的完整增删改查：作用范围、系统提示词、绑定知识库/MCP 服务器、克隆、JSON 导入导出。" },
  { icon: "channel", title: "MCP 服务器", desc: "注册/管理 MCP 服务器（stdio 或 http 传输），支持连通性检测。" },
  { icon: "settings", title: "系统设置", desc: "品牌与外观（租户名、Logo、主题色）、模型网关（默认模型、配额、限流）。" },
  { icon: "chart2", title: "性能监控", desc: "进程/系统/数据库/Redis 的实时资源指标。" },
  { icon: "sparkle", title: "技能演化（超级管理员）", desc: "对已有技能触发 DSPy+GEPA 自动优化，人工审核前后对比后再批准上线。" },
];
</script>

<template>
  <div class="section-card">
    <div class="section-head"><div class="section-title"><Icon name="lock" /> 管理后台一览</div></div>
    <div class="help-intro">
      仅 <b>admin</b> 或 <b>super_admin</b> 角色能看到顶栏"管理"入口和以下模块（对应 <span class="mono">/admin</span> 页面的各个标签页）。
    </div>
    <div class="help-feature-grid">
      <div v-for="m in MODULES" :key="m.title" class="help-feature">
        <div class="help-feature-title"><Icon :name="m.icon" :size="14" /> {{ m.title }}</div>
        <div class="help-feature-body">{{ m.desc }}</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.mono { font-family: var(--font-mono); background: var(--rule-soft); padding: 1px 4px; border-radius: 3px; }
</style>
