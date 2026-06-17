/**
 * useNotificationStream — single per-user SSE to /me/stream.
 *
 * Carries cross-conversation `notify` events so unread / @-mention badges and
 * toasts update even when the relevant group conversation isn't open. Mirrors
 * the Slack/Discord "user gateway" pattern; reuses the same Stream machinery.
 */
import { useStream } from "@/composables/useStream";
import { mediaTicket } from "@/api/client";
import { useChatStore } from "@/stores/chat";
import { useNotificationStore } from "@/stores/notifications";

const API_BASE = import.meta.env.VITE_API_BASE || "/api/v1";

export function useNotificationStream() {
  const stream = useStream();
  let started = false;

  async function start() {
    if (started) return;
    started = true;
    const chat = useChatStore();
    const ns = useNotificationStore();

    stream.on("notify", (ev) => {
      const cid = ev.conversation_id;
      if (!cid) return;
      // The open conversation stays read; just refresh its server cursor.
      if (chat.activeId === cid) { chat.markRead(cid).catch(() => {}); return; }
      const c = chat.conversations.find((x) => x.id === cid);
      if (c) {
        c.unread = (c.unread || 0) + 1;
        if (ev.mention) c.has_mention = true;
      }
      if (ev.mention) {
        ns.push({
          title: ev.title || "新的提及",
          body: ev.snippet || "有人在群聊中@了你",
          kind: "info",
          link: `/?c=${cid}`,
        });
      }
    });

    try {
      await stream.openSSE(async () => {
        const ticket = await mediaTicket.ensure();
        return `${API_BASE}/me/stream?ticket=${encodeURIComponent(ticket)}`;
      });
    } catch {
      started = false;
    }
  }

  function stop() {
    started = false;
    stream.close();
    stream.offAll();
  }

  return { start, stop };
}
