"""Per-turn model/tool call collector feeding the session call log table.

ACP sends ``session/update`` events asynchronously; this class aggregates them
into one record per call so the admin 会话日志 console can show a per-call
overview with durations:

- **tool calls**: keyed by ``toolCallId`` when present (fallback: title).
  Status transitions (pending → running → completed) update the same record;
  duration = first-seen → terminal status (completed/failed/error/cancelled).
- **model calls**: one record per ``usage`` event (input/output tokens).
  Duration is approximated as the gap since the previous event, because ACP
  does not report model-call boundaries.

Pure Python (no DB / Redis), so it can be unit-tested in isolation.
"""
from __future__ import annotations

from datetime import datetime, timezone

TERMINAL_STATUSES = {"completed", "failed", "error", "cancelled"}


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


def _ms(start: datetime, end: datetime) -> int:
    return max(0, int((end - start).total_seconds() * 1000))


class CallCollector:
    def __init__(
        self,
        model_name: str | None,
        turn_started_at: datetime | None = None,
        now=None,
    ) -> None:
        self._model_name = model_name
        self._turn_started_at = turn_started_at
        self._now_fn = now or _utcnow
        self._tools: dict[str, dict] = {}   # key -> tool record
        self._title_seq: dict[str, int] = {}  # title -> calls started with it
        self._model_calls: list[dict] = []
        self._last_event_at: datetime | None = turn_started_at

    # ── tool calls ──
    def on_tool_call(
        self, *,
        title: str | None,
        status: str,
        tool_kind: str | None = None,
        tool_call_id: str | None = None,
        now: datetime | None = None,
    ) -> None:
        now = now or self._now_fn()
        # The hermes agent emits tool_call events without a status field —
        # treat them as completed (single-event call, duration unknown → 0).
        status = status or "completed"
        key = tool_call_id or self._key_for_title(title)
        rec = self._tools.get(key)
        if rec is None:
            rec = {
                "key": key, "title": title, "tool_kind": tool_kind,
                "status": status, "started_at": now, "ended_at": None,
                "duration_ms": None,
            }
            self._tools[key] = rec
            if status in TERMINAL_STATUSES:
                # single-event call: already finished when first seen
                rec["ended_at"] = now
                rec["duration_ms"] = _ms(now, now)
        else:
            if title:
                rec["title"] = title
            if tool_kind:
                rec["tool_kind"] = tool_kind
            rec["status"] = status
            if status in TERMINAL_STATUSES:
                rec["ended_at"] = now
                rec["duration_ms"] = _ms(rec["started_at"], now)
        self._last_event_at = now

    def _key_for_title(self, title: str) -> str:
        """Reuse the in-flight record for this title; once terminal, a repeat
        call with the same title starts a new record."""
        for key in reversed(list(self._tools.keys())):
            rec = self._tools[key]
            if rec["title"] == title and rec["status"] not in TERMINAL_STATUSES:
                return key
        seq = self._title_seq.get(title, 0)
        self._title_seq[title] = seq + 1
        return f"title:{title}:{seq}"

    # ── model calls ──
    def on_usage(
        self, *, input_tokens: int = 0, output_tokens: int = 0, now: datetime | None = None,
    ) -> None:
        now = now or self._now_fn()
        start = self._last_event_at or self._turn_started_at
        duration_ms = _ms(start, now) if start is not None else None
        self._model_calls.append({
            "kind": "model",
            "name": self._model_name,
            "tool_kind": None,
            "status": "completed",
            "duration_ms": duration_ms,
            "tokens_in": input_tokens,
            "tokens_out": output_tokens,
            "started_at": start,
            "ended_at": now,
        })
        self._last_event_at = now

    # ── output ──
    def records(self) -> list[dict]:
        out: list[dict] = []
        for rec in self._tools.values():
            out.append({
                "kind": "tool",
                "name": rec["title"],
                "tool_kind": rec.get("tool_kind"),
                "status": rec["status"],
                "duration_ms": rec.get("duration_ms"),
                "tokens_in": None,
                "tokens_out": None,
                "started_at": rec.get("started_at"),
                "ended_at": rec.get("ended_at"),
            })
        out.extend(self._model_calls)
        return out
