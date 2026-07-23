<script setup lang="ts">
/* 帮助中心 - 图文说明各功能模块，布局对齐 AdminView/FeedbackView 的
   stage + admin-hero + admin-tabs + admin-body 模式。
   支持跨 tab 关键词搜索。 */
import { computed, ref } from "vue";
import Icon from "@/components/Icon.vue";
import { useAuthStore } from "@/stores/auth";
import { useBrandingStore } from "@/stores/branding";
import HelpGettingStarted from "@/components/help/HelpGettingStarted.vue";
import HelpChat from "@/components/help/HelpChat.vue";
import HelpCollaboration from "@/components/help/HelpCollaboration.vue";
import HelpTeamsProjects from "@/components/help/HelpTeamsProjects.vue";
import HelpAssistantsMemory from "@/components/help/HelpAssistantsMemory.vue";
import HelpFilesNotifications from "@/components/help/HelpFilesNotifications.vue";
import HelpProductivity from "@/components/help/HelpProductivity.vue";
import HelpAdmin from "@/components/help/HelpAdmin.vue";
import HelpFaq from "@/components/help/HelpFaq.vue";
import HelpKnowledge from "@/components/help/HelpKnowledge.vue";
import HelpSchedule from "@/components/help/HelpSchedule.vue";
import HelpSecurity from "@/components/help/HelpSecurity.vue";

const auth = useAuthStore();
const branding = useBrandingStore();

const BASE_TABS = [
  { id: "start", label: "快速开始", component: HelpGettingStarted },
  { id: "chat", label: "智能对话", component: HelpChat },
  { id: "collab", label: "协作与圆桌", component: HelpCollaboration },
  { id: "teams", label: "团队与项目", component: HelpTeamsProjects },
  { id: "memory", label: "数字员工与记忆", component: HelpAssistantsMemory },
  { id: "knowledge", label: "知识库", component: HelpKnowledge },
  { id: "files", label: "文件与通知", component: HelpFilesNotifications },
  { id: "tools", label: "效率工具", component: HelpProductivity },
  { id: "schedule", label: "定时任务", component: HelpSchedule },
  { id: "security", label: "安全与权限", component: HelpSecurity },
];
const ADMIN_TAB = { id: "admin", label: "管理后台", component: HelpAdmin };
const FAQ_TAB = { id: "faq", label: "常见问题", component: HelpFaq };

const tabs = computed(() =>
  auth.isAdmin ? [...BASE_TABS, ADMIN_TAB, FAQ_TAB] : [...BASE_TABS, FAQ_TAB],
);

const tab = ref("start");
const activeSection = computed(() => tabs.value.find((t) => t.id === tab.value)?.component ?? HelpGettingStarted);

// ── Search ──
const searchQuery = ref("");
const isSearching = computed(() => searchQuery.value.trim().length > 0);

interface SearchEntry {
  tab: string;
  tabLabel: string;
  title: string;
  body: string;
}

