<script setup lang="ts">
/* 资源广场 - 浏览和复制管理员发布的公共资源（数字员工/SOP/知识库/工具）。 */
import { computed, onMounted, ref } from "vue";
import Icon from "@/components/Icon.vue";
import { useAuthStore } from "@/stores/auth";
import { useNotificationStore } from "@/stores/notifications";
import { galleryApi, type GalleryItem } from "@/api/gallery";

const auth = useAuthStore();
const ns = useNotificationStore();

const items = ref<GalleryItem[]>([]);
const loading = ref(false);
const galleryTab = ref<"profile" | "sop" | "knowledge" | "tool">("profile");
const copying = ref<string | null>(null);

const TAB_LABELS: Record<string, string> = {
  profile: "数字员工",
  sop: "SOP 技能",
  knowledge: "知识库",
  tool: "工具",
};

const TAB_ICONS: Record<string, string> = {
  profile: "user",
  sop: "bolt",
  knowledge: "doc",
  tool: "sparkle",
};

const filtered = computed(() => items.value.filter((i) => i.type === galleryTab.value));
const isAdmin = computed(() => auth.user?.role === "admin" || auth.user?.role === "super_admin");

async function load() {
  loading.value = true;
  try {
    items.value = await galleryApi.list();
  } catch {
    items.value = [];
  } finally {
    loading.value = false;
  }
}

async function copyItem(item: GalleryItem) {
  copying.value = item.id;
  try {
    const result = await galleryApi.copy(item.id);
    if (result.new_id) {
      ns.toast(`已复制「${item.name}」到你的工作区`);
    } else if (result.snapshot) {
      ns.toast(`「${item.name}」的配置快照已返回，请手动导入`);
    }
  } catch {
    ns.toast("复制失败", "error");
  } finally {
    copying.value = null;
  }
}

async function removeItem(item: GalleryItem) {
  if (!confirm(`从广场下架「${item.name}」？`)) return;
  try {
    await galleryApi.remove(item.id);
    await load();
    ns.toast("已下架");
  } catch {
    ns.toast("下架失败", "error");
  }
}

onMounted(load);
</script>

<template>
  <div class="stage">
    <div class="admin-hero">
      <div class="admin-hero-row">
        <span class="admin-badge"><Icon name="globe" :size="11" /> GALLERY</span>
        <span style="font-size: 11.5px; color: var(--ink-mute)">资源广场</span>
      </div>
      <h1 class="admin-title">资源<em>广场</em></h1>
      <div class="admin-sub">浏览管理员发布的公共资源，一键复制到你的工作区使用。</div>

      <div class="admin-tabs">
        <button
          v-for="(label, key) in TAB_LABELS"
          :key="key"
          class="team-tab"
          :class="{ active: galleryTab === key }"
          @click="galleryTab = key as typeof galleryTab"
        >
          <Icon :name="TAB_ICONS[key]" :size="12" style="margin-right:4px" /> {{ label }}
        </button>
      </div>
    </div>

    <div class="admin-body">
      <div v-if="loading" class="empty-state-lg" style="padding:40px;text-align:center;color:var(--ink-mute)">加载中…</div>
      <div v-else-if="!filtered.length" class="empty-state-lg" style="padding:40px;text-align:center;color:var(--ink-mute)">
        <Icon :name="TAB_ICONS[galleryTab]" :size="28" style="color:var(--ink-faint);margin-bottom:8px" />
        <div>广场暂无{{ TAB_LABELS[galleryTab] }}资源</div>
        <div v-if="isAdmin" style="font-size:12px;margin-top:4px">在管理后台的对应页面点击「发布到广场」来共享资源</div>
      </div>
      <div v-else style="display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:14px">
        <div v-for="item in filtered" :key="item.id" class="emp-card">
          <div style="display:flex;align-items:center;gap:10px">
            <div class="emp-avatar" :style="{ background: item.color || '#b8852a' }">
              <Icon :name="item.icon || 'sparkle'" :size="18" />
            </div>
            <div style="flex:1;min-width:0">
              <div style="font-size:14px;font-weight:600;color:var(--ink)">{{ item.name }}</div>
              <div style="font-size:11.5px;color:var(--ink-mute)">{{ item.description || '无描述' }}</div>
            </div>
          </div>
          <div style="display:flex;flex-wrap:wrap;gap:4px;margin-top:8px">
            <span v-if="item.category" class="emp-tag">{{ item.category }}</span>
            <span class="emp-tag">{{ item.download_count }} 次复制</span>
            <span v-if="item.published_by_name" class="emp-tag">by {{ item.published_by_name }}</span>
          </div>
          <div style="display:flex;gap:4px;margin-top:10px;border-top:1px solid var(--rule-soft);padding-top:8px">
            <button class="btn primary" style="flex:1;font-size:12px;padding:4px" :disabled="copying === item.id" @click="copyItem(item)">
              <Icon name="copy" :size="12" /> {{ copying === item.id ? "复制中…" : "复制到工作区" }}
            </button>
            <button v-if="isAdmin" class="icon-btn" title="下架" style="color:var(--danger)" @click="removeItem(item)"><Icon name="close" :size="13" /></button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
