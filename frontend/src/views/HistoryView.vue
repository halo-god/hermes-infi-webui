<script setup lang="ts">
/* 1:1 port of the prototype history page (hermes-history.js), wired to the real
   conversations API. */
import { computed, onMounted, ref, watch } from "vue";
import { useRouter } from "vue-router";
import Icon from "@/components/Icon.vue";
import { conversationsApi } from "@/api/conversations";
import { useChatStore } from "@/stores/chat";
import { useBrandingStore } from "@/stores/branding";
import type { Conversation } from "@/types";

const router = useRouter();
const chat = useChatStore();
const branding = useBrandingStore();

const conversations = ref<Conversation[]>([]);
const q = ref("");
const scope = ref<"all" | "pinned" | "team" | "personal">("all");
const sortBy = ref<"recent" | "name" | "agent">("recent");
const selected = ref<Set<string>>(new Set());
const selectMode = ref(false);
const editingId = ref<string | null>(null);
const editText = ref("");

onMounted(async () => {
  if (!chat.profiles.length) await chat.loadProfiles();
  await reload();
});
async function reload() {
  conversations.value = await conversationsApi.list({ q: q.value.trim() || undefined });
}
let searchTimer: ReturnType<typeof setTimeout> | null = null;
watch(q, () => {
  if (searchTimer) clearTimeout(searchTimer);
  searchTimer = setTimeout(reload, 300);
});

function agentLookup(id: string) {
  const p = chat.profiles.find((pp) => pp.default_agent_id === id);
  return { label: p?.name || branding.shortName, color: p?.color || branding.accent, icon: p?.icon || "brand" };
}
function bucketOf(iso: string): string {
  const d = new Date(iso).getTime();
  const now = Date.now();
  const day = 86400000;
  if (now - d < day) return "今天";
  if (now - d < 2 * day) return "昨天";
  if (now - d < 7 * day) return "本周";
  return "更早";
}
function updatedLabel(iso: string): string {
  const d = new Date(iso);
  const mins = Math.floor((Date.now() - d.getTime()) / 60000);
  if (mins < 1) return "刚刚";
  if (mins < 60) return `${mins} 分钟前`;
  if (mins < 1440) return `${Math.floor(mins / 60)} 小时前`;
  return d.toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit" });
}

const base = computed(() => {
  let list = conversations.value.slice();
  if (scope.value === "pinned") list = list.filter((c) => c.pinned);
  else if (scope.value === "team") list = list.filter((c) => c.type === "group" || c.team_id);
  else if (scope.value === "personal") list = list.filter((c) => c.type !== "group");
  if (sortBy.value === "name") list.sort((a, b) => a.title.localeCompare(b.title, "zh"));
  else if (sortBy.value === "agent") list.sort((a, b) => agentLookup(a.primary_agent_id).label.localeCompare(agentLookup(b.primary_agent_id).label, "zh"));
  else list.sort((a, b) => +new Date(b.updated_at) - +new Date(a.updated_at));
  return list;
});
const groups = computed(() => {
  const order = ["今天", "昨天", "本周", "更早"];
  const map: Record<string, Conversation[]> = {};
  base.value.forEach((c) => {
    const b = bucketOf(c.updated_at);
    (map[b] = map[b] || []).push(c);
  });
  return order.filter((o) => map[o]?.length).map((o) => ({ label: o, items: map[o] }));
});

const allVisibleIds = computed(() => base.value.map((c) => c.id));
const allSelected = computed(() => allVisibleIds.value.length > 0 && allVisibleIds.value.every((id) => selected.value.has(id)));
function toggleSel(id: string) {
  const s = new Set(selected.value);
  s.has(id) ? s.delete(id) : s.add(id);
  selected.value = s;
}
function toggleAll() {
  selected.value = allSelected.value ? new Set() : new Set(allVisibleIds.value);
}
function clearSel() {
  selected.value = new Set();
  selectMode.value = false;
}
function startRename(c: Conversation) {
  editingId.value = c.id;
  editText.value = c.title;
}
async function commitRename(c: Conversation) {
  const t = editText.value.trim();
  editingId.value = null;
  if (t && t !== c.title) {
    await conversationsApi.update(c.id, { title: t });
    c.title = t;
  }
}
async function togglePin(c: Conversation) {
  const u = await conversationsApi.update(c.id, { pinned: !c.pinned });
  c.pinned = u.pinned;
}
async function del(id: string) {
  if (!confirm("删除该会话？")) return;
  await conversationsApi.remove(id);
  await reload();
}
async function bulkDelete() {
  if (!selected.value.size) return;
  if (!confirm(`删除选中的 ${selected.value.size} 个会话？`)) return;
  await conversationsApi.bulkDelete([...selected.value]);
  clearSel();
  await reload();
}
async function openConvo(id: string) {
  await chat.openConversation(id);
  router.push("/");
}
function newChat() {
  chat.landing();
  router.push("/");
}
const SCOPES: [typeof scope.value, string][] = [["all", "全部"], ["pinned", "已置顶"], ["team", "团队/项目"], ["personal", "个人"]];
const SORT_LABEL = { recent: "最近活动", name: "标题", agent: "助手" };
function cycleSort() {
  sortBy.value = sortBy.value === "recent" ? "name" : sortBy.value === "name" ? "agent" : "recent";
}
</script>

