"""ORM models. Import all here so Alembic autogenerate sees them."""
from app.db.models.agent import Agent  # noqa: F401
from app.db.models.audit import AuditLog  # noqa: F401
from app.db.models.branding import BrandAsset  # noqa: F401
from app.db.models.conversation import (  # noqa: F401
    Conversation,
    ConversationFolder,
    GroupMember,
    Message,
)
from app.db.models.feedback import Feedback  # noqa: F401
from app.db.models.identity import DeptTeamMapping, IdentityProvider  # noqa: F401
from app.db.models.system import SystemSettings  # noqa: F401
from app.db.models.team import (  # noqa: F401
    Project,
    ProjectActivity,
    ProjectDoc,
    ProjectDocVersion,
    ProjectTask,
    Team,
    TeamKnowledge,
    TeamKnowledgeVersion,
    TeamMember,
)
from app.db.models.user import User  # noqa: F401
from app.db.models.workspace import WorkspaceFile  # noqa: F401
from app.db.models.memory import AgentMemory, AgentSkill, MemoryEpisode  # noqa: F401
from app.db.models.scheduled import ScheduledTask  # noqa: F401
from app.db.models.subagent import BackgroundSubagent  # noqa: F401
from app.db.models.skill_evolution import SkillFiring, SkillProposal  # noqa: F401
