/**
 * useStream — event-driven SSE/WebSocket composable.
 *
 * Encapsulates the lifecycle of a streaming connection (SSE for single-agent,
 * WebSocket for roundtable) and emits typed events via a simple callback map.
 *
 * Usage:
 *   const stream = useStream()
 *   stream.on('token', (ev) => { ... })
 *   stream.on('done',  (ev) => { ... })
 *   await stream.openSSE(url)
 *   // ...
 *   stream.close()
 */
import { ref } from "vue";
import type { StreamEvent } from "@/types";

export type StreamEventType = StreamEvent["type"];

/** Callback for a specific stream event type. */
export type StreamEventHandler<T extends StreamEvent = StreamEvent> = (ev: T) => void;

/**
 * Composable: creates a managed stream connection.
 * Returns reactive state + lifecycle methods.
 */
export function useStream() {
  const connected = ref(false);
  const error = ref<string | null>(null);

  let es: EventSource | null = null;
  let ws: WebSocket | null = null;
  const handlers = new Map<string, StreamEventHandler[]>();

  /** Register a typed event handler. */
  function on<T extends StreamEventType>(
    type: T,
    handler: StreamEventHandler<Extract<StreamEvent, { type: T }>>,
  ): () => void {
    const list = handlers.get(type) ?? [];
    list.push(handler as StreamEventHandler);
    handlers.set(type, list);
    // Return unsubscribe function
    return () => {
      const arr = handlers.get(type);
      if (arr) {
        const idx = arr.indexOf(handler as StreamEventHandler);
        if (idx !== -1) arr.splice(idx, 1);
      }
    };
  }

  /** Register a wildcard handler that receives ALL events. */
  function onAny(handler: StreamEventHandler): () => void {
    return on("*" as StreamEventType, handler);
  }

  /** Dispatch an event to all registered handlers. */
  function emit(ev: StreamEvent) {
    // Type-specific handlers
    const typed = handlers.get(ev.type);
    if (typed) typed.forEach((fn) => fn(ev));
    // Wildcard handlers
    const wildcard = handlers.get("*");
    if (wildcard) wildcard.forEach((fn) => fn(ev));
  }

  /** Close any open connection. */
  function close() {
    if (es) { es.close(); es = null; }
    if (ws) { ws.close(); ws = null; }
    connected.value = false;
  }

  /** Open an SSE connection and return a promise that resolves when connected. */
  function openSSE(url: string, timeoutMs = 600): Promise<void> {
    close();
    error.value = null;
    es = new EventSource(url);

    es.onmessage = (e) => {
      try {
        emit(JSON.parse(e.data) as StreamEvent);
      } catch { /* heartbeat / non-JSON */ }
    };

    es.onerror = () => {
      error.value = "SSE 连接断开";
      close();
    };

    return new Promise<void>((resolve) => {
      es!.onopen = () => {
        connected.value = true;
        resolve();
      };
      // Fallback timeout in case onopen doesn't fire
      setTimeout(() => {
        connected.value = true;
        resolve();
      }, timeoutMs);
    });
  }

  /** Open a WebSocket connection and return a promise that resolves when connected. */
  function openWS(url: string, timeoutMs = 800): Promise<void> {
    close();
    error.value = null;
    ws = new WebSocket(url);

    ws.onmessage = (e) => {
      try {
        emit(JSON.parse(e.data) as StreamEvent);
      } catch { /* non-JSON */ }
    };

    ws.onclose = () => {
      ws = null;
      connected.value = false;
    };

    ws.onerror = () => {
      error.value = "WebSocket 连接断开";
      close();
    };

    return new Promise<void>((resolve) => {
      ws!.onopen = () => {
        connected.value = true;
        resolve();
      };
      setTimeout(() => {
        connected.value = true;
        resolve();
      }, timeoutMs);
    });
  }

  /** Send data over WebSocket. Only works after openWS(). */
  function send(data: unknown): boolean {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(data));
      return true;
    }
    return false;
  }

  /** Clear all registered handlers. */
  function offAll() {
    handlers.clear();
  }

  return {
    // State
    connected,
    error,
    // Lifecycle
    openSSE,
    openWS,
    close,
    send,
    // Events
    on,
    onAny,
    offAll,
    emit,
  };
}
