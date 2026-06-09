<script setup lang="ts">
import { h, onMounted, ref, computed } from "vue";
import { useRouter } from "vue-router";
import { NCard, NDataTable, NSpin, NTag, NButton, NTabs, NTabPane, NEmpty } from "naive-ui";
import { filesApi, type FileItem } from "@/api/files";
import { conversationsApi } from "@/api/conversations";
import { useNotificationStore } from "@/stores/notifications";
import Icon from "@/components/Icon.vue";

const router = useRouter();
const ns = useNotificationStore();
const files = ref<FileItem[]>([]);
const standaloneFiles = ref<FileItem[]>([]);
const loading = ref(true);
const uploading = ref(false);
const activeTab = ref("all");
const dragover = ref(false);

onMounted(async () => {
  try {
    const [allFiles, standalone] = await Promise.all([
      filesApi.listAll(),
      filesApi.listStandalone(),
    ]);
    files.value = allFiles;
    standaloneFiles.value = standalone;
  } catch {
    files.value = [];
    standaloneFiles.value = [];
  } finally {
    loading.value = false;
  }
});

const displayFiles = computed(() => {
  if (activeTab.value === "standalone") return standaloneFiles.value;
  return [...standaloneFiles.value, ...files.value];
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
    if (row.conversation_id) {
      const url = conversationsApi.fileRawUrl(row.conversation_id, row.id);
      window.open(url, "_blank");
    } else {
      ns.toast("独立文件暂不支持直接下载", "warn");
    }
  } catch {
    ns.toast("下载失败", "error");
  }
}

async function deleteFile(row: FileItem) {
  if (!confirm(`确定删除文件 "${row.name}" 吗？`)) return;
  try {
    await filesApi.remove(row.id);
    standaloneFiles.value = standaloneFiles.value.filter((f) => f.id !== row.id);
    files.value = files.value.filter((f) => f.id !== row.id);
    ns.toast("已删除", "ok");
  } catch {
    ns.toast("删除失败", "error");
  }
}

function goToConversation(row: FileItem) {
  if (row.conversation_id) {
    router.push({ path: "/", query: { c: row.conversation_id } });
  }
}

// Upload handling
function triggerFileInput() {
  const input = document.createElement("input");
  input.type = "file";
  input.multiple = true;
  input.onchange = async (e) => {
    const target = e.target as HTMLInputElement;
    if (target.files) {
      await uploadFiles(Array.from(target.files));
    }
  };
  input.click();
}

function triggerFolderInput() {
  const input = document.createElement("input");
  input.type = "file";
  input.webkitdirectory = true;
  input.multiple = true;
  input.onchange = async (e) => {
    const target = e.target as HTMLInputElement;
    if (target.files) {
      await uploadFiles(Array.from(target.files));
    }
  };
  input.click();
}

async function uploadFiles(fileList: File[]) {
  if (!fileList.length) return;
  uploading.value = true;
  let successCount = 0;
  let failCount = 0;

  for (const file of fileList) {
    try {
      const result = await filesApi.upload(file);
      standaloneFiles.value.unshift(result);
      successCount++;
    } catch {
      failCount++;
    }
  }

  uploading.value = false;
  if (successCount > 0) {
    ns.toast(`成功上传 ${successCount} 个文件${failCount > 0 ? `，${failCount} 个失败` : ""}`, "ok");
  } else if (failCount > 0) {
    ns.toast("上传失败", "error");
  }
}

// Drag and drop
function onDragover(e: DragEvent) {
  e.preventDefault();
  dragover.value = true;
}

function onDragleave() {
  dragover.value = false;
}

