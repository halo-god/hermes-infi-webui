<script setup lang="ts">
/* 反馈页面 — 用户提交反馈 + 查看回复进度；管理员查看全部 + 回复管理。
   布局对齐 AdminView 的 stage + centered body 模式。 */
import { computed, onMounted, ref } from "vue";
import { useRoute } from "vue-router";
import Icon from "@/components/Icon.vue";
import { useAuthStore } from "@/stores/auth";
import { useBrandingStore } from "@/stores/branding";
import { useNotificationStore } from "@/stores/notifications";
import { feedbackApi } from "@/api/feedback";
import type { Feedback } from "@/types";

const route = useRoute();
const auth = useAuthStore();
const branding = useBrandingStore();
const ns = useNotificationStore();

const isAdmin = computed(() => auth.user?.role === "super_admin" || auth.user?.role === "admin");

const feedbackList = ref<Feedback[]>([]);
const loading = ref(true);
const selectedId = ref<number | null>(null);
const selected = ref<Feedback | null>(null);
const statusFilter = ref("");
const categoryFilter = ref("");

// Create form
const showForm = ref(false);
const form = ref({ title: "", content: "", category: "bug" });
const submitting = ref(false);
const attachedImages = ref<{ name: string; dataUrl: string }[]>([]);

function onPaste(e: ClipboardEvent) {
  const items = e.clipboardData?.items;
  if (!items) return;
  for (const item of items) {
    if (item.type.startsWith("image/")) {
      const file = item.getAsFile();
      if (file) addImage(file);
    }
  }
}

function onImageUpload(e: Event) {
  const input = e.target as HTMLInputElement;
  if (!input.files) return;
  for (const file of Array.from(input.files)) {
    if (file.type.startsWith("image/")) addImage(file);
  }
  input.value = "";
}

function addImage(file: File) {
  const reader = new FileReader();
  reader.onload = () => {
    attachedImages.value.push({ name: file.name, dataUrl: reader.result as string });
  };
  reader.readAsDataURL(file);
}

function removeImage(idx: number) {
  attachedImages.value.splice(idx, 1);
}

// Admin reply form
const replyText = ref("");
const replyStatus = ref("");
const replyPriority = ref("");
const saving = ref(false);

const STATUS_LABELS: Record<string, string> = {
  open: "待处理", in_progress: "处理中", resolved: "已解决", closed: "已关闭",
};
const STATUS_COLORS: Record<string, string> = {
  open: "var(--ok)", in_progress: "var(--warn)", resolved: "#5a8a3a", closed: "var(--ink-mute)",
};
const CATEGORY_LABELS: Record<string, string> = { bug: "Bug", suggestion: "建议", other: "其他" };
const CATEGORY_COLORS: Record<string, string> = { bug: "var(--danger)", suggestion: "#3a8a5a", other: "var(--ink-mute)" };
const PRIORITY_LABELS: Record<string, string> = { low: "低", normal: "中", high: "高", urgent: "紧急" };

const filteredList = computed(() => {
  let result = feedbackList.value;
  if (statusFilter.value) result = result.filter((f) => f.status === statusFilter.value);
  if (categoryFilter.value) result = result.filter((f) => f.category === categoryFilter.value);
  return result;
});

const stats = computed(() => ({
  total: feedbackList.value.length,
  open: feedbackList.value.filter((f) => f.status === "open").length,
  inProgress: feedbackList.value.filter((f) => f.status === "in_progress").length,
  resolved: feedbackList.value.filter((f) => f.status === "resolved").length,
}));

async function loadList() {
  loading.value = true;
  try {
    feedbackList.value = await feedbackApi.list({ limit: 200 });
  } catch {
    ns.toast("加载失败", "error");
  } finally {
    loading.value = false;
  }
}

async function loadDetail(id: number) {
  try {
    selected.value = await feedbackApi.get(id);
    replyText.value = selected.value?.reply || "";
    replyStatus.value = selected.value?.status || "open";
    replyPriority.value = selected.value?.priority || "normal";
  } catch {
    ns.toast("加载详情失败", "error");
  }
}