<template>
  <div class="stage">
    <div class="team-hero">
      <div class="team-hero-row" style="align-items: center">
        <div class="team-shield" style="background: linear-gradient(180deg, #2a241a, #15110b); color: var(--accent)"><Icon name="list" :size="28" /></div>
        <div class="team-info">
          <div class="team-crumb">历史 · ALL CONVERSATIONS</div>
          <h1 class="team-name">所有会话</h1>
          <div class="team-tagline">共 {{ conversations.length }} 段对话 · 在此搜索、整理与清理</div>
        </div>
        <div class="team-actions">
          <button class="btn primary" @click="newChat"><Icon name="plus" /> 新会话</button>
        </div>
      </div>
    </div>

    <div class="team-body" style="max-width: 1080px">
      <div class="users-toolbar" style="margin-bottom: 16px">
        <div class="filter-input" style="width: 280px">
          <Icon name="search" /><input v-model="q" placeholder="按标题或助手搜索会话…" />
        </div>
        <div class="hi-seg">
          <button v-for="o in SCOPES" :key="o[0]" :class="{ active: scope === o[0] }" @click="scope = o[0]">{{ o[1] }}</button>
        </div>
        <span class="flex-1"></span>
        <button class="filter-select" @click="cycleSort">排序：{{ SORT_LABEL[sortBy] }} <Icon name="chevron_down" /></button>
        <button class="filter-select" :class="{ on: selectMode }" @click="selectMode ? clearSel() : (selectMode = true)"><Icon name="check" /> {{ selectMode ? "退出选择" : "批量管理" }}</button>
      </div>

      <div v-if="selectMode" class="hi-bulkbar">
        <label class="login-remember" style="margin: 0">
          <span class="login-check" :class="{ on: allSelected }" @click="toggleAll"><Icon v-if="allSelected" name="check" :size="11" /></span>
          全选当前 {{ base.length }} 项
        </label>
        <span class="flex-1"></span>
        <span class="text-mute-sm">已选 {{ selected.size }} 项</span>
        <button class="btn" :disabled="!selected.size" style="color: var(--danger); border-color: var(--rule)" @click="bulkDelete"><Icon name="close" /> 删除所选</button>
      </div>

      <div v-if="!base.length" class="hi-empty">
        <Icon name="search" :size="22" />
        <div style="margin-top: 10px; font-family: var(--font-serif); font-size: 18px; color: var(--ink)">没有匹配的会话</div>
        <div style="font-size: 12.5px; margin-top: 4px">换个关键词，或切换筛选范围。</div>
      </div>

      <div v-for="g in groups" :key="g.label" class="hi-group">
        <div class="hi-group-label">{{ g.label }} <span>{{ g.items.length }}</span></div>
        <div class="hi-list">
          <div v-for="c in g.items" :key="c.id" class="hi-row" :class="{ sel: selected.has(c.id) }">
            <span v-if="selectMode" class="login-check hi-check" :class="{ on: selected.has(c.id) }" @click="toggleSel(c.id)"><Icon v-if="selected.has(c.id)" name="check" :size="11" /></span>
            <div class="hi-seal" :style="{ background: agentLookup(c.primary_agent_id).color || '#b8852a' }"><Icon :name="c.icon || agentLookup(c.primary_agent_id).icon || 'chat'" :size="14" /></div>
            <div class="hi-main" @click="editingId === c.id ? null : openConvo(c.id)">
              <div class="hi-title-row">
                <input v-if="editingId === c.id" class="task-edit-input" v-model="editText" @keydown.enter="commitRename(c)" @keydown.esc="editingId = null" @blur="commitRename(c)" @click.stop />
                <template v-else>
                  <span class="hi-title">{{ c.title }}</span>
                  <Icon v-if="c.pinned" name="pin" :size="11" style="color: var(--accent-deep); flex-shrink: 0" />
                </template>
              </div>
              <div class="hi-meta">
                <span class="hi-agent"><span class="agent-dot" :style="{ background: agentLookup(c.primary_agent_id).color || '#b8852a' }"></span>{{ agentLookup(c.primary_agent_id).label }}</span>
                <span v-if="c.active_agent_ids.length > 1" class="hi-tag team"><Icon name="user" :size="10" /> 圆桌 {{ c.active_agent_ids.length }}</span>
              </div>
            </div>
            <div class="hi-updated">{{ updatedLabel(c.updated_at) }}</div>
            <div class="hi-actions">
              <button class="icon-btn" :title="c.pinned ? '取消置顶' : '置顶'" @click.stop="togglePin(c)"><Icon name="pin" :style="c.pinned ? { color: 'var(--accent-deep)' } : {}" /></button>
              <button class="icon-btn" title="重命名" @click.stop="startRename(c)"><Icon name="note" /></button>
              <button class="icon-btn text-danger" title="删除" @click.stop="del(c.id)"><Icon name="close" /></button>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
