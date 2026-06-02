import { defineStore } from "pinia";
import { ref } from "vue";
import { conversationsApi } from "@/api/conversations";
import { agentsApi } from "@/api/agents";
import { teamsApi } from "@/api/teams";
import { tokenStore } from "@/api/client";
import type { Agent, Conversation, Message, Team, WorkspaceFile, StreamEvent, ConfirmationRequest } from "@/types";
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
  const streaming = ref(false);
  const loading = ref(false);
  const pendingConfirmations = ref<ConfirmationRequest[]>([]);

  let es: EventSource | null = null;
  let ws: WebSocket | null = null;

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
    if (es) { es.close(); es = null; }
    if (ws) { ws.close(); ws = null; }
  }

  async function openConversation(id: string) {
    closeStream();
    activeId.value = id;
    loading.value = true;
    try {
      const detail = await conversationsApi.get(id);
      messages.value = detail.messages;
      activeAgents.value = detail.active_agent_ids || ["hermes"];
      files.value = await conversationsApi.files(id);
    } finally {
      loading.value = false;
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
      if (agentId === "hermes") return; // keep the lead
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
    streaming.value = false;
    closeStream();
    if (activeId.value) {
      conversationsApi.files(activeId.value).then((f) => (files.value = f)).catch(() => {});
    }
    loadConversations().catch(() => {});
  }

  function _apply(ev: StreamEvent) {
    switch (ev.type) {
      case "token": {
        const m = find(ev.message_id);
        if (m) m.content = { ...m.content, text: (m.content.text || "") + ev.delta };
        break;
      }
      case "rt_start": {
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
        break;
      }
      case "rt_token": {
        const r = find(ev.message_id)?.content.replies?.[ev.slot];
        if (r) r.text += ev.delta;
        break;
      }
      case "rt_reply_done": {
        const r = find(ev.message_id)?.content.replies?.[ev.slot];
        if (r) r.status = "complete";
        break;
      }
      case "merge_start": {
        const merged = find(ev.message_id)?.content.merged;
        if (merged) merged.status = "streaming";
        break;
      }
      case "merge_token": {
        const merged = find(ev.message_id)?.content.merged;
        if (merged) merged.text += ev.delta;
        break;
      }
      case "file": {
        if (activeId.value) {
          conversationsApi.files(activeId.value).then((f) => (files.value = f)).catch(() => {});
        }
        break;
      }
      case "confirmation_request": {
        pendingConfirmations.value.push(ev.request);
        break;
      }
      case "confirmation_response": {
        pendingConfirmations.value = pendingConfirmations.value.filter(
          (r) => r.id !== ev.request_id
        );
        break;
      }
      case "done": {
        const m = find(ev.message_id);
        if (m) {
          m.status = (ev.status as Message["status"]) || "complete";
          if (m.content.merged && m.content.merged.status === "streaming") {
            m.content.merged.status = "complete";
          }
        }
        refreshAfterTurn();
        break;
      }
      case "error": {
        const m = find(ev.message_id);
        if (m) m.status = "error";
        refreshAfterTurn();
        break;
      }
    }
  }

  async function send(
    text: string,
    agentId = "hermes",
    opts?: { profileId?: string; webSearch?: boolean; deepThink?: boolean; stagedFiles?: File[] },
  ) {
    if (!activeId.value) await newConversation(agentId);
    const id = activeId.value!;

    // Upload any staged files now that we have a conversation ID
    let fileIds: string[] = [];
    if (opts?.stagedFiles?.length) {
      try {
        const uploaded = await Promise.all(opts.stagedFiles.map((f) => conversationsApi.upload(id, f)));
        fileIds = uploaded.map((r) => r.id);
        files.value = [...files.value, ...uploaded];
      } catch {
        /* upload failure is non-fatal; send message without file ids */
      }
    }

    const passOpts = { profileId: opts?.profileId, webSearch: opts?.webSearch, deepThink: opts?.deepThink, fileIds };
    if (activeAgents.value.length > 1) await sendRoundtable(id, text, passOpts);
    else await sendSingle(id, text, passOpts);
  }

  /** Single agent: open SSE, wait until subscribed, then POST (no missed tokens). */
  async function sendSingle(id: string, text: string, opts?: { profileId?: string; webSearch?: boolean; deepThink?: boolean; fileIds?: string[] }) {
    closeStream();
    streaming.value = true;
    const token = tokenStore.access;
    const url = `${API_BASE}/conversations/${id}/stream?access_token=${encodeURIComponent(token || "")}`;
    es = new EventSource(url);
    es.onmessage = (e) => {
      try { _apply(JSON.parse(e.data) as StreamEvent); } catch { /* heartbeat */ }
    };
    await new Promise<void>((resolve) => {
      es!.onopen = () => resolve();
      setTimeout(resolve, 600);
    });
    const res = await conversationsApi.send(id, text, opts);
    messages.value.push(res.user_message, res.agent_message);
  }

  /** Roundtable: bidirectional WebSocket — send + stream over one socket. */
  async function sendRoundtable(id: string, text: string, opts?: { profileId?: string; webSearch?: boolean; deepThink?: boolean; fileIds?: string[] }) {
    closeStream();
    streaming.value = true;
    const token = tokenStore.access;
    const wsBase = API_BASE.startsWith("http")
      ? API_BASE.replace(/^http/, "ws")
      : `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}${API_BASE}`;
    const url = `${wsBase}/conversations/${id}/ws?access_token=${encodeURIComponent(token || "")}`;
    ws = new WebSocket(url);
    ws.onmessage = (e) => {
      try { _apply(JSON.parse(e.data) as StreamEvent); } catch { /* noop */ }
    };
    ws.onclose = () => { ws = null; };
    await new Promise<void>((resolve) => {
      ws!.onopen = () => resolve();
      setTimeout(resolve, 800);
    });
    // optimistic user bubble (server persists the real one)
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
    ws?.send(JSON.stringify({ action: "send", text, ...restOpts, attached_file_ids: fileIds || [] }));
  }

  async function cancel() {
    if (activeId.value) await conversationsApi.cancel(activeId.value).catch(() => {});
  }

  async function respondConfirmation(requestId: string, choice: string) {
    if (!activeId.value) return;
    pendingConfirmations.value = pendingConfirmations.value.filter((r) => r.id !== requestId);
    await conversationsApi.confirm(activeId.value, requestId, choice).catch(() => {});
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
    loadTeams,
    loadAgents,
    loadProfiles,
    loadConversations,
    openConversation,
    newConversation,
    newConversationWithProfile,
    landing,
    toggleAgent,
    send,
    cancel,
    deleteConversation,
    respondConfirmation,
  };
});
