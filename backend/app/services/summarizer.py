"""P1-2: LLM summariser for conversation history compression.

Produces a structured summary (decisions / todos / key facts / people) of a
batch of messages, to be cached in conversation_summaries and injected into
the prompt prefix by dispatch — so long chats don't overflow the context window.

Uses the shared auxiliary_llm_* channel (dspy.LM, same pattern as
skill_evolution's _build_task_lm). Synchronous dspy calls are wrapped in
asyncio.to_thread by the caller (the runner worker).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class SummaryResult:
    summary: str
    token_estimate: int


def summarize_sync(messages_text: str) -> SummaryResult | None:
    """Synchronous LLM call — must be run off the event loop (asyncio.to_thread).

    `messages_text` is a pre-formatted transcript (caller handles char budgets
    and field filtering). Returns None on any failure (no key, API error, empty
    output) — the caller treats None as "skip this summarisation".
    """
    if not settings.auxiliary_llm_model or not settings.auxiliary_llm_api_key:
        logger.debug("Auxiliary LLM not configured — skipping summarisation")
        return None
    try:
        import dspy
    except ImportError:
        logger.warning("dspy not installed — cannot summarise")
        return None

    if not messages_text.strip():
        return None

    class CondenseConversation(dspy.Signature):
        """阅读以下对话片段，提取关键信息，输出简洁的结构化中文摘要。
        只保留对后续对话有用的事实，忽略寒暄和无关细节。格式：
        【决策】已确定的事项（每条一行，没有则写'无'）
        【待办】待完成的任务（每条一行，没有则写'无'）
        【关键事实】重要的上下文信息（用户偏好、约束、背景）
        【涉及内容】讨论过的主题要点
        总字数控制在 300 字以内。"""
        conversation_excerpt: str = dspy.InputField(desc="一段对话的逐条记录")
        summary: str = dspy.OutputField(desc="结构化中文摘要，300字以内")

    try:
        lm = dspy.LM(
            settings.auxiliary_llm_model,
            api_key=settings.auxiliary_llm_api_key,
            api_base=settings.auxiliary_llm_api_base or None,
        )
        predictor = dspy.Predict(CondenseConversation)
        with dspy.context(lm=lm):
            result = predictor(
                conversation_excerpt=messages_text,
            )
        summary = (getattr(result, "summary", "") or "").strip()
        if not summary:
            return None
        # Rough token estimate: CJK ≈ 2 chars/token.
        token_estimate = len(summary) // 2
        return SummaryResult(summary=summary, token_estimate=token_estimate)
    except Exception:  # noqa: BLE001 — never crash the summary worker
        logger.warning("Summarisation LLM call failed", exc_info=True)
        return None


def build_messages_text(transcript: list[tuple[str, str]]) -> str:
    """Turn a list of (role, text) pairs into the transcript fed to the LLM.

    `role` is a human label like '用户' / '助手'. The caller has already
    filtered out noise (system msgs, pure tool-call turns) and applied char
    budgets per message.
    """
    lines = [f"{role}：{text}" for role, text in transcript if text and text.strip()]
    return "\n".join(lines)
