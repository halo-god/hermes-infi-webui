<script setup lang="ts">
import { h, onMounted, ref, computed } from "vue";
import { useRouter } from "vue-router";
import { NCard, NDataTable, NSpin, NTag, NButton, NEmpty } from "naive-ui";
import { filesApi, type FileItem } from "@/api/files";
import { conversationsApi } from "@/api/conversations";
import { useNotificationStore } from "@/stores/notifications";
import Icon from "@/components/Icon.vue";
import ModalShell from "@/components/ModalShell.vue";

const router = useRouter();
const ns = useNotificationStore();
const files = ref<FileItem[]>([]);
const allFiles = ref<FileItem[]>([]);
const loading = ref(true);
const uploading = ref(false);
const dragover = ref(false);
const currentFolder = ref("/");
const newFolderName = ref("");
const showNewFolder = ref(false);

// Move file modal state
const showMoveModal = ref(false);
const moveTarget = ref<FileItem | null>(null);
const moveFolders = ref<{ path: string; label: string }[]>([]);
const selectedMoveFolder = ref("/");
const moveLoading = ref(false);

async function loadFiles(folder = "/") {
  loading.value = true;
  try {
    const [standalone, all] = await Promise.all([
      filesApi.listStandalone(folder),
      filesApi.listAll(),
    ]);
    files.value = standalone;
    allFiles.value = all;
  } catch {
    files.value = [];
  } finally {
    loading.value = false;
  }
}

onMounted(() => loadFiles("/"));