async function onDrop(e: DragEvent) {
  e.preventDefault();
  dragover.value = false;
  if (e.dataTransfer?.files) {
    await uploadFiles(Array.from(e.dataTransfer.files));
  }
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
    title: "来源",
    key: "source",
    width: 80,
    render: (row: FileItem) =>
      h(
        NTag,
        { size: "small", type: row.source === "ai" ? "success" : "info" },
        () => row.source === "ai" ? "AI生成" : "上传"
      ),
  },
  {
    title: "所属会话",
    key: "conversation_title",
    ellipsis: { tooltip: true },
    render: (row: FileItem) => {
      if (!row.conversation_title || row.conversation_title === "__file_storage__") {
        return h("span", { style: { color: "var(--ink-mute)", fontSize: "12px" } }, "独立文件");
      }
      return h(
        NButton,
        { text: true, size: "small", onClick: () => goToConversation(row) },
        () => row.conversation_title
      );
    },
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
    width: 80,
    render: (row: FileItem) =>
      h("div", { style: { display: "flex", gap: "4px" } }, [
        h(
          NButton,
          { text: true, size: "small", title: "下载", onClick: () => downloadFile(row) },
          () => h(Icon, { name: "arrow_up", size: 14, style: { transform: "rotate(180deg)" } })
        ),
        row.conversation_title === "__file_storage__"
          ? h(
              NButton,
              { text: true, size: "small", title: "删除", onClick: () => deleteFile(row) },
              () => h(Icon, { name: "x", size: 14, style: { color: "var(--error)" } })
            )
          : null,
      ]),
  },
];
</script>

<template>
  <div
    class="files-page"
    @dragover="onDragover"
    @dragleave="onDragleave"
    @drop="onDrop"
  >
    <div class="files-head">
      <Icon name="folder" :size="20" />
      <h2>文件管理</h2>
      <NTag v-if="displayFiles.length" size="small" type="info" style="margin-left: auto">
        {{ displayFiles.length }} 个文件
      </NTag>
    </div>

    <!-- Upload area -->
    <div class="files-upload-bar">
      <button class="files-upload-btn" :disabled="uploading" @click="triggerFileInput">
        <Icon name="arrow_up" :size="14" />
        {{ uploading ? "上传中..." : "上传文件" }}
      </button>
      <button class="files-upload-btn" :disabled="uploading" @click="triggerFolderInput">
        <Icon name="folder" :size="14" />
        上传文件夹
      </button>
      <div class="files-upload-hint">
        拖放文件到此处也可上传
      </div>
    </div>

    <!-- Drag overlay -->
    <div v-if="dragover" class="files-drop-overlay">
      <Icon name="arrow_up" :size="32" />
      <div>释放鼠标上传文件</div>
    </div>

    <!-- Tabs -->
    <NTabs v-model:value="activeTab" type="segment" size="small" class="files-tabs">
      <NTabPane name="all" tab="全部文件" />
      <NTabPane name="standalone" tab="独立文件" />
    </NTabs>

    <NSpin :show="loading">
      <NCard size="small" class="files-card">
        <NDataTable
          v-if="displayFiles.length"
          :columns="columns"
          :data="displayFiles"
          :max-height="600"
          :scrollbar-props="{ trigger: 'hover' }"
          :empty-text="'暂无文件'"
          size="small"
        />
        <NEmpty v-else description="暂无文件，点击上方按钮或拖放文件上传" />
      </NCard>
    </NSpin>
  </div>
</template>

<style scoped>
.files-page {
  max-width: 1400px;
  margin: 0 auto;
  padding: 32px 24px;
  position: relative;
}
.files-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 16px;
  color: var(--ink);
}
.files-head h2 {
  font-family: var(--font-serif);
  font-size: 22px;
  font-weight: 500;
  margin: 0;
}
.files-upload-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 16px;
  padding: 12px 16px;
  background: var(--bg-panel);
  border: 1px dashed var(--rule);
  border-radius: var(--r-md);
  transition: border-color 150ms;
}
.files-upload-bar:hover {
  border-color: var(--accent-soft);
}
.files-upload-btn {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 16px;
  background: var(--accent);
  color: #fff;
  border: none;
  border-radius: var(--r-sm);
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: opacity 150ms;
}
.files-upload-btn:hover {
  opacity: 0.9;
}
.files-upload-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.files-upload-hint {
  font-size: 12px;
  color: var(--ink-mute);
  margin-left: auto;
}
.files-drop-overlay {
  position: absolute;
  inset: 0;
  background: rgba(var(--accent-rgb, 184, 133, 42), 0.1);
  border: 2px dashed var(--accent);
  border-radius: var(--r-md);
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  z-index: 10;
  color: var(--accent);
  font-size: 16px;
  font-weight: 500;
  pointer-events: none;
}
.files-tabs {
  margin-bottom: 16px;
}
.files-card {
  background: var(--bg-panel);
}
</style>