function selectFeedback(id: number) {
  selectedId.value = id;
  loadDetail(id);
}

async function submitFeedback() {
  if (!form.value.title.trim() || !form.value.content.trim()) {
    ns.toast("请填写标题和内容", "error");
    return;
  }
  submitting.value = true;
  try {
    // Append image references to content
    let content = form.value.content;
    if (attachedImages.value.length) {
      const imgList = attachedImages.value.map((img, i) => `[截图${i + 1}: ${img.name}]`).join("\n");
      content += "\n\n--- 附带截图 ---\n" + imgList;
    }
    await feedbackApi.create({ ...form.value, content });
    ns.toast("反馈已提交");
    showForm.value = false;
    form.value = { title: "", content: "", category: "bug" };
    attachedImages.value = [];
    await loadList();
  } catch {
    ns.toast("提交失败", "error");
  } finally {
    submitting.value = false;
  }
}

async function saveReply() {
  if (!selectedId.value) return;
  saving.value = true;
  try {
    selected.value = await feedbackApi.update(selectedId.value, {
      status: replyStatus.value,
      priority: replyPriority.value,
      reply: replyText.value,
    });
    ns.toast("已保存回复");
    await loadList();
  } catch {
    ns.toast("保存失败", "error");
  } finally {
    saving.value = false;
  }
}

onMounted(async () => {
  await loadList();
  const qid = route.query.id;
  if (qid) selectFeedback(Number(qid));
});
</script>