function formatSize(bytes: number | null): string {
  if (!bytes) return "-";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatDate(iso: string): string {
  if (!iso) return "-";
  return new Date(iso).toLocaleDateString("zh-CN", {
    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
  });
}

function getFileIcon(item: FileItem): string {
  if (item.is_folder) return "folder";
  const ext = (item.kind || item.name.split(".").pop() || "").toLowerCase();
  if (["jpg", "jpeg", "png", "gif", "svg", "webp"].includes(ext)) return "star";
  if (["pdf", "doc", "docx", "txt", "md"].includes(ext)) return "doc";
  if (["py", "js", "ts", "vue", "css", "html", "json"].includes(ext)) return "sparkle";
  return "paperclip";
}

// Breadcrumb
const breadcrumbs = computed(() => {
  const parts = currentFolder.value.split("/").filter(Boolean);
  const result = [{ path: "/", label: "根目录" }];
  let path = "";
  for (const p of parts) {
    path += "/" + p;
    result.push({ path, label: p });
  }
  return result;
});

function navigateTo(path: string) {
  currentFolder.value = path;
  loadFiles(path);
}

function enterFolder(item: FileItem) {
  if (item.is_folder) {
    // Real DB folders have UUID ids and folder_path == parent dir.
    // Virtual inferred folders have ids like "folder:/path" and folder_path == full path.
    const isVirtual = item.id.startsWith("folder:");
    if (isVirtual) {
      navigateTo(item.folder_path || "/");
    } else {
      const path = item.folder_path === "/" ? `/${item.name}` : `${item.folder_path}/${item.name}`;
      navigateTo(path);
    }
  }
}

async function downloadFile(row: FileItem) {
  try {
    if (row.is_folder) return;
    if (row.conversation_id && row.conversation_title && row.conversation_title !== "__file_storage__") {
      // 会话内文件
      window.open(conversationsApi.fileRawUrl(row.conversation_id, row.id), "_blank");
    } else {
      // 独立文件
      window.open(filesApi.rawUrl(row.id), "_blank");
    }
  } catch {
    ns.toast("下载失败", "error");
  }
}

async function deleteFile(row: FileItem) {
  if (row.is_folder) {
    if (!confirm(`确定删除文件夹 "${row.name}" 及其所有内容吗？此操作不可恢复。`)) return;
    try {
      await filesApi.removeFolder(row.id);
      await loadFiles(currentFolder.value);
      ns.toast("文件夹已删除", "ok");
    } catch {
      ns.toast("删除失败", "error");
    }
    return;
  }
  if (!confirm(`确定删除文件 "${row.name}" 吗？`)) return;
  try {
    await filesApi.remove(row.id);
    files.value = files.value.filter((f) => f.id !== row.id);
    ns.toast("已删除", "ok");
  } catch {
    ns.toast("删除失败", "error");
  }
}

// Create folder
async function createFolder() {
  if (!newFolderName.value.trim()) return;
  try {
    await filesApi.createFolder(newFolderName.value.trim(), currentFolder.value);
    newFolderName.value = "";
    showNewFolder.value = false;
    await loadFiles(currentFolder.value);
    ns.toast("文件夹已创建", "ok");
  } catch {
    ns.toast("创建失败", "error");
  }
}

// Upload
function triggerFileInput() {
  const input = document.createElement("input");
  input.type = "file";
  input.multiple = true;
  input.onchange = async (e) => {
    const target = e.target as HTMLInputElement;
    if (target.files) await uploadFiles(Array.from(target.files));
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
    if (target.files) await uploadFiles(Array.from(target.files));
  };
  input.click();
}

async function uploadFiles(fileList: File[]) {
  if (!fileList.length) return;
  uploading.value = true;
  let ok = 0, fail = 0;
  for (const file of fileList) {
    try {
      const result = await filesApi.upload(file, currentFolder.value);
      files.value.unshift(result);
      ok++;
    } catch { fail++; }
  }
  uploading.value = false;
  if (ok) ns.toast(`成功上传 ${ok} 个文件${fail ? `，${fail} 个失败` : ""}`, "ok");
  else if (fail) ns.toast("上传失败", "error");
}

function onDragover(e: DragEvent) { e.preventDefault(); dragover.value = true; }
function onDragleave() { dragover.value = false; }
async function onDrop(e: DragEvent) {
  e.preventDefault(); dragover.value = false;
  if (e.dataTransfer?.files) await uploadFiles(Array.from(e.dataTransfer.files));
}

function goToConversation(row: FileItem) {
  if (row.conversation_id) router.push({ path: "/", query: { c: row.conversation_id } });
}

// Move file to folder
async function openMoveModal(row: FileItem) {
  moveTarget.value = row;
  selectedMoveFolder.value = row.folder_path || "/";
  showMoveModal.value = true;
  try {
    moveFolders.value = await filesApi.listFolders();
  } catch {
    moveFolders.value = [{ path: "/", label: "根目录" }];
  }
}

async function confirmMove() {
  if (!moveTarget.value) return;
  moveLoading.value = true;
  try {
    await filesApi.moveToFolder(moveTarget.value.id, selectedMoveFolder.value);
    // Remove from current view if moved to a different folder
    files.value = files.value.filter((f) => f.id !== moveTarget.value!.id);
    ns.toast(`已移动到 ${selectedMoveFolder.value === "/" ? "根目录" : selectedMoveFolder.value}`, "ok");
    showMoveModal.value = false;
    moveTarget.value = null;
  } catch {
    ns.toast("移动失败", "error");
  } finally {
    moveLoading.value = false;
  }
}

const columns = [
  {
    title: "", key: "icon", width: 36,
    render: (row: FileItem) => h(Icon, { name: getFileIcon(row), size: 14, style: { color: row.is_folder ? "var(--accent)" : "var(--ink-mute)" } }),
  },
  {
    title: "文件名", key: "name", ellipsis: { tooltip: true },
    render: (row: FileItem) => row.is_folder
      ? h("span", { style: { cursor: "pointer", color: "var(--accent)", fontWeight: 500 }, onClick: () => enterFolder(row) }, row.name)
      : row.name,
  },
  {
    title: "大小", key: "size", width: 90,
    render: (row: FileItem) => row.is_folder ? "-" : formatSize(row.size),
  },
  {
    title: "来源", key: "source", width: 80,
    render: (row: FileItem) => row.is_folder
      ? h(NTag, { size: "small", type: "warning" }, () => "文件夹")
      : h(NTag, { size: "small", type: row.source === "ai" ? "success" : "info" }, () => row.source === "ai" ? "AI生成" : "上传"),
  },
  {
    title: "所属会话", key: "conversation_title", ellipsis: { tooltip: true },
    render: (row: FileItem) => {
      if (row.is_folder || !row.conversation_title || row.conversation_title === "__file_storage__") {
        return h("span", { style: { color: "var(--ink-mute)", fontSize: "12px" } }, row.is_folder ? "" : "独立文件");
      }
      return h(NButton, { text: true, size: "small", onClick: () => goToConversation(row) }, () => row.conversation_title);
    },
  },
  {
    title: "上传时间", key: "created_at", width: 130,
    render: (row: FileItem) => row.is_folder ? "" : formatDate(row.created_at),
  },
  {
    title: "", key: "actions", width: 110,
    render: (row: FileItem) => {
      if (row.is_folder) {
        return h("div", { style: { display: "flex", gap: "4px", justifyContent: "flex-end" } }, [
          h(NButton, { text: true, size: "small", title: "删除文件夹", onClick: () => deleteFile(row) },
            () => h(Icon, { name: "close", size: 14, style: { color: "var(--error)" } })),
        ]);
      }
      return h("div", { style: { display: "flex", gap: "4px" } }, [
        h(NButton, { text: true, size: "small", title: "移动到文件夹", onClick: () => openMoveModal(row) },
          () => h(Icon, { name: "folder", size: 14 })),
        h(NButton, { text: true, size: "small", title: "下载", onClick: () => downloadFile(row) },
          () => h(Icon, { name: "arrow_up", size: 14, style: { transform: "rotate(180deg)" } })),
        h(NButton, { text: true, size: "small", title: "删除", onClick: () => deleteFile(row) },
          () => h(Icon, { name: "close", size: 14, style: { color: "var(--error)" } })),
      ]);
    },
  },
];

// Expose current folder for parent components
defineExpose({ currentFolder });
</script>

<template>
  <div class="files-page" @dragover="onDragover" @dragleave="onDragleave" @drop="onDrop">
    <div class="files-head">
      <Icon name="folder" :size="20" />
      <h2>文件管理</h2>
      <NTag v-if="files.length" size="small" type="info" style="margin-left: auto">{{ files.length }} 项</NTag>
    </div>

    <!-- Breadcrumb -->
    <div class="files-breadcrumb">
      <span v-for="(bc, i) in breadcrumbs" :key="bc.path">
        <span v-if="i > 0" class="bc-sep">/</span>
        <button class="bc-link" :class="{ active: i === breadcrumbs.length - 1 }" @click="navigateTo(bc.path)">{{ bc.label }}</button>
      </span>
    </div>

    <!-- Toolbar -->
    <div class="files-toolbar">
      <button class="files-btn" :disabled="uploading" @click="triggerFileInput">
        <Icon name="arrow_up" :size="14" /> {{ uploading ? "上传中..." : "上传文件" }}
      </button>
      <button class="files-btn" :disabled="uploading" @click="triggerFolderInput">
        <Icon name="folder" :size="14" /> 上传文件夹
      </button>
      <button class="files-btn" @click="showNewFolder = !showNewFolder">
        <Icon name="folder" :size="14" /> 新建文件夹
      </button>
      <div class="files-hint">拖放文件到此处也可上传</div>
    </div>

    <!-- New folder input -->
    <div v-if="showNewFolder" class="files-new-folder">
      <input v-model="newFolderName" class="cfg-input" placeholder="文件夹名称" @keydown.enter="createFolder" autofocus />
      <button class="btn primary" style="font-size:12px" :disabled="!newFolderName.trim()" @click="createFolder">创建</button>
      <button class="btn" style="font-size:12px" @click="showNewFolder = false; newFolderName = ''">取消</button>
    </div>

    <!-- Drag overlay -->
    <div v-if="dragover" class="files-drop-overlay">
      <Icon name="arrow_up" :size="32" />
      <div>释放鼠标上传文件</div>
    </div>

    <NSpin :show="loading">
      <NCard size="small" class="files-card">
        <NDataTable
          v-if="files.length"
          :columns="columns"
          :data="files"
          :max-height="600"
          :scrollbar-props="{ trigger: 'hover' }"
          :empty-text="'暂无文件'"
          size="small"
        />
        <NEmpty v-else description="暂无文件，点击上方按钮或拖放文件上传" />
      </NCard>
    </NSpin>

    <!-- Move to folder modal -->
    <ModalShell v-if="showMoveModal" title="移动到文件夹" :width="420" @close="showMoveModal = false">
      <div class="move-modal-body">
        <div class="move-file-info">
          <Icon name="doc" :size="14" />
          <span>{{ moveTarget?.name }}</span>
        </div>
        <div class="move-folder-list">
          <label
            v-for="folder in moveFolders"
            :key="folder.path"
            class="move-folder-item"
            :class="{ selected: selectedMoveFolder === folder.path }"
          >
            <input
              type="radio"
              name="move-folder"
              :value="folder.path"
              v-model="selectedMoveFolder"
            />
            <Icon name="folder" :size="14" :style="{ color: selectedMoveFolder === folder.path ? 'var(--accent)' : 'var(--ink-mute)' }" />
            <span>{{ folder.label }}</span>
            <span class="move-folder-path">{{ folder.path }}</span>
          </label>
        </div>
      </div>
      <template #foot>
        <button class="btn" @click="showMoveModal = false">取消</button>
        <button class="btn primary" :disabled="moveLoading || selectedMoveFolder === (moveTarget?.folder_path || '/')" @click="confirmMove">
          {{ moveLoading ? "移动中..." : "确认移动" }}
        </button>
      </template>
    </ModalShell>
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
  margin-bottom: 12px;
  color: var(--ink);
}
.files-head h2 { font-family: var(--font-serif); font-size: 22px; font-weight: 500; margin: 0; }
.files-breadcrumb {
  display: flex;
  align-items: center;
  gap: 2px;
  margin-bottom: 12px;
  font-size: 13px;
}
.bc-sep { color: var(--ink-mute); margin: 0 4px; }
.bc-link {
  background: none; border: none; cursor: pointer;
  color: var(--accent); font-size: 13px; padding: 2px 4px;
  border-radius: 4px; transition: background 120ms;
}
.bc-link:hover { background: var(--accent-tint); }
.bc-link.active { color: var(--ink); font-weight: 600; cursor: default; }
.bc-link.active:hover { background: transparent; }
.files-toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
  padding: 12px 16px;
  background: var(--bg-panel);
  border: 1px dashed var(--rule);
  border-radius: var(--r-md);
}
.files-btn {
  display: flex; align-items: center; gap: 6px;
  padding: 8px 16px; background: var(--accent); color: #fff;
  border: none; border-radius: var(--r-sm); font-size: 13px; font-weight: 500;
  cursor: pointer; transition: opacity 150ms;
}
.files-btn:hover { opacity: 0.9; }
.files-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.files-hint { font-size: 12px; color: var(--ink-mute); margin-left: auto; }
.files-new-folder {
  display: flex; align-items: center; gap: 8px;
  margin-bottom: 12px; padding: 10px 16px;
  background: var(--bg-panel); border-radius: var(--r-sm);
}
.files-new-folder .cfg-input { flex: 1; height: 32px; font-size: 13px; }
.files-drop-overlay {
  position: absolute; inset: 0;
  background: rgba(184, 133, 42, 0.1);
  border: 2px dashed var(--accent); border-radius: var(--r-md);
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  gap: 12px; z-index: 10; color: var(--accent); font-size: 16px; font-weight: 500;
  pointer-events: none;
}
.files-card { background: var(--bg-panel); }
.move-modal-body { display: flex; flex-direction: column; gap: 12px; }
.move-file-info {
  display: flex; align-items: center; gap: 8px;
  padding: 8px 12px; background: var(--bg-panel); border-radius: var(--r-sm);
  font-size: 13px; font-weight: 500;
}
.move-folder-list {
  max-height: 300px; overflow-y: auto;
  border: 1px solid var(--rule); border-radius: var(--r-sm);
}
.move-folder-item {
  display: flex; align-items: center; gap: 8px;
  padding: 10px 12px; cursor: pointer; font-size: 13px;
  border-bottom: 1px solid var(--rule); transition: background 120ms;
}
.move-folder-item:last-child { border-bottom: none; }
.move-folder-item:hover { background: var(--accent-tint); }
.move-folder-item.selected { background: var(--accent-tint); font-weight: 500; }
.move-folder-item input[type="radio"] { margin: 0; }
.move-folder-path { margin-left: auto; font-size: 11px; color: var(--ink-mute); }
</style>
