import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import { useNotificationStore } from "@/stores/notifications";

describe("stores/notifications", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("toast pushes a toast and auto-dismisses after 2800ms", () => {
    const ns = useNotificationStore();
    ns.toast("你好");
    expect(ns.toasts).toHaveLength(1);
    expect(ns.toasts[0]).toMatchObject({ message: "你好", kind: "ok" });
    vi.advanceTimersByTime(2900);
    expect(ns.toasts).toHaveLength(0);
  });

  it("toast accepts a custom kind", () => {
    const ns = useNotificationStore();
    ns.toast("错误", "error");
    expect(ns.toasts[0].kind).toBe("error");
  });

  it("dismiss removes only the matching toast id", () => {
    const ns = useNotificationStore();
    ns.toast("a");
    ns.toast("b");
    const [, b] = ns.toasts;
    ns.dismiss(b.id);
    expect(ns.toasts.map((t) => t.message)).toEqual(["a"]);
  });

  it("push unshifts a notification with defaults and a toast", () => {
    const ns = useNotificationStore();
    ns.push({ title: "标题", body: "内容", kind: "success" });
    expect(ns.inbox).toHaveLength(1);
    expect(ns.inbox[0]).toMatchObject({
      title: "标题",
      body: "内容",
      read: false,
      ts: expect.any(String),
    });
    expect(ns.toasts[0]).toMatchObject({ message: "标题", kind: "info" });
  });

  it("push maps error/warn kinds onto the toast", () => {
    const ns = useNotificationStore();
    ns.push({ title: "失败", body: "", kind: "error" });
    expect(ns.toasts[0].kind).toBe("error");
    ns.push({ title: "警告", body: "", kind: "warn" });
    expect(ns.toasts[1].kind).toBe("warn");
  });

  it("markRead / markAllRead toggle the read flag", () => {
    const ns = useNotificationStore();
    ns.push({ title: "a", body: "", kind: "info" });
    ns.push({ title: "b", body: "", kind: "info" });
    ns.markRead(ns.inbox[1].id);
    expect(ns.inbox.map((n) => n.read)).toEqual([false, true]);
    ns.markAllRead();
    expect(ns.inbox.every((n) => n.read)).toBe(true);
  });

  it("remove deletes a notification", () => {
    const ns = useNotificationStore();
    ns.push({ title: "a", body: "", kind: "info" });
    ns.push({ title: "b", body: "", kind: "info" });
    ns.remove(ns.inbox[1].id);
    expect(ns.inbox.map((n) => n.title)).toEqual(["b"]);
  });

  it("unreadCount counts unread notifications", () => {
    const ns = useNotificationStore();
    ns.push({ title: "a", body: "", kind: "info" });
    ns.push({ title: "b", body: "", kind: "info" });
    expect(ns.unreadCount()).toBe(2);
    ns.markRead(ns.inbox[1].id);
    expect(ns.unreadCount()).toBe(1);
  });
});