const HELP_INDEX: SearchEntry[] = [
  // 快速开始
  { tab: "start", tabLabel: "快速开始", title: "选一个数字员工开聊", body: "首页点击数字员工卡片或直接在输入框发送消息即可开始对话" },
  { tab: "start", tabLabel: "快速开始", title: "拉团队一起协作", body: "加入或创建团队后，可以发起团队群聊，多个人和多个数字员工在同一个会话里 @ 彼此" },
  { tab: "start", tabLabel: "快速开始", title: "把讨论沉淀下来", body: "满意的回复可以沉淀为团队知识或生成项目任务" },
  // 智能对话
  { tab: "chat", tabLabel: "智能对话", title: "流式输出", body: "AI 回复以流式方式逐字显示，生成过程中可以点击停止按钮中断" },
  { tab: "chat", tabLabel: "智能对话", title: "上下文环", body: "消息区顶部显示当前对话的上下文 token 用量环，颜色随用量变化" },
  { tab: "chat", tabLabel: "智能对话", title: "消息分叉", body: "悬停在用户消息上点击分叉按钮，可以从该消息位置创建一个新会话" },
  { tab: "chat", tabLabel: "智能对话", title: "消息搜索", body: "按 ⌘F 在当前会话内搜索消息内容" },
  { tab: "chat", tabLabel: "智能对话", title: "文件上传", body: "支持上传 Office 文档、PDF、图片、代码文件等，大文件自动转存对象存储" },
  // 协作与圆桌
  { tab: "collab", tabLabel: "协作与圆桌", title: "群聊文件夹分组", body: "群聊页签支持独立文件夹系统，可创建文件夹分组管理群聊" },
  { tab: "collab", tabLabel: "协作与圆桌", title: "@提及路由", body: "群聊中 @ 指定数字员工会只让该数字员工回复，@所有AI 则触发圆桌并行作答" },
  { tab: "collab", tabLabel: "协作与圆桌", title: "表情回应", body: "悬停消息点击表情按钮，可选择 👍👎❤️ 等表情对消息做出反应" },
  { tab: "collab", tabLabel: "协作与圆桌", title: "消息编辑与撤回", body: "群聊中自己的消息可以编辑或撤回，编辑后会显示已编辑标记" },
  // 团队与项目
  { tab: "teams", tabLabel: "团队与项目", title: "项目任务看板", body: "项目详情页有任务看板，支持 todo/doing/done 三栏拖拽" },
  { tab: "teams", tabLabel: "团队与项目", title: "任务从对话生成", body: "悬停 AI 回复点击生成任务按钮，可从消息内容自动创建项目任务" },
  { tab: "teams", tabLabel: "团队与项目", title: "知识沉淀", body: "将满意的 AI 回复沉淀为团队知识或项目文档" },
  // 数字员工与记忆
  { tab: "memory", tabLabel: "数字员工与记忆", title: "Profile 创建与编辑", body: "管理员在后台数字员工管理页面创建和编辑 Profile，配置人设、模型、技能" },
  { tab: "memory", tabLabel: "数字员工与记忆", title: "技能绑定", body: "Profile 可绑定技能，技能是一组预定义的 system prompt 片段" },
  { tab: "memory", tabLabel: "数字员工与记忆", title: "MCP 服务器", body: "管理员可注册 MCP 服务器，Profile 绑定后数字员工获得外部工具调用能力" },
  { tab: "memory", tabLabel: "数字员工与记忆", title: "记忆与做梦整理", body: "系统会定期整理对话生成记忆摘要，也可手动触发整理" },
  // 知识库
  { tab: "knowledge", tabLabel: "知识库", title: "目录树绑定", body: "按目录树绑定知识库，目录下所有文件递归绑定，新增文件自动生效" },
  { tab: "knowledge", tabLabel: "知识库", title: "团队知识 vs 项目文档", body: "团队知识归属整个团队，项目文档归属特定项目" },
  { tab: "knowledge", tabLabel: "知识库", title: "Office 文档预览", body: "支持 docx/xlsx/pptx/pdf/csv/md/图片等格式预览" },
  // 文件与通知
  { tab: "files", tabLabel: "文件与通知", title: "工作区文件", body: "AI 生成的文件保存在工作区，可在文件管理页面查看和下载" },
  { tab: "files", tabLabel: "文件与通知", title: "桌面通知", body: "浏览器允许通知权限后，后台任务完成或收到消息时会有桌面通知" },
  // 效率工具
  { tab: "tools", tabLabel: "效率工具", title: "历史会话筛选", body: "历史页面支持按全部/已置顶/团队项目/个人筛选会话" },
  { tab: "tools", tabLabel: "效率工具", title: "定时任务管理", body: "在日程页面创建和管理定时任务，支持 cron 表达式" },
  { tab: "tools", tabLabel: "效率工具", title: "主题切换", body: "顶栏月亮/太阳图标切换界面配色主题" },
  { tab: "tools", tabLabel: "效率工具", title: "快捷键", body: "⌘K 搜索、⌘\\ 折叠侧栏、⌘F 会话内搜索、Enter 发送、Shift+Enter 换行" },
  // 定时任务
  { tab: "schedule", tabLabel: "定时任务", title: "创建定时任务", body: "填写名称、选择数字员工、输入 prompt 和 cron 表达式" },
  { tab: "schedule", tabLabel: "定时任务", title: "Cron 表达式", body: "5 段格式：分 时 日 月 周，支持 * / - , 语法" },
  { tab: "schedule", tabLabel: "定时任务", title: "执行历史", body: "每次执行结果追加到对应会话，可像普通对话一样查看" },
  // 安全与权限
  { tab: "security", tabLabel: "安全与权限", title: "RBAC 角色", body: "super_admin/admin/member/viewer 四种角色，权限矩阵可细粒度覆盖" },
  { tab: "security", tabLabel: "安全与权限", title: "团队级权限", body: "团队管理员/成员/只读成员三级权限" },
  { tab: "security", tabLabel: "安全与权限", title: "数据隔离", body: "会话隔离、文件归属校验、Token 撤销 fail-closed" },
  { tab: "security", tabLabel: "安全与权限", title: "SSO 单点登录", body: "支持 LDAP/AD 和企业微信 SSO，部门自动映射为团队" },
  // 管理后台
  { tab: "admin", tabLabel: "管理后台", title: "品牌定制", body: "管理员可自定义网站名称、slogan、图标、强调色等" },
  { tab: "admin", tabLabel: "管理后台", title: "身份连接器", body: "配置 LDAP/AD 或企业微信 SSO，支持部门映射" },
  { tab: "admin", tabLabel: "管理后台", title: "审计日志", body: "所有后台操作和登录事件都会被记录，支持按时间/操作者/结果筛选" },
  { tab: "admin", tabLabel: "管理后台", title: "运行时日志", body: "审计日志 tab 内可切换到运行时日志，查看后端磁盘日志文件" },
];

