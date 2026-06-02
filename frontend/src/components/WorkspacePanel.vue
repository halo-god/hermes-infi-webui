<script setup lang="ts">
import { computed, ref, watch } from "vue";
import Icon from "@/components/Icon.vue";
import { renderMarkdown } from "@/utils/markdown";
import type { FileItem, WsAdapter, WorkspaceFileVersion } from "@/types";

const props = defineProps<{
  files: FileItem[];
  adapter: WsAdapter;
  title?: string;
  uploadable?: boolean;
  initialFileId?: string;
}>();
const emit = defineEmits<{ close: [] }>();

// ── Multi-tab state ──
interface Tab { id: string; fileId: string }
const openTabs = ref<Tab[]>([]);
const activeTabId = ref<string | null>(null);
const content = ref<string>("");
const loading = ref(false);
const editMode = ref(false);
const editContent = ref("");
const saving = ref(false);
const fullscreen = ref(false);
const showVersions = ref(false);
const versions = ref<WorkspaceFileVersion[]>([]);
const versionsLoading = ref(false);
const previewVersion = ref<{ num: number; content: string } | null>(null);
const newFileBadges = ref<Set<string>>(new Set());
const uploadInput = ref<HTMLInputElement | null>(null);
const uploading = ref(false);

const MAX_TABS = 8;

const activeTab = computed(() => openTabs.value.find((t) => t.id === activeTabId.value) || null);
const activeFile = computed(() => {
  const fid = activeTab.value?.fileId;
  return fid ? props.files.find((f) => f.id === fid) || null : null;
});

// ── Format detection ──
const IMAGE_EXTS = new Set(["png", "jpg", "jpeg", "gif", "svg", "webp", "bmp", "ico"]);
const CODE_EXTS = new Set(["ts", "tsx", "js", "jsx", "py", "go", "rs", "sh", "bash", "yaml",
  "yml", "toml", "css", "scss", "sql", "rb", "java", "c", "cpp", "h", "hpp", "kt", "swift"]);
const EDITABLE_KINDS = new Set(["md", "txt", "log", "json", "html", "htm", "diff", "patch", ...CODE_EXTS]);

function fileExt(f: FileItem): string {
  if (!f.name.includes(".")) return f.kind;
  const parts = f.name.split(".");
  return parts[parts.length - 1].toLowerCase();
}
function fileMode(f: FileItem | null): string {
  if (!f) return "unknown";
  const e = fileExt(f);
  if (e === "md" || e === "docx") return "md";
  if (e === "json") return "json";
  if (e === "csv") return "csv";
  if (e === "html" || e === "htm") return "html";
  if (e === "pdf") return "pdf";
  if (IMAGE_EXTS.has(e)) return "image";
  if (e === "diff" || e === "patch") return "diff";
  if (CODE_EXTS.has(e)) return "code";
  if (e === "txt" || e === "log") return "txt";
  return "unknown";
}

// ── Renderers ──
const mdHtml = computed(() => {
  const src = previewVersion.value?.content ?? content.value;
  return renderMarkdown(src);
});
const jsonPretty = computed(() => {
  const src = previewVersion.value?.content ?? content.value;
  try { return JSON.stringify(JSON.parse(src), null, 2); } catch { return src; }
});
const csvRows = computed(() => {
  const src = previewVersion.value?.content ?? content.value;
  return src.trim().split(/\r?\n/).map((r) => r.split(",").map((c) => c.trim()));
});
const diffLines = computed(() => {
  const src = previewVersion.value?.content ?? content.value;
  return src.split("\n").map((l) => ({
    text: l,
    cls: l.startsWith("+") ? "diff-add" : l.startsWith("-") ? "diff-del"
       : l.startsWith("@@") ? "diff-hunk" : "diff-ctx",
  }));
});
const codeHighlighted = computed(() => {
  const src = previewVersion.value?.content ?? content.value;
  return highlightCode(src, activeFile.value ? fileExt(activeFile.value) : "");
});
const rawUrl = computed(() =>
  activeFile.value ? props.adapter.getRawUrl(activeFile.value.id) : ""
);

