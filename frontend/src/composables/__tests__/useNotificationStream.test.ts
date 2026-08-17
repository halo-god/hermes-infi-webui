import { beforeEach, describe, expect, it, vi } from "vitest";

const nsMock = vi.hoisted(() => ({ push: vi.fn(), toast: vi.fn() }));
const mediaMock = vi.hoisted(() => ({ ensure: vi.fn() }));
const chatMock = vi.hoisted(() => ({
  activeId: null as string | null,
  conversations: [] as { id: string; unread?: number; has_mention?: boolean }[],
  markRead: vi.fn(),
}));
const streamMock = vi.hoisted(() => ({
  handlers: new Map<string, ((ev: Record<string, unknown>) => void)[]>(),
  on: vi.fn(),
  openSSE: vi.fn(),
  close: vi.fn(),
  offAll: vi.fn(),
  emit: vi.fn(),
}));

vi.mock("@/api/client", () => ({ mediaTicket: mediaMock }));
vi.mock("@/stores/notifications", () => ({ useNotificationStore: () => nsMock }));
vi.mock("@/stores/chat", () => ({ useChatStore: () => chatMock }));
vi.mock("@/composables/useStream", () => ({
  useStream: () => streamMock,
}));

import { useNotificationStream } from "@/composables/useNotificationStream";

function setupHandlers() {
  // Rebind the real on/emit around the hoisted state (mock factory can't
  // reference the helper functions directly).
  streamMock.handlers.clear();
  streamMock.on.mockImplementation(
    (type: string, fn: (ev: Record<string, unknown>) => void) => {
      const list = streamMock.handlers.get(type) ?? [];
      list.push(fn);
      streamMock.handlers.set(type, list);
      return () => {};
    },
  );
  streamMock.emit.mockImplementation((ev: Record<string, unknown>) => {
    (streamMock.handlers.get(ev.type as string) ?? []).forEach((fn) => fn(ev));
  });
}

function emit(ev: Record<string, unknown>) {
  (streamMock.handlers.get(ev.type as string) ?? []).forEach((fn) => fn(ev));
}

describe("composables/useNotificationStream", () => {
  beforeEach(() => {
    nsMock.push.mockReset();
    mediaMock.ensure.mockReset();
    mediaMock.ensure.mockResolvedValue("ticket");
    chatMock.activeId = null;
    chatMock.conversations = [];
    chatMock.markRead.mockReset();
    chatMock.markRead.mockResolvedValue(undefined);
    streamMock.openSSE.mockReset();
    streamMock.openSSE.mockResolvedValue(undefined);
    streamMock.close.mockReset();
    streamMock.offAll.mockReset();
    setupHandlers();
  });

  it("start opens the /me/stream SSE with a fresh media ticket", async () => {
    const ns = useNotificationStream();
    await ns.start();
    expect(streamMock.openSSE).toHaveBeenCalledTimes(1);
    const factory = streamMock.openSSE.mock.calls[0][0] as () => Promise<string>;
    await expect(factory()).resolves.toContain("/api/v1/me/stream?ticket=ticket");
  });

  it("start is idempotent while running", async () => {
    const ns = useNotificationStream();
    await ns.start();
    await ns.start();
    expect(streamMock.openSSE).toHaveBeenCalledTimes(1);
  });

  it("ignores notify events without a conversation id", async () => {
    const ns = useNotificationStream();
    await ns.start();
    emit({ type: "notify", title: "x", snippet: "y" });
    expect(nsMock.push).not.toHaveBeenCalled();
  });

  it("marks the open conversation read and skips the toast logic", async () => {
    chatMock.activeId = "c1";
    const ns = useNotificationStream();
    await ns.start();
    emit({ type: "notify", conversation_id: "c1", title: "t", snippet: "s" });
    expect(chatMock.markRead).toHaveBeenCalledWith("c1");
    expect(nsMock.push).not.toHaveBeenCalled();
  });

  it("bumps unread and mention flags for a closed conversation", async () => {
    chatMock.conversations = [{ id: "c2" }];
    const ns = useNotificationStream();
    await ns.start();
    emit({ type: "notify", conversation_id: "c2", title: "t", snippet: "s", mention: true });
    expect(chatMock.conversations[0]).toMatchObject({ unread: 1, has_mention: true });
    expect(nsMock.push).toHaveBeenCalledWith(
      expect.objectContaining({ kind: "info", link: "/?c=c2" }),
    );
  });

  it("subagent_nudge pushes a success notification", async () => {
    const ns = useNotificationStream();
    await ns.start();
    emit({ type: "subagent_nudge", subagent_id: "s1", status: "done", conversation_id: "c3" });
    expect(nsMock.push).toHaveBeenCalledWith(
      expect.objectContaining({ title: "后台任务已完成", kind: "success", link: "/?c=c3" }),
    );
  });

  it("subagent_nudge failure pushes a warn notification", async () => {
    const ns = useNotificationStream();
    await ns.start();
    emit({ type: "subagent_nudge", subagent_id: "s1", status: "timeout" });
    expect(nsMock.push).toHaveBeenCalledWith(expect.objectContaining({ title: "后台任务失败", kind: "warn" }));
  });

  it("an openSSE failure resets started so start can be retried", async () => {
    streamMock.openSSE.mockRejectedValueOnce(new Error("down"));
    const ns = useNotificationStream();
    await ns.start();
    await ns.start();
    expect(streamMock.openSSE).toHaveBeenCalledTimes(2);
  });

  it("stop closes and clears the stream", async () => {
    const ns = useNotificationStream();
    await ns.start();
    ns.stop();
    expect(streamMock.close).toHaveBeenCalled();
    expect(streamMock.offAll).toHaveBeenCalled();
    await ns.start(); // restart works after stop
    expect(streamMock.openSSE).toHaveBeenCalledTimes(2);
  });
});