const searchResults = computed(() => {
  const q = searchQuery.value.trim().toLowerCase();
  if (!q) return [];
  return HELP_INDEX.filter((e) =>
    e.title.toLowerCase().includes(q) ||
    e.body.toLowerCase().includes(q) ||
    e.tabLabel.toLowerCase().includes(q),
  );
});

function jumpTo(tabId: string) {
  searchQuery.value = "";
  tab.value = tabId;
}
</script>

<template>
  <div class="stage">
    <div class="admin-hero">
      <div class="admin-hero-row">
        <span class="admin-badge"><Icon name="help" :size="11" /> HELP</span>
        <span style="font-size: 11.5px; color: var(--ink-mute); font-family: var(--font-mono)">{{ branding.tenantName }}</span>
      </div>
      <h1 class="admin-title">帮助<em>中心</em></h1>
      <div class="admin-sub">了解 Hermes 的每一个功能，快速上手你的协作场景。</div>

      <!-- Search box -->
      <div class="help-search-bar">
        <Icon name="search" :size="14" style="color: var(--ink-mute); flex-shrink: 0" />
        <input
          v-model="searchQuery"
          class="help-search-input"
          placeholder="搜索帮助内容…（如：文件夹、定时任务、快捷键）"
        />
        <button v-if="searchQuery" class="help-search-clear" @click="searchQuery = ''"><Icon name="close" :size="12" /></button>
      </div>

      <div v-if="!isSearching" class="admin-tabs">
        <button
          v-for="t in tabs"
          :key="t.id"
          class="team-tab"
          :class="{ active: tab === t.id }"
          @click="tab = t.id"
        >
          {{ t.label }}
        </button>
      </div>
    </div>

    <div class="admin-body">
      <!-- Search results -->
      <template v-if="isSearching">
        <div class="section-card" style="margin-bottom: 16px">
          <div class="section-head">
            <div class="section-title">
              <Icon name="search" /> 搜索结果 · {{ searchResults.length }} 条
            </div>
          </div>
          <div v-if="!searchResults.length" style="padding: 32px; text-align: center; color: var(--ink-mute)">
            未找到匹配的帮助内容，试试其他关键词？
          </div>
          <div v-else class="help-feature-grid">
            <button
              v-for="(r, i) in searchResults"
              :key="i"
              class="help-feature help-search-result"
              @click="jumpTo(r.tab)"
            >
              <div class="help-feature-title">
                <Icon name="chevron_right" :size="12" style="color: var(--accent)" />
                {{ r.title }}
                <span class="help-search-tab">{{ r.tabLabel }}</span>
              </div>
              <div class="help-feature-body">{{ r.body }}</div>
            </button>
          </div>
        </div>
      </template>

      <!-- Normal tab content -->
      <component v-else :is="activeSection" />
    </div>
  </div>
</template>
