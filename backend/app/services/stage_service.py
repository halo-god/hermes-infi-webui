"""Stage-based system prompt service: selects the appropriate stage prompt
based on conversation context (requirement gathering → implementation → review).

Inspired by ai-agent-book's `staged-system-prompt` experiment: same Agent
with different system prompts per phase, sharing conversation history.
"""
from __future__ import annotations


def detect_stage(
    message_text: str,
    conversation_history_count: int,
    has_code_in_response: bool = False,
) -> str:
    """Detect the current conversation stage based on heuristics.

    Returns one of: 'requirement' | 'implementation' | 'review'
    - requirement: early in conversation, user is describing what they want
    - implementation: code has been generated, user is iterating
    - review: user is asking to check/verify/test
    """
    text_lower = message_text.lower()

    # Review stage: user asks to check, verify, test, review
    review_keywords = ["检查", "验证", "测试", "审查", "review", "check", "verify", "test", "bug", "问题", "修复"]
    if any(kw in text_lower for kw in review_keywords):
        return "review"

    # Implementation stage: code already generated in conversation
    if has_code_in_response or conversation_history_count > 4:
        return "implementation"

    # Default: requirement gathering (early conversation)
    return "requirement"


def get_stage_prompt(profile_stage_prompts: dict, stage: str) -> str | None:
    """Get the system prompt for a specific stage from the profile's stage_prompts.

    stage_prompts format: {"requirement": "...", "implementation": "...", "review": "..."}
    Falls back to None if no stage-specific prompt is configured.
    """
    if not profile_stage_prompts:
        return None
    return profile_stage_prompts.get(stage)


# Dangerous tool patterns that should require user approval before execution.
# When a tool call matches these patterns, the runner should emit a
# confirmation_request instead of auto-executing.
DANGEROUS_TOOL_PATTERNS = [
    "delete_",
    "drop_",
    "remove_",
    "destroy_",
    "execute_shell",
    "run_command",
    "exec_",
    "truncate_",
    "wipe_",
    "reset_",
    "purge_",
]


def should_approve_tool(tool_name: str) -> bool:
    """Check if a tool call should require user approval.

    Returns True if the tool name matches a dangerous pattern.
    """
    name_lower = tool_name.lower()
    return any(pat in name_lower for pat in DANGEROUS_TOOL_PATTERNS)