function highlightCode(code: string, language: string): string {
  const escaped = code
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");

  const KEYWORDS_JS = /\b(const|let|var|function|return|if|else|for|while|import|export|from|class|new|this|async|await|try|catch|throw|typeof|instanceof|in|of|null|undefined|true|false)\b/g;
  const KEYWORDS_PY = /\b(def|class|import|from|return|if|elif|else|for|while|with|as|pass|break|continue|try|except|finally|raise|lambda|and|or|not|in|is|None|True|False|async|await|yield)\b/g;
  const KEYWORDS_GO = /\b(func|package|import|var|const|type|struct|interface|return|if|else|for|range|switch|case|default|go|defer|chan|map|make|new|nil|true|false|break|continue)\b/g;
  const KEYWORDS_RUST = /\b(fn|let|mut|struct|impl|trait|pub|use|mod|type|enum|match|if|else|for|while|loop|return|self|true|false|Some|None|Ok|Err|async|await)\b/g;

  const kwMap: Record<string, RegExp> = {
    ts: KEYWORDS_JS, tsx: KEYWORDS_JS, js: KEYWORDS_JS, jsx: KEYWORDS_JS,
    py: KEYWORDS_PY, go: KEYWORDS_GO, rs: KEYWORDS_RUST,
  };

  let result = escaped
    .replace(/(\/\/[^\n]*|#[^\n]*(?=\n|$))/g, '<span class="hl-comment">$1</span>')
    .replace(/("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|`(?:[^`\\]|\\.)*`)/g, '<span class="hl-str">$1</span>');

  const kw = kwMap[language];
  if (kw) result = result.replace(kw, '<span class="hl-kw">$1</span>');

  result = result.replace(/\b(\d+\.?\d*)\b/g, '<span class="hl-num">$1</span>');

  return result;
}

// ── Tab management ──
function activateTab(tabId: string) {
  activeTabId.value = tabId;
  const tab = openTabs.value.find((t) => t.id === tabId);
  if (tab) {
    const f = props.files.find((x) => x.id === tab.fileId);
    if (f) loadContent(f);
  }
}

function openFile(f: FileItem) {
  const existing = openTabs.value.find((t) => t.fileId === f.id);
  if (existing) {
    activeTabId.value = existing.id;
  } else {
    if (openTabs.value.length >= MAX_TABS) openTabs.value.shift();
    const tab: Tab = { id: `tab-${f.id}`, fileId: f.id };
    openTabs.value.push(tab);
    activeTabId.value = tab.id;
  }
  loadContent(f);
}

function closeTab(tabId: string) {
  const idx = openTabs.value.findIndex((t) => t.id === tabId);
  openTabs.value.splice(idx, 1);
  if (activeTabId.value === tabId) {
    const next = openTabs.value[Math.max(0, idx - 1)];
    if (next) {
      activeTabId.value = next.id;
      const f = props.files.find((f) => f.id === next.fileId);
      if (f) loadContent(f);
    } else {
      activeTabId.value = null;
      content.value = "";
    }
  }
}

async function loadContent(f: FileItem) {
  const mode = fileMode(f);
  editMode.value = false;
  previewVersion.value = null;
  if (mode === "image" || mode === "pdf") {
    content.value = "";
    loading.value = false;
    return;
  }
  loading.value = true;
  try {
    content.value = await props.adapter.getContent(f.id);
  } finally {
    loading.value = false;
  }
}

// Auto-open file when panel opens.
watch(
  () => props.files,
  (files) => {
    files.forEach((f) => {
      const inTab = openTabs.value.find((t) => t.fileId === f.id);
      if (!inTab) return;
      if (activeTab.value?.fileId === f.id) {
        loadContent(f);
      } else {
        newFileBadges.value.add(f.id);
        setTimeout(() => { newFileBadges.value.delete(f.id); }, 3000);
      }
    });
    if (!openTabs.value.length && files.length) {
      const initial = props.initialFileId ? files.find((f) => f.id === props.initialFileId) : null;
      openFile(initial || files[0]);
    }
  },
  { immediate: true },
);

// ── Edit mode ──
function startEdit() {
  editContent.value = content.value;
  editMode.value = true;
  showVersions.value = false;
}
async function saveEdit() {
  if (!activeFile.value || saving.value || !props.adapter.patchContent) return;
  saving.value = true;
  try {
    content.value = await props.adapter.patchContent(activeFile.value.id, editContent.value);
    editMode.value = false;
  } finally {
    saving.value = false;
  }
}

// ── Version history ──
async function toggleVersions() {
  showVersions.value = !showVersions.value;
  if (showVersions.value && activeFile.value && props.adapter.getVersions) {
    versionsLoading.value = true;
    try {
      versions.value = await props.adapter.getVersions(activeFile.value.id);
    } finally {
      versionsLoading.value = false;
    }
  }
}
async function previewVer(v: WorkspaceFileVersion) {
  if (!activeFile.value) return;
  versionsLoading.value = true;
  try {
    const fetched = await props.adapter.getContent(activeFile.value.id);
    previewVersion.value = { num: v.version_num, content: fetched };
  } finally {
    versionsLoading.value = false;
  }
}
async function restoreVer(v: WorkspaceFileVersion) {
  if (!activeFile.value || saving.value || !props.adapter.restoreVersion) return;
  saving.value = true;
  try {
    content.value = await props.adapter.restoreVersion(activeFile.value.id, v.version_num);
    previewVersion.value = null;
    showVersions.value = false;
  } finally {
    saving.value = false;
  }
}

// ── Upload ──
function triggerUpload() {
  uploadInput.value?.click();
}
async function handleUpload(e: Event) {
  const file = (e.target as HTMLInputElement).files?.[0];
  if (!file || !props.adapter.upload) return;
  uploading.value = true;
  try {
    await props.adapter.upload(file);
  } finally {
    uploading.value = false;
    if (uploadInput.value) uploadInput.value.value = "";
  }
}

function download() {
  if (!activeFile.value) return;
  const mode = fileMode(activeFile.value);
  if (mode === "image" || mode === "pdf") {
    window.open(rawUrl.value, "_blank");
    return;
  }
  const blob = new Blob([content.value], { type: "text/plain" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = activeFile.value.name;
  a.click();
  URL.revokeObjectURL(url);
}

function fmtSize(b: number) {
  if (b < 1024) return `${b} B`;
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`;
  return `${(b / 1024 / 1024).toFixed(1)} MB`;
}
function fmtDate(s: string) {
  const d = new Date(s);
  return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}
</script>

<template>
  <aside class="workspace" :class="{ fullscreen }">
    <!-- Hidden upload input -->
    <input ref="uploadInput" type="file" style="display:none" @change="handleUpload" />

    <!-- Header -->
    <div class="ws-head">
      <div class="ws-title">▤ {{ title || '工作区' }} <span class="ws-count">{{ files.length }} 份文件</span></div>
      <div class="ws-actions">
        <button
          v-if="uploadable && adapter.upload"
          class="ws-btn"
          :disabled="uploading"
          title="上传文件"
          @click="triggerUpload"
        ><Icon name="paperclip" :size="14" /> {{ uploading ? '上传中…' : '上传' }}</button>
        <button
          v-if="activeFile && adapter.patchContent && EDITABLE_KINDS.has(fileExt(activeFile)) && !editMode"
          class="ws-btn"
          title="编辑"
          @click="startEdit"
        ><Icon name="edit" :size="14" /></button>
        <button class="ws-btn" title="下载" @click="download"><Icon name="arrow_up" style="transform:rotate(180deg)" :size="14" /></button>
        <button
          v-if="activeFile && adapter.getVersions"
          class="ws-btn"
          :class="{ active: showVersions }"
          title="版本历史"
          @click="toggleVersions"
        ><Icon name="refresh" :size="14" /> 版本历史</button>
        <button class="ws-btn" :class="{ active: fullscreen }" title="全屏" @click="fullscreen = !fullscreen">
          <Icon name="share" :size="14" />
        </button>
        <button class="ws-x" @click="emit('close')">×</button>
      </div>
    </div>

    <!-- Tabs -->
    <div class="ws-tabs" v-if="openTabs.length">
      <button
        v-for="tab in openTabs"
        :key="tab.id"
        class="ws-tab"
        :class="{ active: tab.id === activeTabId }"
        @click="activateTab(tab.id)"
      >
        <span class="ws-tab-kind">{{ files.find(x => x.id === tab.fileId)?.kind || '?' }}</span>
        {{ files.find(x => x.id === tab.fileId)?.name || tab.fileId }}
        <span
          v-if="newFileBadges.has(tab.fileId)"
          class="ws-tab-badge"
        >NEW</span>
        <span class="ws-tab-close" @click.stop="closeTab(tab.id)">×</span>
      </button>
    </div>

    <!-- Body -->
    <div class="ws-body">
      <!-- File tree -->
      <div class="ws-tree">
        <button
          v-for="f in files"
          :key="f.id"
          class="ws-file"
          :class="{ active: activeFile?.id === f.id }"
          @click="openFile(f)"
        >
          <span class="kind">{{ fileExt(f) }}</span>
          <span class="nm">{{ f.name }}</span>
          <span v-if="newFileBadges.has(f.id)" class="ws-new-badge">NEW</span>
          <span v-if="f.current_version !== undefined" class="ver">v{{ f.current_version }}</span>
        </button>
        <div v-if="!files.length" class="ws-empty">暂无文件</div>
      </div>

      <!-- Preview + Version History -->
      <div class="ws-content-wrap">
        <!-- Version history drawer -->
        <div v-if="showVersions" class="ws-versions">
          <div class="ws-ver-head">
            版本历史
            <button class="ws-btn" @click="previewVersion = null; showVersions = false">×</button>
          </div>
          <div v-if="versionsLoading" class="ws-loading">加载中…</div>
          <div v-else-if="!versions.length" class="ws-empty" style="padding: 16px">暂无历史版本</div>
          <div v-else class="ws-ver-list">
            <div
              v-for="v in versions"
              :key="v.version_num"
              class="ws-ver-item"
              :class="{ preview: previewVersion?.num === v.version_num }"
            >
              <div class="ws-ver-info">
                <span class="ws-ver-num">v{{ v.version_num }}</span>
                <span class="ws-ver-date">{{ fmtDate(v.created_at) }}</span>
                <span class="ws-ver-size">{{ fmtSize(v.size_bytes) }}</span>
              </div>
              <div class="ws-ver-btns">
                <button class="ws-btn" @click="previewVer(v)">预览</button>
                <button v-if="adapter.restoreVersion" class="ws-btn" @click="restoreVer(v)" :disabled="saving">恢复</button>
              </div>
            </div>
          </div>
          <div v-if="previewVersion" class="ws-ver-preview-banner">
            预览 v{{ previewVersion.num }} — <button class="ws-btn" @click="previewVersion = null">退出预览</button>
          </div>
        </div>

        <!-- Main preview area -->
        <div class="ws-preview">
          <div v-if="loading" class="ws-loading">加载中…</div>

          <!-- Edit mode -->
          <template v-else-if="editMode && activeFile">
            <div class="ws-edit-bar">
              <span style="font-size:12px;color:var(--ink-mute)">编辑 {{ activeFile.name }}</span>
              <button class="btn primary" :disabled="saving" @click="saveEdit" style="padding: 4px 12px; font-size: 12px">
                {{ saving ? "保存中…" : "保存" }}
              </button>
              <button class="btn" @click="editMode = false" style="padding: 4px 12px; font-size: 12px">取消</button>
            </div>
            <textarea class="ws-editor" v-model="editContent" spellcheck="false"></textarea>
          </template>

          <!-- File previews -->
          <template v-else-if="activeFile">
            <!-- Markdown -->
            <div
              v-if="fileMode(activeFile) === 'md'"
              class="md-preview"
              v-html="mdHtml"
            />
            <!-- JSON -->
            <pre v-else-if="fileMode(activeFile) === 'json'" class="json-preview">{{ jsonPretty }}</pre>
            <!-- CSV table -->
            <div v-else-if="fileMode(activeFile) === 'csv'" class="csv-wrap">
              <table class="csv-preview">
                <thead><tr><th v-for="(c, i) in csvRows[0]" :key="i">{{ c }}</th></tr></thead>
                <tbody>
                  <tr v-for="(row, ri) in csvRows.slice(1)" :key="ri">
                    <td v-for="(c, ci) in row" :key="ci">{{ c }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <!-- HTML iframe -->
            <iframe
              v-else-if="fileMode(activeFile) === 'html'"
              class="html-preview"
              :srcdoc="previewVersion?.content ?? content"
              sandbox="allow-scripts"
            />
            <!-- Image -->
            <div v-else-if="fileMode(activeFile) === 'image'" class="img-preview">
              <img :src="rawUrl" :alt="activeFile.name" />
            </div>
            <!-- PDF -->
            <div v-else-if="fileMode(activeFile) === 'pdf'" class="pdf-preview">
              <iframe :src="rawUrl" frameborder="0" style="width:100%;height:calc(100% - 36px);border:none" />
              <div style="height:36px;display:flex;align-items:center;justify-content:center;border-top:1px solid var(--rule-soft);gap:8px">
                <a :href="rawUrl" target="_blank" class="btn" style="font-size:12px;padding:4px 12px">在新标签页打开</a>
              </div>
            </div>
            <!-- Diff -->
            <div v-else-if="fileMode(activeFile) === 'diff'" class="diff-preview">
              <div
                v-for="(line, i) in diffLines"
                :key="i"
                :class="['diff-line', line.cls]"
              >{{ line.text }}</div>
            </div>
            <!-- Code -->
            <pre
              v-else-if="fileMode(activeFile) === 'code'"
              class="code-preview"
              v-html="codeHighlighted"
            />
            <!-- Txt / fallback -->
            <pre v-else-if="fileMode(activeFile) === 'txt'" class="txt-preview">{{ previewVersion?.content ?? content }}</pre>
            <!-- Unknown -->
            <div v-else class="ws-unknown">
              <Icon name="doc" style="font-size: 40px; color: var(--ink-mute)" />
              <div style="font-size: 13px; color: var(--ink-mute); margin-top: 12px">{{ activeFile.name }}</div>
              <div style="font-size: 12px; color: var(--ink-faint); margin-top: 4px">{{ fmtSize(activeFile.size_bytes) }}</div>
              <button class="btn" style="margin-top: 16px" @click="download">下载文件</button>
            </div>
          </template>

          <div v-else class="ws-empty">选择一个文件预览</div>
        </div>
      </div>
    </div>
  </aside>
</template>

<style scoped>
.workspace {
  position: absolute; top: 0; right: 0; bottom: 0;
  width: clamp(420px, 52%, 720px);
  background: var(--bg-side);
  border-left: 1px solid var(--rule);
  display: flex; flex-direction: column;
  box-shadow: -20px 0 40px -20px rgba(29,26,20,0.18);
  z-index: 10;
  transition: width 200ms;
}
.workspace.fullscreen {
  position: fixed;
  inset: 0;
  width: 100% !important;
  z-index: 500;
}

.ws-head {
  display: flex; align-items: center;
  padding: 10px 14px;
  border-bottom: 1px solid var(--rule-soft);
  gap: 8px;
}
.ws-title {
  font-family: var(--font-serif);
  font-size: 15px; font-weight: 600;
  color: var(--ink);
}
.ws-count {
  font-size: 11px; color: var(--ink-mute);
  background: rgba(29,26,20,0.06);
  padding: 1px 7px; border-radius: 999px;
  margin-left: 6px;
  font-family: var(--font-sans);
}
.ws-actions { display: flex; align-items: center; gap: 4px; margin-left: auto; }
.ws-btn {
  padding: 3px 8px; border-radius: 6px;
  font-size: 11.5px; color: var(--ink-soft);
  display: flex; align-items: center; gap: 4px;
}
.ws-btn:hover { background: rgba(29,26,20,0.07); color: var(--ink); }
.ws-btn.active { background: var(--accent-tint); color: var(--accent-deep); }
.ws-x {
  width: 26px; height: 26px; border-radius: 6px;
  color: var(--ink-mute); font-size: 18px;
}
.ws-x:hover { background: rgba(29,26,20,0.06); color: var(--ink); }

/* Tabs */
.ws-tabs {
  display: flex; align-items: center;
  overflow-x: auto; flex-shrink: 0;
  border-bottom: 1px solid var(--rule-soft);
  background: var(--bg-canvas);
  scrollbar-width: none;
}
.ws-tabs::-webkit-scrollbar { display: none; }
.ws-tab {
  display: flex; align-items: center; gap: 5px;
  padding: 6px 10px;
  font-size: 12px; color: var(--ink-soft);
  white-space: nowrap;
  border-right: 1px solid var(--rule-soft);
  border-bottom: 2px solid transparent;
  flex-shrink: 0;
}
.ws-tab:hover { color: var(--ink); background: rgba(29,26,20,0.04); }
.ws-tab.active { color: var(--ink); border-bottom-color: var(--accent); background: var(--bg-side); }
.ws-tab-kind {
  font-size: 8px; font-weight: 700; text-transform: uppercase;
  background: var(--accent-tint); color: var(--accent-deep);
  border: 1px solid var(--accent-soft);
  border-radius: 3px; padding: 0 3px;
  font-family: var(--font-mono);
}
.ws-tab-badge {
  font-size: 8px; font-weight: 700;
  background: var(--ok); color: #fff;
  border-radius: 3px; padding: 0 3px;
}
.ws-tab-close {
  color: var(--ink-mute); font-size: 14px; line-height: 1;
  padding: 0 2px; border-radius: 3px;
}
.ws-tab-close:hover { background: rgba(29,26,20,0.1); color: var(--ink); }

/* Body layout */
.ws-body { display: flex; flex: 1; min-height: 0; }
.ws-tree {
  width: 164px; border-right: 1px solid var(--rule-soft);
  padding: 6px; overflow-y: auto; flex-shrink: 0;
}
.ws-file {
  display: flex; align-items: center; gap: 6px;
  width: 100%; padding: 7px 9px;
  border-radius: var(--r-sm);
  color: var(--ink-soft); font-size: 12px; text-align: left;
}
.ws-file:hover { background: rgba(29,26,20,0.05); color: var(--ink); }
.ws-file.active {
  background: var(--accent-tint); color: var(--ink);
  box-shadow: inset 0 0 0 1px var(--accent-soft);
}
.ws-file .kind {
  width: 26px; height: 22px; border-radius: 3px;
  background: var(--accent-tint); color: var(--accent-deep);
  border: 1px solid var(--accent-soft);
  display: grid; place-items: center;
  font-family: var(--font-mono); font-size: 8px;
  font-weight: 700; text-transform: uppercase; flex-shrink: 0;
}
.ws-file .nm {
  flex: 1; min-width: 0; white-space: nowrap;
  overflow: hidden; text-overflow: ellipsis;
}
.ws-file .ver { font-size: 9px; color: var(--ink-mute); font-family: var(--font-mono); }
.ws-new-badge {
  font-size: 8px; font-weight: 700;
  background: var(--ok); color: #fff;
  border-radius: 3px; padding: 0 3px;
}

.ws-content-wrap { display: flex; flex: 1; min-width: 0; overflow: hidden; }

/* Version history drawer */
.ws-versions {
  width: 200px; flex-shrink: 0;
  border-right: 1px solid var(--rule-soft);
  background: var(--bg-canvas);
  display: flex; flex-direction: column;
  overflow: hidden;
}
.ws-ver-head {
  display: flex; align-items: center; justify-content: space-between;
  padding: 10px 12px;
  font-size: 12px; font-weight: 600; color: var(--ink);
  border-bottom: 1px solid var(--rule-soft);
}
.ws-ver-list { flex: 1; overflow-y: auto; }
.ws-ver-item {
  padding: 8px 12px;
  border-bottom: 1px solid var(--rule-soft);
}
.ws-ver-item.preview { background: var(--accent-tint); }
.ws-ver-info { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.ws-ver-num { font-size: 11px; font-weight: 700; font-family: var(--font-mono); color: var(--ink); }
.ws-ver-date { font-size: 10.5px; color: var(--ink-mute); }
.ws-ver-size { font-size: 10px; color: var(--ink-faint); }
.ws-ver-btns { display: flex; gap: 4px; margin-top: 6px; }
.ws-ver-preview-banner {
  padding: 8px 12px;
  background: var(--accent-tint);
  font-size: 11.5px; color: var(--accent-deep);
  border-top: 1px solid var(--accent-soft);
  display: flex; align-items: center; gap: 8px;
}

/* Preview area */
.ws-preview {
  flex: 1; min-width: 0; overflow-y: auto;
  background: var(--bg-panel); padding: 22px;
}
.ws-empty, .ws-loading {
  display: grid; place-items: center;
  height: 100%; color: var(--ink-mute); font-size: 13px;
}
.ws-unknown {
  display: flex; flex-direction: column;
  align-items: center; justify-content: center;
  height: 100%;
}

/* Edit */
.ws-edit-bar {
  display: flex; align-items: center; gap: 8px;
  margin-bottom: 10px;
}
.ws-editor {
  width: 100%; height: calc(100% - 40px);
  resize: none; outline: none;
  border: 1px solid var(--rule);
  border-radius: var(--r-sm);
  padding: 14px 16px;
  font-family: var(--font-mono);
  font-size: 12.5px; line-height: 1.6;
  background: var(--bg-canvas);
  color: var(--ink);
  tab-size: 2;
}

/* Markdown */
.md-preview { max-width: 680px; margin: 0 auto; color: var(--ink); font-size: 13.5px; line-height: 1.7; }
.md-preview :deep(h1) { font-family: var(--font-serif); font-size: 24px; margin: 0 0 10px; }
.md-preview :deep(h2) { font-family: var(--font-serif); font-size: 18px; margin: 20px 0 8px; padding-bottom: 5px; border-bottom: 1px solid var(--rule); }
.md-preview :deep(h3) { font-size: 15px; margin: 14px 0 6px; }
.md-preview :deep(ul), .md-preview :deep(ol) { padding-left: 20px; }
.md-preview :deep(li) { margin-bottom: 4px; }
.md-preview :deep(blockquote) { border-left: 3px solid var(--accent); padding-left: 12px; color: var(--ink-mute); font-style: italic; margin: 0 0 12px; }
.md-preview :deep(table) { width: 100%; border-collapse: collapse; font-size: 12.5px; margin: 8px 0; }
.md-preview :deep(th) { text-align: left; padding: 7px 10px; border-bottom: 2px solid var(--ink); font-size: 11px; text-transform: uppercase; color: var(--ink-mute); }
.md-preview :deep(td) { padding: 7px 10px; border-bottom: 1px solid var(--rule-soft); color: var(--ink-soft); }
.md-preview :deep(code) { font-family: var(--font-mono); font-size: 0.88em; background: rgba(29,26,20,0.07); padding: 1.5px 5px; border-radius: 5px; color: var(--accent-deep); }
.md-preview :deep(pre) { background: #1d1a14; color: #f0ebde; border-radius: var(--r-sm); padding: 13px 15px; overflow-x: auto; }
.md-preview :deep(pre code) { background: none; color: inherit; padding: 0; }

/* Code highlight */
.code-preview {
  max-width: 100%; margin: 0;
  background: #1d1a14; color: #f0ebde;
  border-radius: var(--r-sm);
  padding: 16px 18px;
  font-family: var(--font-mono); font-size: 12.5px; line-height: 1.6;
  overflow-x: auto; white-space: pre; tab-size: 2;
}
.code-preview :deep(.hl-comment) { color: #7a7060; font-style: italic; }
.code-preview :deep(.hl-str) { color: #8aaf62; }
.code-preview :deep(.hl-kw) { color: #c89a4a; font-weight: 600; }
.code-preview :deep(.hl-num) { color: #7ab0d8; }

/* JSON / txt */
.json-preview, .txt-preview {
  max-width: 100%; margin: 0;
  background: #1d1a14; color: #f0ebde;
  border-radius: var(--r-sm); padding: 16px 18px;
  font-family: var(--font-mono); font-size: 12.5px; line-height: 1.6;
  overflow-x: auto; white-space: pre-wrap; word-break: break-word;
}

/* CSV */
.csv-wrap { overflow-x: auto; }
.csv-preview { width: 100%; max-width: 100%; border-collapse: collapse; font-size: 12.5px; }
.csv-preview th {
  text-align: left; padding: 8px 10px;
  border-bottom: 2px solid var(--ink);
  font-size: 10.5px; text-transform: uppercase;
  color: var(--ink-mute); background: rgba(29,26,20,0.02);
}
.csv-preview td { padding: 8px 10px; border-bottom: 1px solid var(--rule-soft); color: var(--ink-soft); }

/* HTML iframe */
.html-preview { width: 100%; height: 100%; border: none; border-radius: var(--r-sm); background: #fff; }

/* Image */
.img-preview { display: flex; justify-content: center; align-items: flex-start; }
.img-preview img { max-width: 100%; border-radius: var(--r-sm); box-shadow: 0 2px 12px rgba(0,0,0,0.12); }

/* PDF */
.pdf-preview { width: 100%; height: 100%; }

/* Diff */
.diff-preview {
  font-family: var(--font-mono); font-size: 12.5px; line-height: 1.5;
  background: #1d1a14; border-radius: var(--r-sm); padding: 16px 18px;
  overflow-x: auto;
}
.diff-line { padding: 0 4px; white-space: pre; }
.diff-add { background: rgba(80, 160, 80, 0.2); color: #7fcf7f; }
.diff-del { background: rgba(180, 60, 60, 0.2); color: #cf7f7f; }
.diff-hunk { color: #7ab0d8; font-style: italic; }
.diff-ctx { color: #9a9080; }
</style>
