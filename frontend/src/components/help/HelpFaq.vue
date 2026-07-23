<script setup lang="ts">
/* 帮助中心 · 常见问题 — 手风琴折叠展示。 */
import Icon from "@/components/Icon.vue";

const FAQ = [
  {
    q: "单人对话和圆桌群聊有什么区别？",
    a: "单人对话通过 SSE 流式连接绑定一个助手；群聊通过 WebSocket 连接多人多助手，可以 @ 任意人，被多个助手同时回复时会自动生成一段综合各方观点的汇总。",
  },
  {
    q: "怎么邀请同事加入我的团队？",
    a: "在团队主页点击「邀请成员」，可以选择生成邀请链接（带有效期）、按邮箱批量邀请已有账号，或者依赖管理员配置好的企业微信/LDAP 自动同步。",
  },
  {
    q: "为什么我看不到「管理」入口？",
    a: "管理后台仅对 admin / super_admin 角色开放。如果你需要管理权限，请联系已有管理员在「用户管理」里调整你的角色。",
  },
  {
    q: "「做梦整理记忆」多久能用一次？",
    a: "普通用户整理一次后有冷却时间，冷却期间按钮会显示剩余分钟数；超级管理员不受冷却限制，可随时重新触发用于验证效果。",
  },
  {
    q: "AI 正在生成回复时能中途停止吗？",
    a: "可以。生成过程中输入框位置会变成停止按钮，点击即可立即中断当前这次生成。",
  },
  {
    q: "怎么把一段讨论变成团队知识或项目任务？",
    a: "悬停在满意的 AI 回复上，操作条里有「沉淀为团队知识」「沉淀为项目文档」「从此消息生成任务」；整段对话也可以通过「更多操作 -> 智能创建」一次性提炼成一个新项目和任务清单。",
  },
  {
    q: "定时任务显示执行失败怎么办？",
    a: "先在「定时任务」列表查看该次运行状态；可以编辑任务的提示词或 Cron 表达式后重新保存，也可以先临时禁用该任务排查原因。每个任务最多重试 3 次，超过后进入死信队列。",
  },
  {
    q: "工作区文件支持预览哪些格式？",
    a: "Markdown、Word/Excel/PPT、PDF、JSON、CSV、HTML、图片、diff/patch 以及常见编程语言代码都能直接预览；不支持预览的类型可以直接下载查看。",
  },
  {
    q: "群聊和会话的文件夹是独立的吗？",
    a: "是的。侧栏的「会话」和「群聊」页签各自有独立的文件夹系统。在群聊页签内点击「新建文件夹」创建的是群聊专用文件夹，不会出现在会话页签中。右键菜单「移入文件夹」也会显示当前页签对应的文件夹列表。",
  },
  {
    q: "知识库目录绑定后新增文件会自动生效吗？",
    a: "会。按目录树绑定知识库时，系统会递归收集该目录下所有文件（含子目录）。之后新增到该目录的文件会自动被绑定，无需重新配置。三种绑定方式（单条/目录/团队）可组合使用，系统自动去重。",
  },
  {
    q: "定时任务创建了但没有执行？",
    a: "请检查：1) 任务是否被禁用（列表中有开关）；2) Cron 表达式是否正确（参考帮助中心的 Cron 速查）；3) 服务器时区是否与你的预期一致；4) Agent Runner 进程是否正常运行。如果仍无法解决，联系管理员查看后台日志。",
  },
  {
    q: "Office 文档预览显示空白或内容不全？",
    a: "Office 预览依赖文档解析库，某些复杂格式（如嵌入的 Visio 对象、宏脚本）可能无法完整提取。大文件（&gt;30KB 提取内容）不会全文显示，而是生成引用路径让 AI 按需读取。如果预览完全空白，尝试重新上传或转换为 PDF 格式。",
  },
];
</script>

<template>
  <div class="section-card">
    <div class="section-head"><div class="section-title"><Icon name="help" /> 常见问题</div></div>
    <div style="padding: 4px 18px 18px">
      <details v-for="item in FAQ" :key="item.q" class="faq-item">
        <summary>
          <Icon name="chevron_down" :size="14" class="faq-chevron" />
          {{ item.q }}
        </summary>
        <div class="faq-body">{{ item.a }}</div>
      </details>
    </div>
  </div>
</template>

<style scoped>
.faq-item { border-bottom: 1px solid var(--rule-soft); padding: 10px 0; }
.faq-item:last-child { border-bottom: none; }
.faq-item summary {
  cursor: pointer;
  list-style: none;
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
  font-size: 13.5px;
  color: var(--ink);
}
.faq-item summary::-webkit-details-marker { display: none; }
.faq-item .faq-chevron { transition: transform 150ms ease; flex-shrink: 0; color: var(--ink-mute); }
.faq-item[open] summary .faq-chevron { transform: rotate(180deg); }
.faq-body { padding: 8px 0 0 22px; font-size: 13px; color: var(--ink-soft); line-height: 1.6; }
</style>
