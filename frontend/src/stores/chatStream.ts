/**
 * chatStream — stream event handlers extracted from chat store.
 *
 * Handles SSE/WebSocket event processing for conversations.
 */
import { conversationsApi } from "@/api/conversations";
import { useNotificationStore } from "@/stores/notifications";
import type { ConfirmationRequest, Message, StreamEvent } from "@/types";
import type { Ref } from "vue";

interface StreamHandlersDeps {
  activeId: Ref<string | null>;
  messages: Ref<Message[]>;
  conversations: Ref<{ id: string; title: string }[]>;
  pendingConfirmations: Ref<ConfirmationRequest[]>;
  contextTokens: Ref<number>;
  contextSize: Ref<number>;
  files: Ref<{ id: string }[]>;
  find: (id: string) => Message | undefined;
  refreshAfterTurn: () => void;
}

/** Drop events that belong to another conversation. */
function scoped<T extends StreamEvent>(
  fn: (ev: T) => void,
  activeId: Ref<string | null>,
): (ev: T) => void {
  return (ev) => {
    if (ev.conversation_id && ev.conversation_id !== activeId.value) return;
    fn(ev);
  };
}

export function registerStreamHandlers(
  stream: {
    on: <T extends StreamEvent["type"]>(
      type: T,
      handler: (ev: Extract<StreamEvent, { type: T }>) => void,
    ) => () => void;
    offAll: () => void;
  },
  deps: StreamHandlersDeps,
) {
  const { activeId, messages, conversations, pendingConfirmations, contextTokens, contextSize, files, find, refreshAfterTurn } = deps;

  stream.offAll();

  stream.on("token", scoped((ev) => {
    const m = find(ev.message_id);
    if (m && m.status === "streaming") m.content = { ...m.content, text: (m.content.text || "") + ev.delta };
  }, activeId));

  // Debounced refresh
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

  stream.on("start", scoped((ev) => {
    cancelRefresh();
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
  }, activeId));

  stream.on("rt_start", scoped((ev) => {
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
  }, activeId));

  stream.on("rt_token", scoped((ev) => {
    const r = find(ev.message_id)?.content.replies?.[ev.slot];
    if (r && r.status === "streaming") r.text += ev.delta;
  }, activeId));

  stream.on("rt_reply_done", scoped((ev) => {
    const r = find(ev.message_id)?.content.replies?.[ev.slot];
    if (r) r.status = ev.status || "complete";
  }, activeId));

  stream.on("merge_start", scoped((ev) => {
    const merged = find(ev.message_id)?.content.merged;
    if (merged) merged.status = "streaming";
  }, activeId));

  stream.on("merge_token", scoped((ev) => {
    const merged = find(ev.message_id)?.content.merged;
    if (merged && merged.status === "streaming") merged.text += ev.delta;
  }, activeId));

  stream.on("tool_call", scoped((ev) => {
    const m = find(ev.message_id);
    if (m) {
      if (!m.steps) m.steps = [];
      const existing = m.steps.find((s) => s.title === ev.title);
      if (existing) existing.status = ev.status || existing.status;
      else m.steps.push({ title: ev.title || "调用工具", status: ev.status || "running" });
    }
  }, activeId));

  stream.on("thought", scoped((ev) => {
    const m = find(ev.message_id);
    if (m && m.status === "streaming") m.thinking = (m.thinking || "") + ev.delta;
  }, activeId));

  stream.on("plan", scoped((ev) => {
    const m = find(ev.message_id);
    if (m) m.plan = ev.entries;
  }, activeId));

  stream.on("usage", scoped((ev) => {
    const m = find(ev.message_id);
    const usage: Record<string, number> = {};
    if (ev.input_tokens != null) usage.input_tokens = ev.input_tokens;
    if (ev.output_tokens != null) usage.output_tokens = ev.output_tokens;
    if (ev.context_size != null) usage.context_size = ev.context_size;
    if (ev.context_used != null) usage.context_used = ev.context_used;
    if (m) m.usage = usage as any;
    if (ev.context_size) {
      contextSize.value = ev.context_size;
      contextTokens.value = ev.context_used || 0;
    } else {
      contextTokens.value = (ev.input_tokens || 0) + (ev.output_tokens || 0);
    }
  }, activeId));

  stream.on("session_info", scoped((ev) => {
    if (ev.title) {
      const c = conversations.value.find((c) => c.id === activeId.value);
      if (c && c.title === "新会话") c.title = ev.title;
    }
  }, activeId));

  stream.on("file", scoped((ev) => {
    const m = find(ev.message_id);
    if (m) {
      if (!m.content.files) m.content = { ...m.content, files: [] };
      const existing = m.content.files!.find((f) => f.id === ev.file_id);
      if (!existing) {
        m.content.files!.push({ id: ev.file_id, name: ev.name, kind: ev.kind, diff: ev.diff });
      }
    }
    if (activeId.value) {
      conversationsApi.files(activeId.value).then((f) => (files.value = f)).catch(() => {});
    }
  }, activeId));

  stream.on("confirmation_request", scoped((ev) => {
    if (pendingConfirmations.value.find((r) => r.id === ev.request.id)) return;
    pendingConfirmations.value.push(ev.request);
    const ns = useNotificationStore();
    ns.push({ title: "需要确认", body: ev.request.question || "AI 需要你的确认", kind: "warn" });
    if (document.hidden && "Notification" in window && Notification.permission === "granted") {
      new Notification("Hermes · 需要确认", { body: ev.request.question || "AI 需要你的确认", tag: "hermes-confirm" });
    }
  }, activeId));

  stream.on("confirmation_response", scoped((ev) => {
    pendingConfirmations.value = pendingConfirmations.value.filter(
      (r) => r.id !== ev.request_id,
    );
  }, activeId));

  stream.on("clarify_auto", scoped((ev) => {
    const ns = useNotificationStore();
    ns.push({ title: "已自动确认", body: `${ev.question} → ${ev.choice}`, kind: "info" });
  }, activeId));

  stream.on("done", scoped((ev) => {
    const m = find(ev.message_id);
    if (m) {
      m.status = (ev.status as Message["status"]) || "complete";
      if (ev.text !== undefined) m.content = { ...m.content, text: ev.text };
      if (m.content.merged && m.content.merged.status === "streaming") {
        m.content.merged.status = "complete";
      }
    }
    if (document.hidden || !activeId.value) {
      const ns = useNotificationStore();
      const text = (ev.text || m?.content?.text || "").slice(0, 80);
      ns.push({ title: "AI 回复完成", body: text || "点击查看", kind: "success", link: `/?c=${activeId.value}` });
      if (document.hidden && "Notification" in window && Notification.permission === "granted") {
        new Notification("Hermes · AI 回复完成", { body: text || "点击查看", tag: "hermes-done" });
      }
    }
    scheduleRefresh();
  }, activeId));

  stream.on("error", scoped((ev) => {
    const m = find(ev.message_id);
    if (m) m.status = "error";
    scheduleRefresh();
  }, activeId));
}
