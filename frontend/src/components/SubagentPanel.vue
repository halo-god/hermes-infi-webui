<script setup lang="ts">
/* Background subagent drawer — styled like MemberPanel's right-side drawer.
   Lists subagents spawned from this conversation; expanding one shows its
   transcript via the normal conversation-detail endpoint (its own headless
   Conversation), since the runner writes plain Message rows there. */
import { ref, onMounted, onUnmounted } from "vue";
import Icon from "@/components/Icon.vue";
import { subagentsApi, type Subagent } from "@/api/subagents";
import { conversationsApi } from "@/api/conversations";
import type { Message } from "@/types";

const props = defineProps<{ conversationId: string }>();
const emit = defineEmits<{ close: [] }>();

const subagents = ref<Subagent[]>([]);
const loading = ref(false);
const expandedId = ref<string | null>(null);
const transcript = ref<Message[]>([]);
const transcriptLoading = ref(false);
const purpose = ref("");
const initialPrompt = ref("");
const spawning = ref(false);
const followUpText = ref("");
const sending = ref(false);

const STATUS_LABEL: Record<string, string> = {
  starting: "启动中", running: "运行中", idle: "已完成",
  waiting_input: "等待输入", done: "已完成", error: "失败",
  stopped: "已停止", timeout: "超时", interrupted: "已中断",
};

async function load() {
  loading.value = true;
  try {
    subagents.value = await subagentsApi.list(props.conversationId);
  } catch { /* ignore */ } finally {
    loading.value = false;
  }
}

async function spawn() {
  if (!purpose.value.trim() || !initialPrompt.value.trim()) return;
  spawning.value = true;
  try {
    await subagentsApi.spawn(props.conversationId, {
      purpose: purpose.value.trim(),
      initial_prompt: initialPrompt.value.trim(),
    });
    purpose.value = "";
    initialPrompt.value = "";
    await load();
  } catch { /* ignore */ } finally {
    spawning.value = false;
  }
}

async function loadTranscript(s: Subagent) {
  transcriptLoading.value = true;
  try {
    const detail = await conversationsApi.get(s.subagent_conversation_id);
    transcript.value = detail.messages || [];
    await subagentsApi.markRead(props.conversationId, s.id);
    s.unread_count = 0;
  } catch {
    transcript.value = [];
  } finally {
    transcriptLoading.value = false;
  }
}

async function expand(s: Subagent) {
  if (expandedId.value === s.id) {
    expandedId.value = null;
    return;
  }
  expandedId.value = s.id;
  await loadTranscript(s);
}

async function sendFollowUp(s: Subagent) {
  if (!followUpText.value.trim()) return;
  sending.value = true;
  try {
    await subagentsApi.send(props.conversationId, s.id, followUpText.value.trim());
    followUpText.value = "";
  } catch { /* ignore */ } finally {
    sending.value = false;
  }
}

async function stop(s: Subagent) {
  if (!confirm(`停止后台任务「${s.purpose}」？`)) return;
  await subagentsApi.stop(props.conversationId, s.id);
  await load();
}

async function onNudge(e: Event) {
  const detail = (e as CustomEvent).detail as { subagent_id: string } | undefined;
  if (!detail) return;
  await load();
  if (expandedId.value === detail.subagent_id) {
    const s = subagents.value.find((x) => x.id === detail.subagent_id);
    if (s) await loadTranscript(s);
  }
}

onMounted(() => {
  load();
  window.addEventListener("hermes:subagent-nudge", onNudge);
});
onUnmounted(() => {
  window.removeEventListener("hermes:subagent-nudge", onNudge);
});
</script>

