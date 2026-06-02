<script setup lang="ts">
import { h, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { NCard, NDataTable, NSpin, NTag, NButton } from "naive-ui";
import { filesApi, type FileItem } from "@/api/files";
import { conversationsApi } from "@/api/conversations";
import { useNotificationStore } from "@/stores/notifications";
import Icon from "@/components/Icon.vue";

const router = useRouter();
const ns = useNotificationStore();
const files = ref<FileItem[]>([]);
const loading = ref(true);

onMounted(async () => {
  try {
    files.value = await filesApi.listAll();
  } catch {
    files.value = [];
  } finally {
    loading.value = false;
  }
});

function formatSize(bytes: number | null): string {
  if (!bytes) return "-";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatDate(iso: string): string {
  if (!iso) return "-";
  return new Date(iso).toLocaleDateString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function getFileIcon(name: string): string {
  const ext = name.split(".").pop()?.toLowerCase() || "";
  if (["jpg", "jpeg", "png", "gif", "svg", "webp"].includes(ext)) return "star";
  if (["pdf", "doc", "docx", "txt", "md"].includes(ext)) return "doc";
  if (["py", "js", "ts", "vue", "css", "html", "json"].includes(ext)) return "sparkle";
  return "paperclip";
}

async function downloadFile(row: FileItem) {
  try {
    const url = conversationsApi.fileRawUrl(row.conversation_id, row.id);
    window.open(url, "_blank");
  } catch {
    ns.toast("下载失败", "error");
  }
}

function goToConversation(row: FileItem) {
  router.push({ path: "/", query: { c: row.conversation_id } });
}

const columns = [
  {
    title: "",
    key: "icon",
    width: 36,
    render: (row: FileItem) =>
      h(Icon, { name: getFileIcon(row.name), size: 14, style: { color: "var(--accent)" } }),
  },
  {
    title: "文件名",
    key: "name",
    ellipsis: { tooltip: true },
    sorter: (a: FileItem, b: FileItem) => a.name.localeCompare(b.name),
  },
  {
    title: "大小",
    key: "size",
    width: 90,
    render: (row: FileItem) => formatSize(row.size),
    sorter: (a: FileItem, b: FileItem) => (a.size || 0) - (b.size || 0),
  },
  {
    title: "所属会话",
    key: "conversation_title",
    ellipsis: { tooltip: true },
    render: (row: FileItem) =>
      h(
        NButton,
        { text: true, size: "small", onClick: () => goToConversation(row) },
        () => row.conversation_title || "未命名"
      ),
  },
  {
    title: "上传时间",
    key: "created_at",
    width: 130,
    render: (row: FileItem) => formatDate(row.created_at),
    sorter: (a: FileItem, b: FileItem) => a.created_at.localeCompare(b.created_at),
  },
  {
    title: "",
    key: "actions",
    width: 60,
    render: (row: FileItem) =>
      h(
        NButton,
        { text: true, size: "small", title: "下载", onClick: () => downloadFile(row) },
        () => h(Icon, { name: "arrow_up", size: 14, style: { transform: "rotate(180deg)" } })
      ),
  },
];
</script>

<template>
  <div class="files-page">
    <div class="files-head">
      <Icon name="folder" :size="20" />
      <h2>文件管理</h2>
      <NTag v-if="files.length" size="small" type="info" style="margin-left: auto">
        {{ files.length }} 个文件
      </NTag>
    </div>

    <NSpin :show="loading">
      <NCard size="small" class="files-card">
        <NDataTable
          :columns="columns"
          :data="files"
          :max-height="600"
          :scrollbar-props="{ trigger: 'hover' }"
          :empty-text="'暂无文件'"
          size="small"
        />
      </NCard>
    </NSpin>
  </div>
</template>

<style scoped>
.files-page {
  max-width: 960px;
  margin: 0 auto;
  padding: 32px 24px;
}
.files-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 24px;
  color: var(--ink);
}
.files-head h2 {
  font-family: var(--font-serif);
  font-size: 22px;
  font-weight: 500;
  margin: 0;
}
.files-card {
  background: var(--bg-panel);
}
</style>
