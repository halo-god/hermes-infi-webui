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


def summarize_title_sync(user_text: str, reply_text: str) -> str | None:
    """Generate a concise conversation title from the first exchange.

    Synchronous LLM call — must be run off the event loop (asyncio.to_thread).
    Returns None on any failure (not configured, API error, empty output) —
    the caller keeps the truncated-placeholder title in that case.
    """
    # Fallback chain for the cheap-channel credentials: auxiliary_llm_* is
    # the designated slot, but most deployments only configured the
    # skill-evolution LLM (same class of background, non-user-facing work) —
    # without this fallback title generation silently no-ops.
    model = settings.auxiliary_llm_model or settings.skill_evolution_llm_model
    api_key = settings.auxiliary_llm_api_key or settings.skill_evolution_llm_api_key
    api_base = (
        settings.auxiliary_llm_api_base
        or settings.skill_evolution_llm_api_base
        or None
    )
    if not model or not api_key:
        logger.debug("No auxiliary/evolution LLM configured — skipping title summary")
        return None
    try:
        import dspy
    except ImportError:
        logger.warning("dspy not installed — cannot summarise title")
        return None

    user_excerpt = (user_text or "").strip()[:600]
    reply_excerpt = (reply_text or "").strip()[:600]
    if not user_excerpt:
        return None

    class TitleConversation(dspy.Signature):
        """根据对话的首轮交流，生成一个简短的会话标题。
        要求：中文；不超过 16 个字；概括用户的核心诉求或话题；
        不使用引号、书名号或句末标点；不要以"关于"开头。"""
        user_message: str = dspy.InputField(desc="用户的首条消息")
        assistant_reply: str = dspy.InputField(desc="助手的首条回复（可能为空）")
        title: str = dspy.OutputField(desc="不超过 16 字的简短中文标题")

    try:
        lm = dspy.LM(model, api_key=api_key, api_base=api_base)
        predictor = dspy.Predict(TitleConversation)
        with dspy.context(lm=lm):
            result = predictor(
                user_message=user_excerpt,
                assistant_reply=reply_excerpt,
            )
        title = (getattr(result, "title", "") or "").strip()
        if not title:
            return None
        # Hard cleanup: the LLM sometimes ignores the no-quotes rule.
        title = title.strip("\"'“”‘’《》「」。.！!？? \t")
        # Keep it inside the column limit even if the model rambles.
        return title[:40] or None
    except Exception:  # noqa: BLE001 — never crash the caller's turn flow
        logger.warning("Title summarisation LLM call failed", exc_info=True)
        return None