<template>
  <div class="stage">
    <!-- Hero header (mirrors AdminView) -->
    <div class="admin-hero">
      <div class="admin-hero-row">
        <span class="admin-badge"><Icon name="chat" :size="11" /> FEEDBACK</span>
        <span style="font-size: 11.5px; color: var(--ink-mute); font-family: var(--font-mono)">{{ branding.tenantName }}</span>
      </div>
      <h1 class="admin-title">反馈<em>中心</em></h1>
      <div class="admin-sub">提交意见、报告 Bug，跟踪处理进度。</div>
    </div>

    <div class="admin-body">
      <!-- Stat cards -->
      <div class="stat-grid" style="grid-template-columns: repeat(4, 1fr); margin-bottom: 20px">
        <div class="stat">
          <div class="stat-label">全部</div>
          <div class="stat-value">{{ stats.total }}</div>
        </div>
        <div class="stat">
          <div class="stat-label">待处理</div>
          <div class="stat-value" style="color: var(--ok)">{{ stats.open }}</div>
        </div>
        <div class="stat">
          <div class="stat-label">处理中</div>
          <div class="stat-value" style="color: var(--warn)">{{ stats.inProgress }}</div>
        </div>
        <div class="stat">
          <div class="stat-label">已解决</div>
          <div class="stat-value" style="color: #5a8a3a">{{ stats.resolved }}</div>
        </div>
      </div>

      <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px">
        <div style="display: flex; gap: 8px; align-items: center">
          <select v-model="statusFilter" class="fb-select">
            <option value="">全部状态</option>
            <option value="open">待处理</option>
            <option value="in_progress">处理中</option>
            <option value="resolved">已解决</option>
            <option value="closed">已关闭</option>
          </select>
          <select v-model="categoryFilter" class="fb-select">
            <option value="">全部分类</option>
            <option value="bug">Bug</option>
            <option value="suggestion">建议</option>
            <option value="other">其他</option>
          </select>
          <span style="font-size: 12px; color: var(--ink-mute)">{{ filteredList.length }} 条</span>
        </div>
        <button class="btn primary" @click="showForm = !showForm">
          <Icon name="edit" :size="13" /> 提交反馈
        </button>
      </div>

      <!-- Create form -->
      <div v-if="showForm" class="section-card" style="margin-bottom: 16px">
        <div class="section-head"><div class="section-title"><Icon name="edit" /> 提交新反馈</div></div>
        <div style="padding: 18px; display: flex; flex-direction: column; gap: 10px" @paste="onPaste">
          <input v-model="form.title" class="cfg-input" placeholder="反馈标题（简明描述问题或建议）" />
          <textarea v-model="form.content" class="cfg-input" rows="4" placeholder="详细描述你的反馈或问题，包括复现步骤（如果是 Bug）…&#10;&#10;💡 可直接在此粘贴截图" style="resize: vertical; font-family: inherit" />
          <!-- Image upload + preview -->
          <div style="display: flex; gap: 8px; align-items: center; flex-wrap: wrap">
            <label class="btn" style="cursor: pointer; font-size: 12px; display: inline-flex; align-items: center; gap: 4px">
              <Icon name="paperclip" :size="13" /> 上传截图
              <input type="file" accept="image/*" multiple style="display: none" @change="onImageUpload" />
            </label>
            <span v-if="attachedImages.length" style="font-size: 11px; color: var(--ink-mute)">{{ attachedImages.length }} 张图片</span>
          </div>
          <div v-if="attachedImages.length" style="display: flex; gap: 8px; flex-wrap: wrap">
            <div v-for="(img, i) in attachedImages" :key="i" style="position: relative; width: 80px; height: 80px">
              <img :src="img.dataUrl" style="width: 100%; height: 100%; object-fit: cover; border-radius: 6px; border: 1px solid var(--rule)" />
              <button style="position: absolute; top: -4px; right: -4px; width: 18px; height: 18px; border-radius: 50%; background: var(--danger); color: #fff; border: none; cursor: pointer; font-size: 11px; line-height: 1; display: flex; align-items: center; justify-content: center" @click="removeImage(i)">×</button>
            </div>
          </div>
          <div style="display: flex; gap: 8px; align-items: center">
            <select v-model="form.category" class="fb-select">
              <option value="bug">Bug</option>
              <option value="suggestion">建议</option>
              <option value="other">其他</option>
            </select>
            <button class="btn primary" :disabled="submitting" @click="submitFeedback" style="margin-left: auto">{{ submitting ? "提交中…" : "提交" }}</button>
            <button class="btn" @click="showForm = false">取消</button>
          </div>
        </div>
      </div>

      <!-- Feedback list + detail -->
      <div class="col-grid" style="grid-template-columns: 1fr 420px; gap: 16px">
        <!-- Left: list -->
        <div class="section-card">
          <div class="section-head"><div class="section-title"><Icon name="doc" /> 反馈列表</div></div>
          <div style="padding: 4px 0">
            <div v-if="loading" style="padding: 40px; text-align: center; color: var(--ink-mute)">加载中…</div>
            <div v-else-if="!filteredList.length" style="padding: 40px; text-align: center; color: var(--ink-mute)">暂无反馈</div>
            <div
              v-for="f in filteredList"
              :key="f.id"
              class="fb-row"
              :class="{ active: selectedId === f.id }"
              @click="selectFeedback(f.id)"
            >
              <div style="flex: 1; min-width: 0">
                <div style="font-size: 13.5px; font-weight: 600; color: var(--ink)">{{ f.title }}</div>
                <div style="display: flex; gap: 8px; margin-top: 4px; align-items: center; flex-wrap: wrap">
                  <span class="fb-cat-pill" :style="{ color: CATEGORY_COLORS[f.category], borderColor: CATEGORY_COLORS[f.category] + '44' }">{{ CATEGORY_LABELS[f.category] }}</span>
                  <span class="fb-st-pill" :style="{ color: STATUS_COLORS[f.status], borderColor: STATUS_COLORS[f.status] + '44' }">{{ STATUS_LABELS[f.status] }}</span>
                  <span v-if="f.reply" style="font-size: 11px; color: var(--accent-deep)">💬 已回复</span>
                  <span v-if="isAdmin" style="font-size: 11px; color: var(--ink-mute)">👤 {{ f.user_name }}</span>
                </div>
              </div>
              <div style="font-size: 11px; color: var(--ink-faint); flex-shrink: 0; white-space: nowrap">
                {{ new Date(f.created_at).toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' }) }}
              </div>
            </div>
          </div>
        </div>

        <!-- Right: detail -->
        <div class="section-card" v-if="selected">
          <div class="section-head"><div class="section-title"><Icon name="doc" /> 反馈详情 #{{ selected.id }}</div></div>
          <div style="padding: 18px">
            <h3 style="margin: 0 0 8px; font-size: 15px; color: var(--ink)">{{ selected.title }}</h3>
            <div style="display: flex; gap: 6px; margin-bottom: 14px; flex-wrap: wrap">
              <span class="fb-cat-pill" :style="{ color: CATEGORY_COLORS[selected.category], borderColor: CATEGORY_COLORS[selected.category] + '44' }">{{ CATEGORY_LABELS[selected.category] }}</span>
              <span class="fb-st-pill" :style="{ color: STATUS_COLORS[selected.status], borderColor: STATUS_COLORS[selected.status] + '44' }">{{ STATUS_LABELS[selected.status] }}</span>
              <span v-if="isAdmin" class="fb-cat-pill" style="color: var(--ink-mute); border-color: var(--rule)">优先级: {{ PRIORITY_LABELS[selected.priority] }}</span>
            </div>
            <div style="font-size: 13px; color: var(--ink); line-height: 1.6; background: var(--bg-canvas); border-radius: 8px; padding: 12px; margin-bottom: 12px; white-space: pre-wrap">{{ selected.content }}</div>
            <div style="font-size: 11px; color: var(--ink-mute); margin-bottom: 16px">
              提交者: {{ selected.user_name }} · {{ new Date(selected.created_at).toLocaleString('zh-CN') }}
            </div>

            <!-- Admin reply display (for regular users) -->
            <div v-if="selected.reply && !isAdmin" style="background: var(--accent-tint); border-radius: 10px; padding: 14px">
              <div style="font-size: 12px; font-weight: 600; color: var(--accent-deep); margin-bottom: 6px">💬 管理员回复</div>
              <div style="font-size: 13px; color: var(--ink); line-height: 1.6; white-space: pre-wrap">{{ selected.reply }}</div>
              <div v-if="selected.replied_at" style="font-size: 11px; color: var(--ink-mute); margin-top: 8px">{{ new Date(selected.replied_at).toLocaleString('zh-CN') }}</div>
            </div>

            <!-- Admin reply form -->
            <div v-if="isAdmin" style="display: flex; flex-direction: column; gap: 10px; border-top: 1px solid var(--rule-soft); padding-top: 14px">
              <label style="font-size: 12px; font-weight: 600; color: var(--ink)">回复内容</label>
              <textarea v-model="replyText" class="cfg-input" rows="4" placeholder="输入回复…" style="resize: vertical; font-family: inherit" />
              <div style="display: flex; gap: 8px; align-items: center">
                <select v-model="replyStatus" class="fb-select">
                  <option value="open">待处理</option>
                  <option value="in_progress">处理中</option>
                  <option value="resolved">已解决</option>
                  <option value="closed">已关闭</option>
                </select>
                <select v-model="replyPriority" class="fb-select">
                  <option value="low">低</option>
                  <option value="normal">中</option>
                  <option value="high">高</option>
                  <option value="urgent">紧急</option>
                </select>
                <button class="btn primary" :disabled="saving" @click="saveReply" style="margin-left: auto">{{ saving ? "保存中…" : "保存回复" }}</button>
              </div>
            </div>
          </div>
        </div>
        <div v-else class="section-card" style="display: flex; align-items: center; justify-content: center; color: var(--ink-mute); font-style: italic; min-height: 200px">
          选择一条反馈查看详情
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.fb-select {
  background: var(--bg-canvas); border: 1px solid var(--rule); border-radius: 6px;
  padding: 4px 8px; font-size: 12px; color: var(--ink); cursor: pointer; outline: none;
}
.fb-row {
  display: flex; gap: 12px; padding: 12px 18px; cursor: pointer;
  border-bottom: 1px solid var(--rule-soft); transition: background 120ms;
}
.fb-row:hover { background: var(--bg-hover); }
.fb-row.active { background: var(--accent-tint); }
.fb-cat-pill, .fb-st-pill {
  font-size: 10px; font-weight: 600; padding: 1px 6px; border-radius: 4px;
  border: 1px solid; background: var(--bg-canvas);
}
</style>
