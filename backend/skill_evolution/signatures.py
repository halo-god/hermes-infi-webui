"""The dspy program GEPA optimizes. The ONLY tunable text is the Predict's
instructions, seeded from AgentSkill.content — this is the literal
mechanism through which "GEPA optimizes a skill" is implemented."""
from __future__ import annotations

import dspy


class SkillReplySignature(dspy.Signature):
    """Placeholder docstring — overwritten per-instance by AgentSkill.content
    via .with_instructions(), never used as-is."""

    trigger_query: str = dspy.InputField(desc="触发该技能的用户消息")
    response: str = dspy.OutputField(desc="AI 依据技能内容给出的回复")


class SkillModule(dspy.Module):
    """Wraps a single dspy.Predict whose instructions ARE the skill's
    content. GEPA mutates self.respond.signature.instructions; nothing else
    in this module is optimizable."""

    def __init__(self, skill_content: str):
        super().__init__()
        self.respond = dspy.Predict(SkillReplySignature.with_instructions(skill_content))

    def forward(self, trigger_query: str):
        return self.respond(trigger_query=trigger_query)


class ProfileReplySignature(dspy.Signature):
    """Placeholder docstring — overwritten per-instance by Profile.system_prompt
    via .with_instructions(), never used as-is."""

    trigger_query: str = dspy.InputField(desc="发给该助手（Profile）的用户消息")
    response: str = dspy.OutputField(desc="AI 依据该助手的人设（system_prompt）给出的回复")


class ProfileModule(dspy.Module):
    """P2-4: same mechanism as SkillModule but the optimizable instructions
    are seeded from Profile.system_prompt instead of AgentSkill.content.
    GEPA mutates self.respond.signature.instructions; nothing else changes."""

    def __init__(self, system_prompt: str):
        super().__init__()
        self.respond = dspy.Predict(ProfileReplySignature.with_instructions(system_prompt))

    def forward(self, trigger_query: str):
        return self.respond(trigger_query=trigger_query)
