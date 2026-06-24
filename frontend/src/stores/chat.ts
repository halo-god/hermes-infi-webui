import { defineStore } from "pinia";
import { computed, ref } from "vue";
import { conversationsApi } from "@/api/conversations";
import { profilesApi } from "@/api/agents";
import { teamsApi } from "@/api/teams";
import { mediaTicket, tokenStore } from "@/api/client";
import { useStream } from "@/composables/useStream";
import { registerStreamHandlers } from "@/stores/chatStream";
import type { ClarifyEntry, Conversation, ConversationFolder, Message, Team, WorkspaceFile, ConfirmationRequest, PlanEntry } from "@/types";
import type { Profile } from "@/api/agents";

const API_BASE = import.meta.env.VITE_API_BASE || "/api/v1";

export const useChatStore = defineStore("chat", () => {
  const conversations = ref<Conversation[]>([]);
  const profiles = ref<Profile[]>([]);
  const teams = ref<Team[]>([]);
  const activeId = ref<string | null>(null);
  const activeAgents = ref<string[]>(["hermes"]);
  const activeProfiles = ref<Profile[]>([]);
  const messages = ref<Message[]>([]);
  const files = ref<WorkspaceFile[]>([]);
  const streamingConvoId = ref<string | null>(null);
  const streaming = computed(() => streamingConvoId.value !== null);
  const loading = ref(false);
  const contextTokens = ref(0);
  const contextSize = ref(0);
  const features = ref<{ followup_chips: boolean }>({ followup_chips: false });
  const pendingConfirmations = ref<ConfirmationRequest[]>([]);
  const hasMoreMessages = ref(true);
  const loadingOlder = ref(false);
  const CONVO_PAGE = 100;
  const hasMoreConversations = ref(true);
  const loadingMoreConvos = ref(false);
  const folders = ref<ConversationFolder[]>([]);
  // Group chat: persistent WS id + live typing indicators for the open group.
  const groupStreamId = ref<string | null>(null);
  const typingUsers = ref<{ user_id: string; name: string }[]>([]);

  // ── Stream composable ──
  const stream = useStream();

  async function loadTeams() {
    try {
      teams.value = await teamsApi.list();
    } catch (e) {
      console.error("[chat] loadTeams failed:", e);
      teams.value = [];
    }
  }

  /** Sync activeProfiles from activeAgents (backend truth) + profiles list. */
  function syncActiveProfiles() {
    activeProfiles.value = activeAgents.value
      .map((aid) => profiles.value.find((p) => p.default_agent_id === aid))
      .filter((p): p is Profile => !!p);
  }

  async function loadProfiles() {
    try {
      profiles.value = await profilesApi.list();
    } catch (e) {
      console.error("[chat] loadProfiles failed:", e);
      profiles.value = [];
    }
  }

  async function loadConfig() {
    try {
      const resp = await fetch("/api/v1/config");
      const data = await resp.json();
      features.value = data.features || { followup_chips: false };
    } catch (e) {
      console.error("[chat] loadConfig failed:", e);
    }
  }

  async function loadConversations() {
    // First page; pagination state reset. Append further pages via loadMore.
    try {
      const page = await conversationsApi.list({ limit: CONVO_PAGE, offset: 0 });
      conversations.value = page;
      hasMoreConversations.value = page.length >= CONVO_PAGE;
    } catch (e) {
      console.error("[chat] loadConversations failed:", e);
      // Don't clear existing conversations on transient errors
    }
  }

  async function loadMoreConversations() {
    if (loadingMoreConvos.value || !hasMoreConversations.value) return;
    loadingMoreConvos.value = true;
    try {
      const page = await conversationsApi.list({
        limit: CONVO_PAGE,
        offset: conversations.value.length,
      });
      // Dedupe by id — optimistic unshifts (new convos) can shift offsets.
      const seen = new Set(conversations.value.map((c) => c.id));
      conversations.value.push(...page.filter((c) => !seen.has(c.id)));
      hasMoreConversations.value = page.length >= CONVO_PAGE;
    } catch (e) {
      console.error("[chat] loadMoreConversations failed:", e);
    } finally {
      loadingMoreConvos.value = false;
    }
  }

  async function loadFolders() {
    try {
      folders.value = await conversationsApi.listFolders();
    } catch (e) {
      console.error("[chat] loadFolders failed:", e);
      folders.value = [];
    }
  }

  async function createFolder(name: string) {
    const f = await conversationsApi.createFolder(name);
    folders.value.push(f);
    return f;
  }

  async function renameFolder(id: string, name: string) {
    const f = await conversationsApi.updateFolder(id, { name });
    const i = folders.value.findIndex((x) => x.id === id);
    if (i >= 0) folders.value[i] = f;
    return f;
  }

  async function toggleFolderPinned(id: string, pinned: boolean) {
    // Optimistic update.
    const i = folders.value.findIndex((x) => x.id === id);
    if (i >= 0) folders.value[i] = { ...folders.value[i], pinned };
    try {
      await conversationsApi.updateFolder(id, { pinned });
    } catch (err) {
      // Revert on failure.
      if (i >= 0) folders.value[i] = { ...folders.value[i], pinned: !pinned };
      throw err;
    }
  }

  async function reorderFolders(items: { id: string; sort_order: number }[]) {
    // Optimistic update.
    const map = new Map(items.map((it) => [it.id, it.sort_order]));
    folders.value = folders.value.map((f) =>
      map.has(f.id) ? { ...f, sort_order: map.get(f.id)! } : f
    );
    try {
      await conversationsApi.reorderFolders(items);
    } catch (err) {
      // Reload from server on failure.
      await loadFolders();
      throw err;
    }
  }

  async function deleteFolder(id: string) {
    await conversationsApi.deleteFolder(id);
    folders.value = folders.value.filter((f) => f.id !== id);
    // Clear folder_id on local conversations (backend already SET NULL).
    for (const c of conversations.value) {
      if (c.folder_id === id) c.folder_id = null;
    }
  }

  /** Move a conversation into a folder (pass null to remove from any folder). */
  async function moveConversationToFolder(conversationId: string, folderId: string | null) {
    const updated = await conversationsApi.update(conversationId, { folder_id: folderId });
    const i = conversations.value.findIndex((c) => c.id === conversationId);
    if (i >= 0) conversations.value[i] = { ...conversations.value[i], ...updated };
    return updated;
  }

  function closeStream() {
    stream.close();
    stream.offAll();
    groupStreamId.value = null;
    typingUsers.value = [];
  }

  /** Open a persistent WebSocket for a group conversation (peer messages,
   *  typing, agent turns all flow over this one socket while it's open). */
  async function openGroupStream(id: string) {
    setupStreamHandlers();
    const wsBase = API_BASE.startsWith("http")
      ? API_BASE.replace(/^http/, "ws")
      : `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}${API_BASE}`;
    await stream.openWS(async () => {
      const ticket = await mediaTicket.ensure();
      return `${wsBase}/conversations/${id}/ws?ticket=${encodeURIComponent(ticket)}`;
    });
    groupStreamId.value = id;
  }

  /** Mark the open group as read and clear its local unread badge. */
  async function markRead(id: string) {
    try {
      await conversationsApi.read(id);
    } catch { /* non-fatal */ }
    const c = conversations.value.find((c) => c.id === id);
    if (c) { c.unread = 0; c.has_mention = false; }
  }

  /** Broadcast an ephemeral typing ping over the open group socket. */
  function sendTyping(name: string) {
    if (groupStreamId.value && groupStreamId.value === activeId.value) {
      stream.send({ action: "typing", name });
    }
  }

  async function openConversation(id: string) {
    closeStream();
    activeId.value = id;
    loading.value = true;
    hasMoreMessages.value = true;
    contextTokens.value = 0;
    contextSize.value = 0;
    pendingConfirmations.value = []; // modals belong to the previous conversation
    try {
      const detail = await conversationsApi.get(id);
      // Map content.tool_calls to steps for persisted messages
      messages.value = detail.messages.map((m: Message) => ({
        ...m,
        steps: m.content?.tool_calls as { title: string; status: string }[] | undefined,
        thinking: (m.content as Record<string, unknown>)?.thinking as string | undefined,
        plan: (m.content as Record<string, unknown>)?.plan as PlanEntry[] | undefined,
      }));
      hasMoreMessages.value = detail.messages.length >= 50;
      activeAgents.value = detail.active_agent_ids || ["hermes"];
      // Ensure the conversation is in the sidebar list (covers newly created convos)
      const idx = conversations.value.findIndex((c) => c.id === id);
      if (idx !== -1) {
        Object.assign(conversations.value[idx], detail);
      } else {
        conversations.value.unshift(detail);
      }
      syncActiveProfiles();
      files.value = await conversationsApi.files(id);

      // Group chat: open a persistent WS for realtime peer messages + typing,
      // and mark the channel read (clears its unread/@ badge).
      if (detail.type === "group") {
        await openGroupStream(id);
        markRead(id).catch(() => {});
        return;
      }

      // Reconnect SSE if conversation has a streaming message
      const streamingMsg = messages.value.find((m) => m.status === "streaming");
      if (streamingMsg) {
        // Restore the clarify modal lost on refresh — pending entries are
        // persisted in the streaming message's content by the runner.
        const clarifies = (streamingMsg.content as Record<string, unknown>).clarifies as ClarifyEntry[] | undefined;
        for (const c of clarifies || []) {
          if (c.status === "pending" && !pendingConfirmations.value.find((r) => r.id === c.id)) {
            pendingConfirmations.value.push({
              id: c.id,
              conversation_id: id,
              message_id: streamingMsg.id,
              question: c.question,
              options: c.options,
              questions: [{ question: c.question, options: c.options, allow_free_text: true }],
            });
          }
        }
        streamingConvoId.value = id;
        setupStreamHandlers();
        // Replay the in-flight turn from its start: the event stream is
        // durable, so tokens emitted while we were away are recovered.
        const sinceMs = Date.parse(streamingMsg.created_at);
        const since = Number.isFinite(sinceMs) ? `&since=${Math.max(sinceMs - 1, 0)}-0` : "";
        await stream.openSSE(async () => {
          const ticket = await mediaTicket.ensure();
          return `${API_BASE}/conversations/${id}/stream?ticket=${encodeURIComponent(ticket)}${since}`;
        });
      }
    } finally {
      loading.value = false;
    }
  }

  async function loadMoreMessages() {
    if (!activeId.value || loadingOlder.value || !hasMoreMessages.value) return;
    loadingOlder.value = true;
    try {
      // Skip optimistic (tmp-) messages — they don't have a real UUID yet and
      // the backend rejects non-UUID cursors with 422.
      const oldest = messages.value.find((m) => !m.id.startsWith("tmp-"));
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
    } catch (e) {
      console.error("[chat] loadMoreMessages failed:", e);
    } finally {
      loadingOlder.value = false;
    }
  }

  async function newConversation(agentId = "hermes", profileId?: string): Promise<string> {
    closeStream();
    const detail = await conversationsApi.create({ primary_agent_id: agentId, profile_id: profileId || undefined });
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
    syncActiveProfiles();
  }

  /** Toggle a profile into/out of the roundtable. Maps to agent_id for backend. */
  async function toggleProfile(profileId: string) {
    const profile = profiles.value.find((p) => p.id === profileId);
    if (!profile) return;
    await toggleAgent(profile.default_agent_id);
  }

  const find = (id: string) => messages.value.find((x) => x.id === id);

  function refreshAfterTurn() {
    streamingConvoId.value = null;
    // Keep a group's persistent WS open so peer messages keep flowing; only
    // tear down the per-turn SSE/WS of a personal conversation.
    if (!groupStreamId.value) closeStream();
    if (activeId.value) {
      conversationsApi.files(activeId.value).then((f) => (files.value = f)).catch(() => {});
    }
    loadConversations().catch(() => {});
  }

  // ── Stream event handlers (delegated to chatStream module) ──

  function setupStreamHandlers() {
    registerStreamHandlers(stream, {
      activeId,
      messages,
      conversations,
      pendingConfirmations,
      contextTokens,
      contextSize,
      files,
      typingUsers,
      find,
      refreshAfterTurn,
    });
  }

  async function send(
    text: string,
    agentId = "hermes",
    opts?: { profileId?: string; webSearch?: boolean; deepThink?: boolean; stagedFiles?: File[]; mentions?: string[]; replyToId?: string },
  ) {
    if (!activeId.value) await newConversation(agentId, opts?.profileId);
    const id = activeId.value!;

    let fileIds: string[] = [];
    if (opts?.stagedFiles?.length) {
      try {
        const uploaded = await Promise.all(opts.stagedFiles.map((f) => conversationsApi.upload(id, f)));
        fileIds = uploaded.map((r) => r.id);
        files.value = [...files.value, ...uploaded];
      } catch (e) {
        console.error("[chat] file upload failed:", e);
      }
    }

    // Check if this is a group conversation
    const activeConvo = conversations.value.find((c) => c.id === id);
    const isGroup = activeConvo?.type === "group";

    if (isGroup) {
      // Group chat: send over the persistent WS so peer/agent events + typing
      // all flow back over one socket. dispatch_group routes server-side.
      if (groupStreamId.value !== id) await openGroupStream(id);
      const mentions = opts?.mentions || [];
      // An agent reply is expected when an AI is @-ed, all-AI is requested, or
      // the channel auto-replies (always mode) without an @所有真人 broadcast.
      const expectAgent =
        mentions.some((m) => m === "__all_agents__" || (!m.startsWith("user:") && m !== "__all_humans__")) ||
        (activeConvo?.channel_mode === "always" && !mentions.includes("__all_humans__"));
      if (expectAgent) streamingConvoId.value = id;
      // Optimistic user bubble — reconciled by the `message` echo (same text).
      messages.value.push({
        id: `tmp-${crypto.randomUUID?.() ?? Math.random().toString(36).slice(2) + Date.now().toString(36)}`,
        conversation_id: id,
        owner_id: null,
        role: "user",
        agent_id: null,
        content: { text },
        status: "complete",
        created_at: new Date().toISOString(),
      });
      stream.send({
        action: "send",
        text,
        mentions,
        reply_to_id: opts?.replyToId,
        profileId: opts?.profileId,
        attached_file_ids: fileIds,
      });
    } else {
      // Personal conversation: existing logic
      const passOpts = { profileId: opts?.profileId, fileIds };
      if (activeAgents.value.length > 1) await sendRoundtable(id, text, passOpts);
      else await sendSingle(id, text, passOpts);
    }
  }

  function isActivelyStreaming(id: string) {
    return streamingConvoId.value === id;
  }

  /** Single agent: open SSE, register handlers, then POST. */
  async function sendSingle(id: string, text: string, opts?: { profileId?: string; fileIds?: string[] }) {
    closeStream();
    streamingConvoId.value = id;
    setupStreamHandlers();

    // Ensure access token is available (restore if needed)
    if (!tokenStore.access) {
      const restored = await tokenStore.restore();
      if (!restored) {
        console.error("[chat] Cannot send message: no access token");
        streamingConvoId.value = null;
        return;
      }
    }

    // Optimistic user bubble — push BEFORE opening SSE so the user message
    // always appears above the agent's streaming bubble, even if SSE "start"
    // arrives before the API response.
    const optimisticUser = {
      id: `tmp-${crypto.randomUUID?.() ?? Math.random().toString(36).slice(2) + Date.now().toString(36)}`,
      conversation_id: id,
      owner_id: null,
      role: "user" as const,
      agent_id: null,
      content: { text },
      status: "complete" as const,
      created_at: new Date().toISOString(),
    };
    messages.value.push(optimisticUser);

    // Anchor the stream BEFORE the POST. The runner publishes start/token/done
    // only after the prompt is enqueued (post-POST), so anything from this
    // anchor onward belongs to this turn. Passing `since` makes the SSE replay
    // durable: even if the EventSource connects late (openSSE resolves on its
    // timeout before the socket is truly open), the server replays from here
    // instead of capturing last_id past the already-published events — which
    // would otherwise leave the agent bubble blank while Redis holds the reply.
    const since = `&since=${Math.max(Date.now() - 1, 0)}-0`;
    console.debug(`[chat] sendSingle: opening SSE conv=${id} since=${since}`);
    await stream.openSSE(async () => {
      const ticket = await mediaTicket.ensure();
      return `${API_BASE}/conversations/${id}/stream?ticket=${encodeURIComponent(ticket)}${since}`;
    });
    console.debug(`[chat] sendSingle: SSE ${stream.connected.value ? "connected" : "pending"}, POSTing message`);

    const res = await conversationsApi.send(id, text, opts);
    // Replace the optimistic user message with the real one (server-assigned id)
    const optIdx = messages.value.findIndex((m) => m.id === optimisticUser.id);
    if (optIdx !== -1) messages.value.splice(optIdx, 1, res.user_message);
    // The SSE "start" event may have already created the agent bubble
    if (!find(res.agent_message.id)) messages.value.push(res.agent_message);
  }

  /** Roundtable: bidirectional WebSocket — send + stream over one socket. */
  async function sendRoundtable(id: string, text: string, opts?: { profileId?: string; fileIds?: string[] }) {
    closeStream();
    streamingConvoId.value = id;
    setupStreamHandlers();

    const wsBase = API_BASE.startsWith("http")
      ? API_BASE.replace(/^http/, "ws")
      : `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}${API_BASE}`;
    await stream.openWS(async () => {
      const ticket = await mediaTicket.ensure();
      return `${wsBase}/conversations/${id}/ws?ticket=${encodeURIComponent(ticket)}`;
    });

    // Optimistic user bubble
    messages.value.push({
      id: `tmp-${crypto.randomUUID?.() ?? Math.random().toString(36).slice(2) + Date.now().toString(36)}`,
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
    // Close the SSE/WS stream immediately so the UI stops showing "generating"
    // instead of waiting for the server to notice the disconnect.
    closeStream();
  }

  async function respondConfirmation(requestId: string, choice: string) {
    if (!activeId.value) return;
    const id = activeId.value;
    pendingConfirmations.value = pendingConfirmations.value.filter((r) => r.id !== requestId);
    // Tell the runner we responded (so it can unblock and continue the conversation)
    try {
      await conversationsApi.confirm(id, requestId, choice);
    } catch (e) {
      console.error("[chat] confirm response failed:", e);
    }
    // Runner handles the follow-up turn internally — no need to sendSingle here
  }

  async function newConversationWithProfile(profileId: string): Promise<string> {
    closeStream();
    const detail = await conversationsApi.create({ primary_agent_id: "hermes", profile_id: profileId });
    conversations.value.unshift(detail);
    activeId.value = detail.id;
    activeAgents.value = detail.active_agent_ids || ["hermes"];
    syncActiveProfiles();
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
    activeProfiles.value = [];
    pendingConfirmations.value = [];
  }

  return {
    conversations,
    profiles,
    teams,
    activeId,
    activeProfiles,
    messages,
    files,
    streaming,
    loading,
    pendingConfirmations,
    hasMoreMessages,
    loadingOlder,
    contextTokens,
    contextSize,
    streamingConvoId,
    features,
    loadConfig,
    // Stream state (read-only exposure)
    streamConnected: stream.connected,
    streamError: stream.error,
    loadTeams,
    loadProfiles,
    loadConversations,
    loadMoreConversations,
    hasMoreConversations,
    loadingMoreConvos,
    folders,
    loadFolders,
    createFolder,
    renameFolder,
    toggleFolderPinned,
    reorderFolders,
    deleteFolder,
    moveConversationToFolder,
    openConversation,
    loadMoreMessages,
    newConversation,
    newConversationWithProfile,
    landing,
    toggleAgent,
    toggleProfile,
    profileByAgentId: (agentId: string) => profiles.value.find((p) => p.default_agent_id === agentId),
    send,
    cancel,
    deleteConversation,
    respondConfirmation,
    isActivelyStreaming,
    typingUsers,
    groupStreamId,
    markRead,
    sendTyping,
  };
});
