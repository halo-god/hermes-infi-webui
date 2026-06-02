/**
 * Shared constants used across the frontend application.
 * Centralises magic strings, default values, and configuration constants.
 */

/** API base path — matches Vite proxy config. */
export const API_BASE = import.meta.env.VITE_API_BASE || "/api/v1";

/** Default agent ID (the built-in Hermes agent). */
export const DEFAULT_AGENT_ID = "hermes";

/** LocalStorage keys. */
export const STORAGE_KEYS = {
  THEME: "hermes.theme",
  TOKEN: "hermes.token",
  REFRESH_TOKEN: "hermes.refresh_token",
  LOCALE: "hermes.locale",
  SIDEBAR_COLLAPSED: "hermes.sidebar.collapsed",
} as const;

/** Stream event types — mirrors backend StreamEvent type union. */
export const STREAM_EVENTS = {
  START: "start",
  TOKEN: "token",
  TOOL_CALL: "tool_call",
  FILE: "file",
  DONE: "done",
  ERROR: "error",
  RT_START: "rt_start",
  RT_TOKEN: "rt_token",
  RT_REPLY_DONE: "rt_reply_done",
  MERGE_START: "merge_start",
  MERGE_TOKEN: "merge_token",
  CONFIRM_REQUEST: "confirmation_request",
  CONFIRM_RESPONSE: "confirmation_response",
} as const;

/** Max message length (backend enforced). */
export const MAX_MESSAGE_LENGTH = 50_000;

/** Default pagination page size. */
export const PAGE_SIZE = 50;

/** SSE/WS connection timeouts (ms). */
export const STREAM_TIMEOUT_SSE = 600;
export const STREAM_TIMEOUT_WS = 800;
