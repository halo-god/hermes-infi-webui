import { describe, expect, it } from "vitest";
import {
  API_BASE,
  DEFAULT_AGENT_ID,
  MAX_MESSAGE_LENGTH,
  PAGE_SIZE,
  STORAGE_KEYS,
  STREAM_EVENTS,
  STREAM_TIMEOUT_SSE,
  STREAM_TIMEOUT_WS,
} from "@/shared/constants";

describe("shared/constants", () => {
  it("exposes the API base path", () => {
    expect(API_BASE).toMatch(/^\/api\/v1$/);
  });

  it("default agent is hermes", () => {
    expect(DEFAULT_AGENT_ID).toBe("hermes");
  });

  it("storage keys are stable", () => {
    expect(STORAGE_KEYS.THEME).toBe("hermes.theme");
    expect(STORAGE_KEYS.TOKEN).toBe("hermes.token");
    expect(STORAGE_KEYS.LOCALE).toBe("hermes.locale");
  });

  it("stream event names mirror the backend contract", () => {
    expect(STREAM_EVENTS.START).toBe("start");
    expect(STREAM_EVENTS.TOKEN).toBe("token");
    expect(STREAM_EVENTS.DONE).toBe("done");
    expect(STREAM_EVENTS.RT_START).toBe("rt_start");
    expect(STREAM_EVENTS.CONFIRM_REQUEST).toBe("confirmation_request");
  });

  it("limits and timeouts are set", () => {
    expect(MAX_MESSAGE_LENGTH).toBe(50_000);
    expect(PAGE_SIZE).toBe(50);
    expect(STREAM_TIMEOUT_SSE).toBe(600);
    expect(STREAM_TIMEOUT_WS).toBe(800);
  });
});