<template>
  <div class="sap-panel">
    <div class="sap-head">
      <Icon name="cube" :size="13" />
      <span style="flex:1;font-size:13px;font-weight:600;color:var(--ink)">后台任务</span>
      <button class="icon-btn" @click="emit('close')"><Icon name="x" :size="14" /></button>
    </div>

    <div class="sap-new">
      <input v-model="purpose" class="cfg-input" placeholder="任务目的（简短标题）" style="margin-bottom:6px" />
      <textarea v-model="initialPrompt" class="cfg-input" style="width:100%;min-height:60px;resize:vertical" placeholder="要求这个后台任务做什么"></textarea>
      <button class="btn primary" style="margin-top:6px;font-size:12px" :disabled="spawning || !purpose.trim() || !initialPrompt.trim()" @click="spawn">
        {{ spawning ? "创建中…" : "+ 新建后台任务" }}
      </button>
    </div>

    <div class="sap-list">
      <div v-if="loading" style="padding:14px;font-size:12px;color:var(--ink-mute)">加载中…</div>
      <div v-else-if="!subagents.length" style="padding:14px;font-size:12px;color:var(--ink-mute)">暂无后台任务</div>
      <div v-for="s in subagents" :key="s.id" class="sap-item">
        <div class="sap-item-head" @click="expand(s)">
          <span class="sap-st-pill" :class="s.status">{{ STATUS_LABEL[s.status] || s.status }}</span>
          <span style="flex:1;min-width:0;font-size:12.5px;color:var(--ink);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{{ s.purpose }}</span>
          <span v-if="s.unread_count > 0" class="sap-unread">{{ s.unread_count }}</span>
        </div>
        <div v-if="expandedId === s.id" class="sap-transcript">
          <div v-if="transcriptLoading" style="font-size:12px;color:var(--ink-mute)">加载中…</div>
          <template v-else>
            <div v-for="m in transcript" :key="m.id" class="sap-msg" :class="m.role">
              <div class="sap-msg-role">{{ m.role === 'user' ? '指令' : '回复' }}</div>
              <div class="sap-msg-text">{{ m.content?.text || '' }}</div>
            </div>
            <div v-if="s.error_detail" style="font-size:12px;color:var(--danger)">{{ s.error_detail }}</div>
          </template>
          <div v-if="!['done','error','stopped','timeout','interrupted'].includes(s.status)" style="display:flex;gap:6px;margin-top:8px">
            <input v-model="followUpText" class="cfg-input" placeholder="继续对话…" style="flex:1" @keydown.enter="sendFollowUp(s)" />
            <button class="btn" style="font-size:12px" :disabled="sending || !followUpText.trim()" @click="sendFollowUp(s)">发送</button>
          </div>
          <button class="btn text-danger" style="font-size:11.5px;margin-top:8px" @click="stop(s)">停止任务</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.sap-panel {
  position: absolute;
  top: 0; right: 0; bottom: 0;
  width: 320px;
  background: var(--bg-side);
  border-left: 1px solid var(--rule);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  box-shadow: -20px 0 40px -20px rgba(29,26,20,0.18);
  z-index: 10;
}
.sap-head {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 10px 12px;
  border-bottom: 1px solid var(--rule-soft);
  flex-shrink: 0;
}
.sap-new {
  padding: 10px 12px;
  border-bottom: 1px solid var(--rule-soft);
  flex-shrink: 0;
}
.sap-list {
  flex: 1;
  overflow-y: auto;
}
.sap-item {
  border-bottom: 1px solid var(--rule-soft);
}
.sap-item-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  cursor: pointer;
}
.sap-item-head:hover { background: color-mix(in srgb, var(--accent) 6%, transparent); }
.sap-st-pill {
  flex-shrink: 0;
  font-size: 10px;
  font-weight: 600;
  padding: 1px 6px;
  border-radius: 4px;
  border: 1px solid var(--rule);
  background: var(--bg-canvas);
  color: var(--ink-mute);
}
.sap-st-pill.running, .sap-st-pill.starting { border-color: var(--accent); color: var(--accent); }
.sap-st-pill.idle, .sap-st-pill.done { border-color: #3a7a4a; color: #3a7a4a; }
.sap-st-pill.error, .sap-st-pill.timeout, .sap-st-pill.interrupted { border-color: var(--danger); color: var(--danger); }
.sap-st-pill.stopped, .sap-st-pill.waiting_input { border-color: var(--ink-mute); color: var(--ink-mute); }
.sap-unread {
  flex-shrink: 0;
  min-width: 16px;
  height: 16px;
  padding: 0 4px;
  border-radius: 999px;
  background: var(--danger);
  color: #fff;
  font-size: 10px;
  line-height: 16px;
  text-align: center;
}
.sap-transcript {
  padding: 0 12px 12px;
}
.sap-msg {
  margin-bottom: 8px;
  padding: 6px 8px;
  border-radius: 6px;
  background: color-mix(in srgb, var(--ink) 4%, transparent);
}
.sap-msg-role {
  font-size: 10.5px;
  color: var(--ink-mute);
  margin-bottom: 2px;
}
.sap-msg-text {
  font-size: 12.5px;
  color: var(--ink);
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
