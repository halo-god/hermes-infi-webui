/** Shared formatting utilities. */

import type { AxiosError } from "axios";

/** Format a timestamp to a compact zh-CN datetime string. */
export function fmtDate(ts: string | number | Date | null | undefined): string {
  if (!ts) return "";
  return new Date(ts).toLocaleString("zh-CN", { hour12: false });
}

/** Format a date to date-only (no time). */
export function fmtDay(ts: string | number | Date | null | undefined): string {
  if (!ts) return "";
  return new Date(ts).toLocaleDateString("zh-CN");
}

/** Format a number with locale separators (e.g. 1,234). */
export function fmtNum(n: number | null | undefined): string {
  if (n == null) return "0";
  return n.toLocaleString();
}

/** Format bytes to human-readable size. */
export function fmtBytes(bytes: number | null | undefined): string {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  let i = 0;
  let size = bytes;
  while (size >= 1024 && i < units.length - 1) {
    size /= 1024;
    i++;
  }
  return `${size.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

/** Extract a human-readable error message from an unknown catch value.
 *  Handles Axios errors (response.data.detail), Error instances, and strings. */
export function extractDetail(e: unknown): string {
  if (!e) return "未知错误";
  // Axios error with detail
  const ax = e as AxiosError<{ detail?: string }>;
  if (ax.response?.data?.detail) return ax.response.data.detail;
  // Standard Error
  if (e instanceof Error) return e.message;
  // String
  if (typeof e === "string") return e;
  return "未知错误";
}
