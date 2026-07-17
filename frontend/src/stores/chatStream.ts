/**
 * chatStream — stream event handlers extracted from chat store.
 *
 * Handles SSE/WebSocket event processing for conversations.
 */
import { conversationsApi } from "@/api/conversations";
import { useNotificationStore } from "@/stores/notifications";
import { useBrandingStore } from "@/stores/branding";
import { getActivePinia } from "pinia";
import i18n from "@/i18n";
import type { ConfirmationRequest, Message, StreamEvent } from "@/types";
import type { Ref } from "vue";

/** Branding short-name for desktop-notification prefixes. Resolved lazily via
 *  the active Pinia instance so this works outside a component setup context. */
function brandPrefix(): string {
  try {
    const pinia = getActivePinia();
    if (!pinia) return "";
    const branding = useBrandingStore();
    return (branding.shortName || "") + " · ";
  } catch {
    return "";
  }
}

interface StreamHandlersDeps {
  activeId: Ref<string | null>;
  messages: Ref<Message[]>;
  conversations: Ref<{ id: string; title: string }[]>;
  pendingConfirmations: Ref<ConfirmationRequest[]>;
  contextTokens: Ref<number>;
  contextSize: Ref<number>;
  files: Ref<{ id: string }[]>;
  typingUsers: Ref<{ user_id: string; name: string }[]>;
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
  const { activeId, messages, conversations, pendingConfirmations, contextTokens, contextSize, files, typingUsers, find, refreshAfterTurn } = deps;

  stream.offAll();

  // ── Group chat: realtime peer messages, edits, typing, membership ──
  stream.on("message", scoped((ev) => {
    const incoming = ev.message;
    if (!incoming) return;
    if (find(incoming.id)) return; // already have it (echo dedupe)
    // Reconcile the sender's optimistic bubble (temp id, same text) if present.
    if (incoming.role === "user") {
      const idx = messages.value.findIndex(
        (m) => m.id.startsWith("tmp-") && m.role === "user" &&
          (m.content?.text || "") === (incoming.content?.text || ""),
      );
      if (idx !== -1) { messages.value.splice(idx, 1, incoming); return; }
    }
    messages.value.push(incoming);
  }, activeId));

  stream.on("message_update", scoped((ev) => {
    const m = find(ev.message_id);
    if (!m) return;
    const p = ev.patch || {};
    if (p.content !== undefined) m.content = { ...m.content, ...p.content };
    if (p.edited_at !== undefined) m.edited_at = p.edited_at;
    if (p.deleted_at !== undefined) m.deleted_at = p.deleted_at;
    if (p.reactions !== undefined) m.reactions = p.reactions;
  }, activeId));

  // Typing indicators expire after 4s; refresh the timer on each ping.
  const typingTimers = new Map<string, ReturnType<typeof setTimeout>>();
  stream.on("typing", scoped((ev) => {
    if (!ev.user_id) return;
    const existing = typingUsers.value.find((u) => u.user_id === ev.user_id);
    if (existing) existing.name = ev.name || existing.name;
    else typingUsers.value.push({ user_id: ev.user_id, name: ev.name || "" });
    const prev = typingTimers.get(ev.user_id);
    if (prev) clearTimeout(prev);
    typingTimers.set(ev.user_id, setTimeout(() => {
      typingUsers.value = typingUsers.value.filter((u) => u.user_id !== ev.user_id);
      typingTimers.delete(ev.user_id);
    }, 4000));
  }, activeId));

  stream.on("members_changed", scoped((ev) => {
    window.dispatchEvent(new CustomEvent("hermes:members-changed", {
      detail: { conversation_id: ev.conversation_id },
    }));
  }, activeId));

  stream.on("subagent_nudge", scoped((ev) => {
    window.dispatchEvent(new CustomEvent("hermes:subagent-nudge", {
      detail: { subagent_id: ev.subagent_id, status: ev.status },
    }));
    const failed = ev.status === "error" || ev.status === "timeout";
    const ns = useNotificationStore();
    ns.push({
      title: failed ? "后台任务失败" : "后台任务已完成",
      body: failed ? "点击查看详情" : "有新的回复等待查看",
      kind: failed ? "warn" : "success",
    });
  }, activeId));

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
        agent_id: ev.agent_id || "hermes",
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
        .map((a) => ({ agent_id: a.agent_id, profile_id: a.profile_id ?? null, text: "", status: "streaming" as const }));
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

  const t = i18n.global.t;

  stream.on("tool_call", scoped((ev) => {
    const m = find(ev.message_id);
    if (m) {
      if (!m.steps) m.steps = [];
      const existing = m.steps.find((s) => s.title === ev.title);
      if (existing) existing.status = ev.status || existing.status;
      else m.steps.push({ title: ev.title || t("stream.toolCall"), status: ev.status || "running" });
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
    if (m) {
      m.usage = {
        input_tokens: ev.input_tokens || 0,
        output_tokens: ev.output_tokens || 0,
        context_size: ev.context_size,
        context_used: ev.context_used,
      };
    }
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
      if (c && c.title === t("stream.newConversation")) c.title = ev.title;
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
    ns.push({ title: t("stream.needsConfirmation"), body: ev.request.question || t("stream.aiNeedsConfirmation"), kind: "warn" });
    if (document.hidden && "Notification" in window && Notification.permission === "granted") {
      new Notification(brandPrefix() + t("stream.needsConfirmation"), { body: ev.request.question || t("stream.aiNeedsConfirmation"), tag: "hermes-confirm" });
    }
  }, activeId));

  stream.on("confirmation_response", scoped((ev) => {
    pendingConfirmations.value = pendingConfirmations.value.filter(
      (r) => r.id !== ev.request_id,
    );
  }, activeId));

  stream.on("clarify_auto", scoped((ev) => {
    const ns = useNotificationStore();
    ns.push({ title: t("stream.autoConfirmed"), body: `${ev.question} → ${ev.choice}`, kind: "info" });
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
    // A cancelled turn is not a "completion" — never fire the success
    // notification for it (the cancel action already gave the user feedback).
    if (ev.status !== "cancelled" && (document.hidden || !activeId.value)) {
      const ns = useNotificationStore();
      const text = (ev.text || m?.content?.text || "").slice(0, 80);
      ns.push({ title: t("stream.aiReplyComplete"), body: text || t("stream.clickToView"), kind: "success", link: `/?c=${activeId.value}` });
      if (document.hidden && "Notification" in window && Notification.permission === "granted") {
        new Notification(brandPrefix() + t("stream.aiReplyComplete"), { body: text || t("stream.clickToView"), tag: "hermes-done" });
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
