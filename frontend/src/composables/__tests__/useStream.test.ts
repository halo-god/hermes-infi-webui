import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useStream } from "@/composables/useStream";

/** Minimal EventSource stand-in capturing instances for manual event firing. */
class EventSourceMock {
  static instances: EventSourceMock[] = [];
  url: string;
  onopen: (() => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  close = vi.fn();
  constructor(url: string) {
    this.url = url;
    EventSourceMock.instances.push(this);
  }
}

/** Minimal WebSocket stand-in; readyState follows the connect lifecycle. */
class WebSocketMock {
  static instances: WebSocketMock[] = [];
  static OPEN = 1;
  static CONNECTING = 0;
  url: string;
  readyState = WebSocketMock.CONNECTING;
  onopen: (() => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  onclose: ((e: { code: number }) => void) | null = null;
  onerror: (() => void) | null = null;
  send = vi.fn();
  close = vi.fn();
  constructor(url: string) {
    this.url = url;
    WebSocketMock.instances.push(this);
  }
  /** Simulates the browser opening the connection: readyState flips first. */
  open() {
    this.readyState = WebSocketMock.OPEN;
    this.onopen?.();
  }
}

const lastES = () => EventSourceMock.instances[EventSourceMock.instances.length - 1];
const lastWS = () => WebSocketMock.instances[WebSocketMock.instances.length - 1];

/** Flush microtasks + the initial connect(), returning the opened ES instance. */
async function openSSE(stream: ReturnType<typeof useStream>, url = "sse://x") {
  const p = stream.openSSE(() => url);
  await vi.advanceTimersByTimeAsync(10);
  const es = lastES();
  expect(es.url).toBe(url);
  return { p, es };
}

async function openWS(stream: ReturnType<typeof useStream>, url = "ws://x") {
  const p = stream.openWS(() => url);
  await vi.advanceTimersByTimeAsync(10);
  const ws = lastWS();
  expect(ws.url).toBe(url);
  return { p, ws };
}

describe("composables/useStream", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    EventSourceMock.instances = [];
    WebSocketMock.instances = [];
    vi.stubGlobal("EventSource", EventSourceMock);
    vi.stubGlobal("WebSocket", WebSocketMock);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  describe("SSE", () => {
    it("connects, fires onopen and resolves", async () => {
      const stream = useStream();
      const { p, es } = await openSSE(stream);
      expect(stream.connected.value).toBe(false);
      es.onopen?.();
      await vi.advanceTimersByTimeAsync(200); // poll interval sees connected
      await expect(p).resolves.toBeUndefined();
      expect(stream.connected.value).toBe(true);
    });

    it("resolves via the timeout even if the socket never opens", async () => {
      const stream = useStream();
      const { p } = await openSSE(stream);
      await vi.advanceTimersByTimeAsync(3200);
      await expect(p).resolves.toBeUndefined();
      expect(stream.connected.value).toBe(false);
    });

    it("dispatches parsed events to typed and wildcard handlers", async () => {
      const stream = useStream();
      const onToken = vi.fn();
      const onAny = vi.fn();
      stream.on("token", onToken);
      stream.onAny(onAny);
      const { es } = await openSSE(stream);
      es.onmessage?.({ data: JSON.stringify({ type: "token", message_id: "m1", delta: "x" }) });
      expect(onToken).toHaveBeenCalledWith(expect.objectContaining({ delta: "x" }));
      expect(onAny).toHaveBeenCalledTimes(1);
    });

    it("ignores non-JSON messages (heartbeats)", async () => {
      const stream = useStream();
      const onAny = vi.fn();
      stream.onAny(onAny);
      const { es } = await openSSE(stream);
      es.onmessage?.({ data: ": keepalive" });
      expect(onAny).not.toHaveBeenCalled();
    });

    it("reconnects with exponential backoff after errors", async () => {
      const stream = useStream();
      await openSSE(stream);
      expect(EventSourceMock.instances).toHaveLength(1);
      lastES().onerror?.();
      expect(stream.connected.value).toBe(false);
      await vi.advanceTimersByTimeAsync(1100); // 1s backoff
      expect(EventSourceMock.instances).toHaveLength(2);
      lastES().onerror?.();
      await vi.advanceTimersByTimeAsync(2100); // 2s backoff
      expect(EventSourceMock.instances).toHaveLength(3);
    });

    it("gives up after 8 consecutive errors and calls onGiveUp", async () => {
      const onGiveUp = vi.fn();
      const stream = useStream(onGiveUp);
      await openSSE(stream);
      for (let i = 0; i < 8; i++) {
        lastES().onerror?.();
        await vi.advanceTimersByTimeAsync(35_000); // covers max 30s backoff
      }
      expect(stream.error.value).toBe("SSE 连接断开");
      expect(stream.connected.value).toBe(false);
      expect(onGiveUp).toHaveBeenCalledTimes(1);
    });

    it("retries when the url factory rejects", async () => {
      const stream = useStream();
      let calls = 0;
      const p = stream.openSSE(() => {
        calls++;
        if (calls === 1) return Promise.reject(new Error("ticket"));
        return "sse://ok";
      });
      await vi.advanceTimersByTimeAsync(1100);
      expect(EventSourceMock.instances).toHaveLength(1);
      expect(lastES().url).toBe("sse://ok");
      expect(calls).toBe(2);
      p.catch(() => {});
    });

    it("close cancels reconnection and clears state", async () => {
      const stream = useStream();
      await openSSE(stream);
      lastES().onerror?.();
      stream.close();
      expect(stream.connected.value).toBe(false);
      expect(stream.error.value).toBeNull();
      await vi.advanceTimersByTimeAsync(10_000);
      expect(EventSourceMock.instances).toHaveLength(1); // no reconnect after close
    });

    it("an in-flight connect is superseded by close (epoch)", async () => {
      const stream = useStream();
      let urlCalled = false;
      const p = stream.openSSE(async () => {
        await new Promise((r) => setTimeout(r, 500));
        urlCalled = true;
        return "sse://late";
      });
      await vi.advanceTimersByTimeAsync(100);
      stream.close();
      await vi.advanceTimersByTimeAsync(1000);
      expect(urlCalled).toBe(true); // url was fetched…
      expect(EventSourceMock.instances).toHaveLength(0); // …but no socket created
      p.catch(() => {});
    });
  });

  describe("WebSocket", () => {
    it("connects, fires onopen, resolves and starts the heartbeat", async () => {
      const stream = useStream();
      const { p, ws } = await openWS(stream);
      ws.open();
      await vi.advanceTimersByTimeAsync(200);
      await expect(p).resolves.toBeUndefined();
      expect(stream.connected.value).toBe(true);
      expect(ws.readyState).toBe(WebSocketMock.OPEN);
      // heartbeat ping after 30s
      await vi.advanceTimersByTimeAsync(30_000);
      expect(ws.send).toHaveBeenCalledWith(JSON.stringify({ type: "ping" }));
    });

    it("dispatches messages but ignores pong", async () => {
      const stream = useStream();
      const onToken = vi.fn();
      stream.on("token", onToken);
      const { ws } = await openWS(stream);
      ws.onmessage?.({ data: JSON.stringify({ type: "token", message_id: "m1", delta: "y" }) });
      expect(onToken).toHaveBeenCalledTimes(1);
      ws.onmessage?.({ data: JSON.stringify({ type: "pong" }) });
      expect(onToken).toHaveBeenCalledTimes(1);
    });

    it("reconnects on abnormal closure but not on a clean 1000", async () => {
      const stream = useStream();
      const { ws } = await openWS(stream);
      ws.onclose?.({ code: 1006 });
      await vi.advanceTimersByTimeAsync(1100);
      expect(WebSocketMock.instances).toHaveLength(2);
      lastWS().onclose?.({ code: 1000 });
      await vi.advanceTimersByTimeAsync(10_000);
      expect(WebSocketMock.instances).toHaveLength(2); // no reconnect after 1000
    });

    it("retries when the url factory rejects", async () => {
      const stream = useStream();
      let calls = 0;
      const p = stream.openWS(() => {
        calls++;
        if (calls === 1) return Promise.reject(new Error("ticket"));
        return "ws://ok";
      });
      await vi.advanceTimersByTimeAsync(1100);
      expect(WebSocketMock.instances).toHaveLength(1);
      expect(lastWS().url).toBe("ws://ok");
      p.catch(() => {});
    });

    it("send returns false when not open", () => {
      const stream = useStream();
      expect(stream.send({ action: "send" })).toBe(false);
    });

    it("send delivers JSON when the socket is open", async () => {
      const stream = useStream();
      const { ws } = await openWS(stream);
      ws.open();
      await vi.advanceTimersByTimeAsync(200);
      expect(stream.send({ action: "send", text: "hi" })).toBe(true);
      expect(ws.send).toHaveBeenCalledWith(JSON.stringify({ action: "send", text: "hi" }));
    });
  });

  describe("event registration", () => {
    it("on returns an unsubscribe function", async () => {
      const stream = useStream();
      const handler = vi.fn();
      const unsubscribe = stream.on("done", handler);
      const { es } = await openSSE(stream);
      es.onmessage?.({ data: JSON.stringify({ type: "done", message_id: "m1", status: "complete" }) });
      expect(handler).toHaveBeenCalledTimes(1);
      unsubscribe();
      es.onmessage?.({ data: JSON.stringify({ type: "done", message_id: "m1", status: "complete" }) });
      expect(handler).toHaveBeenCalledTimes(1);
    });

    it("emit dispatches to all matching handlers", () => {
      const stream = useStream();
      const a = vi.fn();
      const b = vi.fn();
      stream.on("token", a);
      stream.on("token", b);
      stream.emit({ type: "token", message_id: "m1", delta: "z" });
      expect(a).toHaveBeenCalledTimes(1);
      expect(b).toHaveBeenCalledTimes(1);
    });

    it("offAll clears every handler", () => {
      const stream = useStream();
      const handler = vi.fn();
      stream.on("token", handler);
      stream.offAll();
      stream.emit({ type: "token", message_id: "m1", delta: "z" });
      expect(handler).not.toHaveBeenCalled();
    });
  });

  it("close closes the socket and clears state", async () => {
    const stream = useStream();
    const { ws } = await openWS(stream);
    stream.close();
    expect(ws.close).toHaveBeenCalled();
    expect(stream.connected.value).toBe(false);
    expect(stream.error.value).toBeNull();
    await vi.advanceTimersByTimeAsync(60_000);
    expect(ws.send).not.toHaveBeenCalled(); // heartbeat stopped
  });
});
