import { defineStore } from "pinia";
import { computed, ref } from "vue";
import { conversationsApi } from "@/api/conversations";
import { agentsApi } from "@/api/agents";
import { teamsApi } from "@/api/teams";
import { tokenStore } from "@/api/client";
import { useStream } from "@/composables/useStream";
import type { Agent, Conversation, Message, Team, WorkspaceFile, ConfirmationRequest } from "@/types";
import type { Profile } from "@/api/agents";

const API_BASE = import.meta.env.VITE_API_BASE || "/api/v1";

export const useChatStore = defineStore("chat", () => {
  const conversations = ref<Conversation[]>([]);
  const agents = ref<Agent[]>([]);
  const profiles = ref<Profile[]>([]);
  const teams = ref<Team[]>([]);
  const activeId = ref<string | null>(null);
  const activeAgents = ref<string[]>(["hermes"]);
  const messages = ref<Message[]>([]);
  const files = ref<WorkspaceFile[]>([]);
  const streamingConvoId = ref<string | null>(null);
  const streaming = computed(() => streamingConvoId.value !== null);
  const loading = ref(false);
  const pendingConfirmations = ref<ConfirmationRequest[]>([]);
  const hasMoreMessages = ref(true);
  const loadingOlder = ref(false);

  // ── Stream composable ──
  const stream = useStream();

  async function loadTeams() {
    try {
      teams.value = await teamsApi.list();
    } catch {
      teams.value = [];
    }
  }

  async function loadAgents() {
    try {
      agents.value = await agentsApi.list();
    } catch {
      agents.value = [];
    }
  }

  async function loadProfiles() {
    try {
      profiles.value = await agentsApi.profiles();
    } catch {
      profiles.value = [];
    }
  }

  async function loadConversations() {
    conversations.value = await conversationsApi.list();
  }

  function closeStream() {
    stream.close();
    stream.offAll();
  }

  async function openConversation(id: string) {
    closeStream();
    activeId.value = id;
    loading.value = true;
    hasMoreMessages.value = true;
    try {
      const detail = await conversationsApi.get(id);
      // Map content.tool_calls to steps for persisted messages
      messages.value = detail.messages.map((m: Message) => ({
        ...m,
        steps: m.content?.tool_calls as { title: string; status: string }[] | undefined,
      }));
      hasMoreMessages.value = detail.messages.length >= 50;
      activeAgents.value = detail.active_agent_ids || ["hermes"];
      files.value = await conversationsApi.files(id);

      // Reconnect SSE if conversation has a streaming message
      const streamingMsg = messages.value.find((m) => m.status === "streaming");
      if (streamingMsg) {
        streamingConvoId.value = id;
        registerStreamHandlers();
        const token = tokenStore.access;
        const url = `${API_BASE}/conversations/${id}/stream?access_token=${encodeURIComponent(token || "")}`;
        await stream.openSSE(url);
      }
    } finally {
      loading.value = false;
    }
  }

  async function loadMoreMessages() {
    if (!activeId.value || loadingOlder.value || !hasMoreMessages.value) return;
    loadingOlder.value = true;
    try {
      const oldest = messages.value[0];
      if (!oldest) { hasMoreMessages.value = false; return; }
      const older = await conversationsApi.getMessages(activeId.value, {
        limit: 50,
        before: oldest.id,
      });
      if (older.length === 0) {
        hasMoreMessages.value = false;
      } else {
        messages.value = [...older, ...messages.value];
        if (older.length < 50) hasMoreMessages.value = false;
      }
    } catch {
      // silently fail
    } finally {
      loadingOlder.value = false;
    }
  }

  async function newConversation(agentId = "hermes"): Promise<string> {
    closeStream();
    const detail = await conversationsApi.create({ primary_agent_id: agentId });
    conversations.value.unshift(detail);
    activeId.value = detail.id;
    activeAgents.value = detail.active_agent_ids || [agentId];
    messages.value = [];
    files.value = [];
    return detail.id;
  }

  async function toggleAgent(agentId: string) {
    if (!activeId.value) return;
    let next = [...activeAgents.value];
    if (next.includes(agentId)) {
      if (agentId === "hermes") return;
      next = next.filter((a) => a !== agentId);
    } else {
      next.push(agentId);
    }
    if (!next.includes("hermes")) next.unshift("hermes");
    const convo = await conversationsApi.setAgents(activeId.value, next);
    activeAgents.value = convo.active_agent_ids;
  }

  const find = (id: string) => messages.value.find((x) => x.id === id);

  function refreshAfterTurn() {
    streamingConvoId.value = null;
    closeStream();
    if (activeId.value) {
      conversationsApi.files(activeId.value).then((f) => (files.value = f)).catch(() => {});
    }
    loadConversations().catch(() => {});
  }

  // ── Stream event handlers (4.1 message flow pattern) ──

  function registerStreamHandlers() {
    stream.offAll();

    stream.on("token", (ev) => {
      const m = find(ev.message_id);
      if (m) m.content = { ...m.content, text: (m.content.text || "") + ev.delta };
    });

    // Debounced refresh: intermediate "done" events (multi-message split) are followed
    // by a "start" event within ~100ms. Delay refresh to avoid killing the SSE stream.
    let refreshTimer: ReturnType<typeof setTimeout> | null = null;
    const scheduleRefresh = () => {
      if (refreshTimer) clearTimeout(refreshTimer);
      refreshTimer = setTimeout(() => {
        refreshTimer = null;
        refreshAfterTurn();
      }, 500);
    };
    const cancelRefresh = () => {
      if (refreshTimer) { clearTimeout(refreshTimer); refreshTimer = null; }
    };

    stream.on("start", (ev) => {
      cancelRefresh();  // New message coming, don't close stream
      if (!find(ev.message_id)) {
        messages.value.push({
          id: ev.message_id,
          conversation_id: activeId.value || "",
          owner_id: null,
          role: "agent",
          agent_id: "hermes",
          content: { text: "" },
          status: "streaming",
          created_at: new Date().toISOString(),
        });
      }
    });

    stream.on("rt_start", (ev) => {
      if (!find(ev.message_id)) {
        const replies = [...ev.agents]
          .sort((a, b) => a.slot - b.slot)
          .map((a) => ({ agent_id: a.agent_id, text: "", status: "streaming" as const }));
        messages.value.push({
          id: ev.message_id,
          conversation_id: activeId.value || "",
          owner_id: null,
          role: "roundtable",
          agent_id: replies[0]?.agent_id || null,
          content: { text: "", replies, merged: { text: "", status: "pending" } },
          status: "streaming",
          created_at: new Date().toISOString(),
        });
      }
    });

    stream.on("rt_token", (ev) => {
      const r = find(ev.message_id)?.content.replies?.[ev.slot];
      if (r) r.text += ev.delta;
    });

    stream.on("rt_reply_done", (ev) => {
      const r = find(ev.message_id)?.content.replies?.[ev.slot];
      if (r) r.status = "complete";
    });

    stream.on("merge_start", (ev) => {
      const merged = find(ev.message_id)?.content.merged;
      if (merged) merged.status = "streaming";
    });

    stream.on("merge_token", (ev) => {
      const merged = find(ev.message_id)?.content.merged;
      if (merged) merged.text += ev.delta;
    });

    stream.on("tool_call", (ev) => {
      const m = find(ev.message_id);
      if (m) {
        if (!m.steps) m.steps = [];
        const existing = m.steps.find((s) => s.title === ev.title);
        if (existing) existing.status = ev.status || existing.status;
        else m.steps.push({ title: ev.title || "调用工具", status: ev.status || "running" });
      }
    });

    stream.on("file", () => {
      if (activeId.value) {
        conversationsApi.files(activeId.value).then((f) => (files.value = f)).catch(() => {});
      }
    });

    stream.on("confirmation_request", (ev) => {
      pendingConfirmations.value.push(ev.request);
    });

    stream.on("confirmation_response", (ev) => {
      pendingConfirmations.value = pendingConfirmations.value.filter(
        (r) => r.id !== ev.request_id,
      );
    });

    stream.on("done", (ev) => {
      const m = find(ev.message_id);
      if (m) {
        m.status = (ev.status as Message["status"]) || "complete";
        if (ev.text !== undefined) m.content = { ...m.content, text: ev.text };
        if (m.content.merged && m.content.merged.status === "streaming") {
          m.content.merged.status = "complete";
        }
      }
      scheduleRefresh();  // Delayed: cancel if a new "start" event follows
    });

    stream.on("error", (ev) => {
      const m = find(ev.message_id);
      if (m) m.status = "error";
      scheduleRefresh();
    });
  }

  async function send(
    text: string,
    agentId = "hermes",
    opts?: { profileId?: string; webSearch?: boolean; deepThink?: boolean; stagedFiles?: File[] },
  ) {
    if (!activeId.value) await newConversation(agentId);
    const id = activeId.value!;

    let fileIds: string[] = [];
    if (opts?.stagedFiles?.length) {
      try {
        const uploaded = await Promise.all(opts.stagedFiles.map((f) => conversationsApi.upload(id, f)));
        fileIds = uploaded.map((r) => r.id);
        files.value = [...files.value, ...uploaded];
      } catch { /* upload failure is non-fatal */ }
    }

    const passOpts = { profileId: opts?.profileId, webSearch: opts?.webSearch, deepThink: opts?.deepThink, fileIds };
    if (activeAgents.value.length > 1) await sendRoundtable(id, text, passOpts);
    else await sendSingle(id, text, passOpts);
  }

  function isActivelyStreaming(id: string) {
    return streamingConvoId.value === id;
  }

  /** Single agent: open SSE, register handlers, then POST. */
  async function sendSingle(id: string, text: string, opts?: { profileId?: string; webSearch?: boolean; deepThink?: boolean; fileIds?: string[] }) {
    closeStream();
    streamingConvoId.value = id;
    registerStreamHandlers();

    const token = tokenStore.access;
    const url = `${API_BASE}/conversations/${id}/stream?access_token=${encodeURIComponent(token || "")}`;
    await stream.openSSE(url);

    const res = await conversationsApi.send(id, text, opts);
    messages.value.push(res.user_message, res.agent_message);
  }

  /** Roundtable: bidirectional WebSocket — send + stream over one socket. */
  async function sendRoundtable(id: string, text: string, opts?: { profileId?: string; webSearch?: boolean; deepThink?: boolean; fileIds?: string[] }) {
    closeStream();
    streamingConvoId.value = id;
    registerStreamHandlers();

    const token = tokenStore.access;
    const wsBase = API_BASE.startsWith("http")
      ? API_BASE.replace(/^http/, "ws")
      : `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}${API_BASE}`;
    const url = `${wsBase}/conversations/${id}/ws?access_token=${encodeURIComponent(token || "")}`;
    await stream.openWS(url);

    // Optimistic user bubble
    messages.value.push({
      id: `tmp-${Date.now()}`,
      conversation_id: id,
      owner_id: null,
      role: "user",
      agent_id: null,
      content: { text },
      status: "complete",
      created_at: new Date().toISOString(),
    });

    const { fileIds, ...restOpts } = opts || {};
    stream.send({ action: "send", text, ...restOpts, attached_file_ids: fileIds || [] });
  }

  async function cancel() {
    if (activeId.value) await conversationsApi.cancel(activeId.value).catch(() => {});
  }

  async function respondConfirmation(requestId: string, choice: string) {
    if (!activeId.value) return;
    const id = activeId.value;
    pendingConfirmations.value = pendingConfirmations.value.filter((r) => r.id !== requestId);
    // Tell the runner we responded (so it can unblock and continue the conversation)
    try { await conversationsApi.confirm(id, requestId, choice); } catch { /* ok */ }
    // Runner handles the follow-up turn internally — no need to sendSingle here
  }

  async function newConversationWithProfile(profileId: string): Promise<string> {
    closeStream();
    const detail = await conversationsApi.create({ primary_agent_id: "hermes", profile_id: profileId });
    conversations.value.unshift(detail);
    activeId.value = detail.id;
    activeAgents.value = detail.active_agent_ids || ["hermes"];
    messages.value = [];
    files.value = [];
    return detail.id;
  }

  async function deleteConversation(id: string) {
    await conversationsApi.remove(id);
    conversations.value = conversations.value.filter((c) => c.id !== id);
    if (activeId.value === id) {
      activeId.value = null;
      messages.value = [];
      files.value = [];
      closeStream();
    }
  }

  function landing() {
    closeStream();
    activeId.value = null;
    messages.value = [];
    files.value = [];
    activeAgents.value = ["hermes"];
  }

  return {
    conversations,
    agents,
    profiles,
    teams,
    activeId,
    activeAgents,
    messages,
    files,
    streaming,
    loading,
    pendingConfirmations,
    hasMoreMessages,
    loadingOlder,
    // Stream state (read-only exposure)
    streamConnected: stream.connected,
    streamError: stream.error,
    loadTeams,
    loadAgents,
    loadProfiles,
    loadConversations,
    openConversation,
    loadMoreMessages,
    newConversation,
    newConversationWithProfile,
    landing,
    toggleAgent,
    send,
    cancel,
    deleteConversation,
    respondConfirmation,
    isActivelyStreaming,
  };
});
